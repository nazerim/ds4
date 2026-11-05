#!/usr/bin/env python3
"""v5 corpus builder: extract REAL agent conversations from the local
session store as replayable message-lists. Privacy: output to
/tmp/v5-corpus (perms 0700), conversation ids masked, contents never
leave the machine, never committed, delete after the campaign.

Shape per checkpoint: a PREFIX of messages ending at a user boundary
plus ONE synthetic 'Continue.' user message, so BOTH arms see
byte-identical prompts and regenerate the next assistant turn (real
tool-shaped traffic: recorded tool calls become assistant tool_calls +
tool-role results; a fixed 3-tool schema is attached so requests carry
tool affordance exactly like the live agent traffic that DSpark
drafts)."""
import json
import os
import sqlite3
import statistics
import sys

DB = '/Users/naz/.opencode2/data/opencode/opencode.db'
OUT = '/tmp/v5-corpus'
MIN_BYTES = 500_000          # candidate sessions
MAX_CHECKS = 5                 # checkpoints per conversation
TARGET_KTOKENS = 32768         # prefix prompt_tokens target (~4 chars/tok)
MAX_MSG_CHARS = 12_000         # clamp huge tool outputs (both arms equal)

TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "shell", "description": "Run a shell command and return output",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                       "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read", "description": "Read a file from the workspace",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "edit", "description": "Apply a structured edit to a file",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"},
                                      "diff": {"type": "string"}},
                       "required": ["path", "diff"]}}}]


def build_messages(sid, cur):
    msgs = []
    rows = cur.execute(
        "select seq, type, data from session_message where session_id=? "
        "and type in ('user','assistant') order by seq", (sid,)).fetchall()
    for seq, typ, data in rows:
        try:
            m = json.loads(data)
        except json.JSONDecodeError:
            continue
        role = m.get('role') or typ
        if role == 'user':
            txt = m.get('text') or ''
            if txt.strip():
                msgs.append({"role": "user", "content": txt[:MAX_MSG_CHARS]})
        elif role == 'assistant':
            content, calls = [], []
            for blk in (m.get('content') or []):
                t = blk.get('type')
                if t == 'text' and blk.get('text', '').strip():
                    content.append(blk['text'][:MAX_MSG_CHARS])
                elif t == 'tool':
                    name = blk.get('name') or 'shell'
                    state = blk.get('state') or {}
                    args = state.get('input') or {}
                    call_id = (blk.get('callID') or blk.get('call_id')
                               or f"call_{seq}_{len(calls)}")
                    argstr = json.dumps(args)[:MAX_MSG_CHARS]
                    if name not in {t['function']['name'] for t in
                                    TOOLS_SCHEMA}:
                        name = 'shell'
                        argstr = json.dumps({"command": str(args)[:500]})
                    calls.append({"id": call_id, "type": "function",
                                  "function": {"name": name,
                                              "arguments": argstr}})
                    out = state.get('output') or state.get('result') or ''
                    out = (out if isinstance(out, str)
                           else json.dumps(out))[:MAX_MSG_CHARS]
                    msgs.append({"role": "tool",
                                 "tool_call_id": call_id,
                                 "content": out or "(no output)"})
            if content or calls:
                a = {"role": "assistant",
                     "content": "\n".join(content) if content else ""}
                if calls:
                    a["tool_calls"] = calls
                msgs.append(a)
    return msgs


def checkpoints(msgs):
    """v5.2 DEPTH LADDER: up to 4 user-boundary prefixes nearest
    dump-char targets 90k/180k/320k/520k (measured tok/char ranges
    ~0.24-1.04 across content types, so the true 32-120k-token band is
    found by MEASURING prompt_tokens in the informal cycle, then
    selecting per-conversation rungs that landed 32768-120000 - v5.1
    finding #1: static estimation cannot hit the band). Dedup 20%.
    The ladder prefixes are byte-stable across boots/conversations, so
    informal runs warm the scored cycle via disk-KV."""
    idx = [i for i, m in enumerate(msgs)
           if m['role'] == 'user' and i + 1 < len(msgs)][1:]
    if not idx:
        return []
    def size(i):
        return sum(len(json.dumps(m)) for m in msgs[:i])
    picks = []
    for target in (90_000, 180_000, 320_000):
        cand = min(idx, key=lambda i: abs(size(i) - target))
        if size(cand) < 40_000:
            continue
        if any(abs(size(cand) - size(p)) < 0.2 * target for p in picks):
            continue
        picks.append(cand)
    return [msgs[:i] + [{"role": "user", "content": "Continue."}]
            for i in sorted(picks)]


def main():
    os.makedirs(OUT, mode=0o700, exist_ok=True)
    con = sqlite3.connect(DB)
    cur = con.cursor()
    sess = [r[0] for r in cur.execute(
        "select session_id, sum(length(data)) s from session_message "
        "group by session_id having s > ? order by s desc limit 28",
        (MIN_BYTES,))]
    EXCLUDE = {'ses_e5e30fb53ffe70hjZIfqugG4Qk',
               'ses_e5ac25e9bffeozfKZiXcJyZj7p'}   # campaign's own lanes
    SECRET_MARKS = ('DS4_API_KEY=', 'github_pat_', 'ghp_', '-----BEGIN')
    made = 0
    for n, sid in enumerate(sess):
        if sid in EXCLUDE:
            continue
        msgs = build_messages(sid, cur)
        if not msgs:
            continue
        cps = checkpoints(msgs)
        if not cps:
            continue
        blob = json.dumps(cps)
        if any(k in blob for k in SECRET_MARKS):
            print(f'skip conv (secret marker): masked id'); continue
        conv = {"conversation": n, "tools": TOOLS_SCHEMA,
                "checkpoints": cps}
        path = f"{OUT}/conv_{n:02d}.json"
        with open(path, 'w') as f:
            json.dump(conv, f)
        os.chmod(path, 0o600)
        sizes = [sum(len(m["content"]) if isinstance(m.get("content"), str)
                     else 0 for m in cp) for cp in cps]
        print(f"conv_{n:02d}: {len(cps)} checkpoints, prefix chars "
              f"{sizes} (session masked)")
        made += 1
    print(f"BUILT {made} conversations -> {OUT}")
    return 0 if made >= 8 else 1


if __name__ == '__main__':
    sys.exit(main())
