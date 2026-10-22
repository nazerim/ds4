#!/usr/bin/env python3
"""Post-tool-turn live-reuse probe against :8002 (R2b repro, 2026-10-08).

Reproduces the production 18:16:02 event: a chat session that rolls warm,
emits a tool call (finish=tool_calls), and whose tool-result follow-up
fails the live reuse probe -> alien routing -> eviction + full re-prefill.

Flow per run:
  1. foreign session turn (occupies the second batched slot) [--two-slot]
  2. warm turn A (normal chat)
  3. warm turn B (rolls on A)
  4. tool turn: list_files elicitation -> expect finish_reason=tool_calls
  5. follow-up: tool result + new user turn (the turn that missed in prod)
  6. follow-up 2: does rolling resume?

Ground truth is the server log's "chat ctx=A..B:N prompt start" lines:
A ~= previous live position => warm reuse; A=0 (or a disk anchor) => miss.
Elicitation prompt/schema/follow-up shape mirror tests/ds4_test.c
(TEST_LIST_FILES_*). Tools ride on every turn, like the production client.

Usage: toolturn_reuse_probe.py [--two-slot] [--repeat N]
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8002"
MODEL = "qwen"

USER_PROMPT = ("Use the list_files tool to list the current directory exactly "
               "once, then report the listed files and stop.")
USER_PROMPT_PROSE = ("Explain in one sentence what a directory listing shows, "
                     "then use the list_files tool to list the current "
                     "directory exactly once, then report the listed files "
                     "and stop.")
TOOL_RESULT_ML = ('{\n  "path": ".",\n  "files": ["README.md", "Makefile", '
                  '"ds4.c", "metal"]\n}')
TOOL = {"type": "function", "function": {
    "name": "list_files",
    "description": "List files in a directory.",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string",
                                           "description": "Directory path to list."}},
                   "required": ["path"]}}}
TOOL_RESULT = '["README.md","Makefile","ds4.c","metal"]'

# edit-style: large arguments (production's failing call was `edit`, gen=702).
WRITE_TOOL = {"type": "function", "function": {
    "name": "write_file",
    "description": "Write content to a file.",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"},
                                  "content": {"type": "string"}},
                   "required": ["path", "content"]}}}
ESSAY = ("The lighthouse keeper trimmed the wick each evening, polished the "
         "Fresnel lens, and logged the passing ships. Storms tested the tower; "
         "calm nights let him read by the lamp. Over the years the light grew "
         "brighter, guiding more vessels home. ")
WRITE_PROMPT = ("Use the write_file tool to write the following text to "
                "/tmp/notes.txt exactly as given, then stop. Do not write "
                "anything else.\n\n" + ESSAY * 6)
SPECIAL = ('def greet(name):\n'
           '    msg = "Hello, " + name + "!"  # inline comment\n'
           '    if name == "world":\n'
           '        return msg.upper()\n'
           '    return msg\n'
           '\n'
           'class Foo:\n'
           '    pass  # TODO: implement\n'
           '\n'
           'Say "hello" and \'goodbye\'.\n'
           'Unicode: café 你好 🌊 naïve résumé\n'
           'Angle brackets: <tag> </tag> <not-a-marker>\n'
           'Percent and braces: 100% {json: "like"} this\n'
           'Tabs:\there\tand\tthere\n')
SPECIAL_PROMPT = ("Use the write_file tool to write the following text to "
                  "/tmp/special.txt exactly as given, then stop. Do not "
                  "write anything else.\n\n" + SPECIAL)
FOREIGN_PROMPT = "What is 2+2? Answer with the number only."
WARM_A = "Say hello in exactly one word."
WARM_B = "Now say goodbye in exactly one word."
AFTER_TOOL = "Summarize the file list in one short sentence."
AFTER_TOOL2 = "And now in French, one short sentence."


ENSURE_ASCII = True


def post_chat(messages, max_tokens, tools=True, label="", think=False):
    body = {"model": MODEL, "messages": messages, "max_tokens": max_tokens,
            "temperature": 0.0, "stream": False, "think": think}
    if tools:
        body["tools"] = [TOOL]
        body["tool_choice"] = "auto"
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=ENSURE_ASCII).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        out = json.loads(r.read().decode())
    wall = time.time() - t0
    ch = out["choices"][0]
    msg = ch["message"]
    u = out.get("usage", {})
    print(f"  {label}: prompt_tok={u.get('prompt_tokens')} "
          f"completion_tok={u.get('completion_tokens')} wall={wall:.1f}s "
          f"finish={ch.get('finish_reason')} "
          f"tool_calls={len(msg.get('tool_calls') or [])}", flush=True)
    return msg


def assistant_msg(msg, reser=False, badargs=False, nocontent=False,
                  trim=False, addnl=False, nullcontent=False):
    m = {"role": "assistant"}
    calls_present = bool(msg.get("tool_calls"))
    if nullcontent and calls_present:
        m["content"] = None
    elif nocontent and calls_present:
        pass
    else:
        content = msg.get("content") or ""
        if trim:
            content = content.rstrip()
        if addnl and content and not content.endswith("\n"):
            content += "\n"
        m["content"] = content
    if msg.get("reasoning_content"):
        m["reasoning_content"] = msg["reasoning_content"]
    calls = msg.get("tool_calls") or []
    if calls:
        c = calls[0]
        fn = c.get("function", {})
        args = fn.get("arguments") or "{}"
        if reser:
            try:
                args = json.dumps(json.loads(args), separators=(",", ":"))
            except ValueError:
                pass
        if badargs:
            # deliberate divergence: inject a space after each colon.  If the
            # server's tool-call text memory replays the remembered DSML by id,
            # the client's arguments serialization must NOT matter.
            args = args.replace('":"', '": "')
        m["tool_calls"] = [{"id": c.get("id"), "type": "function",
                            "function": {"name": fn.get("name"),
                                         "arguments": args}}]
    return m


def post_chat_stream(messages, max_tokens, tools=True, label="", think=False):
    body = {"model": MODEL, "messages": messages, "max_tokens": max_tokens,
            "temperature": 0.0, "stream": True, "think": think}
    if tools:
        body["tools"] = [TOOL]
        body["tool_choice"] = "auto"
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    content, reasoning, calls, finish = [], [], {}, None
    with urllib.request.urlopen(req, timeout=1800) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            ev = json.loads(data)
            ch = ev["choices"][0]
            d = ch.get("delta") or {}
            if d.get("content"):
                content.append(d["content"])
            if d.get("reasoning_content"):
                reasoning.append(d["reasoning_content"])
            for tc in d.get("tool_calls") or []:
                c = calls.setdefault(tc.get("index", 0),
                                     {"id": None, "name": "", "args": ""})
                if tc.get("id"):
                    c["id"] = tc["id"]
                f = tc.get("function") or {}
                if f.get("name"):
                    c["name"] = f["name"]
                if f.get("arguments"):
                    c["args"] += f["arguments"]
            finish = ch.get("finish_reason")
    wall = time.time() - t0
    msg = {"content": "".join(content),
           "reasoning_content": "".join(reasoning)}
    if calls:
        c = calls[min(calls)]
        msg["tool_calls"] = [{"id": c["id"], "type": "function",
                              "function": {"name": c["name"],
                                           "arguments": c["args"] or "{}"}}]
    print(f"  {label}: wall={wall:.1f}s finish={finish} "
          f"content_len={len(msg['content'])} "
          f"reasoning_len={len(msg['reasoning_content'])} "
          f"tool_calls={len(msg.get('tool_calls') or [])}", flush=True)
    return msg


CYCLE_DIRS = ["the current directory", "/tmp", "the /usr directory",
              "the /etc directory", "the home directory", "/var", "/opt",
              "/srv"]


def cycle_prompt(i):
    return (f"Use the list_files tool to list {CYCLE_DIRS[i % len(CYCLE_DIRS)]} "
            f"exactly once, then stop.")


def main():
    global ENSURE_ASCII
    two_slot = "--two-slot" in sys.argv
    stream = "--stream" in sys.argv
    special = "--special" in sys.argv
    trim = "--trim" in sys.argv
    addnl = "--addnl" in sys.argv
    nullcontent = "--nullcontent" in sys.argv
    ENSURE_ASCII = "--raw-utf8" not in sys.argv
    cycles = 6
    if "--cycles" in sys.argv:
        cycles = int(sys.argv[sys.argv.index("--cycles") + 1])
    post = post_chat_stream if stream else post_chat
    if special:
        globals()["TOOL"] = WRITE_TOOL
    print(f"chained tool cycles: {cycles} (stream={stream} two_slot={two_slot} "
          f"special={special} ensure_ascii={ENSURE_ASCII} trim={trim} "
          f"addnl={addnl} nullcontent={nullcontent})", flush=True)
    if two_slot:
        print("-- foreign turn (occupies the other slot):", flush=True)
        post([{"role": "user", "content": FOREIGN_PROMPT}],
             max_tokens=8, tools=False, label="foreign")
    print("-- warm A:", flush=True)
    a = post([{"role": "user", "content": WARM_A}], max_tokens=16,
             label="warmA")
    hist = [{"role": "user", "content": WARM_A},
            assistant_msg(a, trim=trim, addnl=addnl, nullcontent=nullcontent)]
    print("-- warm B (rolls on A):", flush=True)
    b = post(hist + [{"role": "user", "content": WARM_B}], max_tokens=16,
             label="warmB")
    hist += [{"role": "user", "content": WARM_B},
             assistant_msg(b, trim=trim, addnl=addnl, nullcontent=nullcontent)]
    for i in range(cycles):
        print(f"-- cycle {i} tool turn:", flush=True)
        user_msg = SPECIAL_PROMPT if special else cycle_prompt(i)
        t = post(hist + [{"role": "user", "content": user_msg}],
                 max_tokens=512, label=f"toolturn{i}")
        calls = t.get("tool_calls") or []
        if not calls:
            print(f"  cycle {i}: NO TOOL CALL (finish={t.get('finish_reason')}) "
                  f"— stopping chain", flush=True)
            return
        call = calls[0]
        fn = call.get("function", {})
        print(f"  cycle {i} tool_call: id={call.get('id')} "
              f"name={fn.get('name')}", flush=True)
        followup_user = SPECIAL_PROMPT if special else cycle_prompt(i + 1)
        result = "File written." if special else TOOL_RESULT
        hist += [{"role": "user", "content": user_msg},
                 assistant_msg(t, trim=trim, addnl=addnl,
                                nullcontent=nullcontent),
                 {"role": "tool", "tool_call_id": call.get("id"),
                  "content": result},
                 {"role": "user", "content": followup_user}]
        # the next loop iteration's post() IS the follow-up that prod missed on
    print("-- final normal turn:", flush=True)
    f = post(hist + [{"role": "user", "content": AFTER_TOOL}], max_tokens=64,
             label="final")
    print("done", flush=True)


if __name__ == "__main__":
    main()
