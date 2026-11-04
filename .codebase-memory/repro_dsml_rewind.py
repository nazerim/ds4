#!/usr/bin/env python3
"""Deterministic repro for the EXACT+DSpark DSML rewind full-rebuild stall
(PLAN-DSML-REWIND M0/M1 acceptance harness).

Builds a large message history + one agent-like turn that must emit a long
tool-call argument block at temperature > 0, streams the response, and reports
every inter-chunk gap over --gap seconds.  A multi-minute gap inside the
tool-call block, followed by completion, is the stall; with the M0 patch the
engine log carries a "generation rewind rebuild" line at the same instant.

Nothing here writes to the training corpus: it talks to a private instance on
its own port with DS4_TRAIN_DUMP unset.

Usage:
  python3 repro_dsml_rewind.py --port 8011 --kb 400 --max-tokens 900
"""
import argparse
import json
import sys
import time
import urllib.request

FILLER_TURN = (
    "Reviewing the deployment notes for the {svc} service: the retry budget is "
    "{n} attempts with exponential backoff, the connection pool is sized to the "
    "worker count, and the health probe reads from the readiness endpoint rather "
    "than the liveness one. We keep the migration in its own transaction so a "
    "partial apply cannot leave the schema half-upgraded, and every write path "
    "takes a lease so two replicas never fight over the same queue partition. "
    "The dashboards are wired to the same trace ids the ingest layer emits, which "
    "is what made the last incident review short: we could see the tail latency "
    "come from the cold cache rather than from the database itself. "
)


def build_messages(target_kb):
    messages = [
        {"role": "system",
         "content": "You are a careful engineering assistant. Use the provided "
                    "tools when the user asks you to write or run files."},
    ]
    approx = 0
    svc = 0
    while approx < target_kb * 1024:
        svc += 1
        text = FILLER_TURN.format(svc=f"svc-{svc}", n=(svc % 5) + 2) * 4
        messages.append({"role": "user",
                         "content": f"Notes batch {svc}: summarise the risks."})
        messages.append({"role": "assistant", "content": text})
        approx += len(text)
    messages.append({
        "role": "user",
        "content": "Skip any discussion and call the write_file tool right now. "
                   "Create `tools/cache_probe.py` as a COMPLETE implementation of "
                   "at least 220 lines: module docstring, imports, a Client class "
                   "with socket connect/send/recv helpers, a key generator, a "
                   "latency recorder with percentile math, a table renderer, an "
                   "argparse main(), and three small test helpers at the bottom. "
                   "Put the entire source in the tool call arguments; do not use "
                   "stubs, placeholders, or elisions.",
    })
    return messages


TOOLS = [{
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write a UTF-8 text file to disk, creating parents.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "destination path"},
                "content": {"type": "string",
                            "description": "full file contents"},
            },
            "required": ["path", "content"],
        },
    },
}]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--kb", type=int, default=400, help="history size in KB")
    ap.add_argument("--max-tokens", type=int, default=900)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--gap", type=float, default=5.0, help="stall threshold (s)")
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument("--thinking", action="store_true")
    ap.add_argument("--no-thinking", action="store_true")
    args = ap.parse_args()

    messages = build_messages(args.kb)
    body = {
        "model": "ds4",
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "seed": args.seed,
        "stream": True,
    }
    body["thinking"] = {"type": "disabled"} if args.no_thinking else {"type": "enabled"}
    payload = json.dumps(body).encode()

    url = f"http://{args.host}:{args.port}/v1/chat/completions"
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json",
                 "Accept": "text/event-stream"},
        method="POST")

    t_start = time.time()
    with urllib.request.urlopen(req, timeout=args.timeout) as resp:
        t_open = time.time()
        print(f"[t={t_open - t_start:7.2f}s] stream open ({args.kb} KB history)",
              flush=True)
        last = t_open
        chars = 0
        tool_chars = 0
        in_tool = False
        worst_gap = 0.0
        worst_gap_at = 0.0
        worst_gap_ctx = ""
        for raw in resp:
            now = time.time()
            gap = now - last
            last = now
            line = raw.decode("utf-8", "replace").strip()
            if gap > args.gap:
                print(f"[t={now - t_start:7.2f}s] GAP {gap:.1f}s "
                      f"in_tool={int(in_tool)} after {chars} chars: "
                      f"...{worst_gap_ctx[-90:]}", flush=True)
                if gap > worst_gap:
                    worst_gap, worst_gap_at = gap, now - t_start
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                evt = json.loads(data)
            except json.JSONDecodeError:
                continue
            for choice in evt.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content") or ""
                calls = delta.get("tool_calls") or []
                for call in calls:
                    arg = (call.get("function") or {}).get("arguments") or ""
                    piece += arg
                if piece:
                    chars += len(piece)
                    if "<｜tool" in piece or "tool_call" in piece or calls:
                        in_tool = True
                    if calls or in_tool:
                        tool_chars += len(piece)
                    worst_gap_ctx += piece
                    worst_gap_ctx = worst_gap_ctx[-400:]
    t_end = time.time()
    print(f"[t={t_end - t_start:7.2f}s] stream done: {chars} chars "
          f"({tool_chars} in tool payload), worst gap {worst_gap:.1f}s "
          f"at t={worst_gap_at:.1f}s", flush=True)
    return 0 if worst_gap >= args.gap else 2


if __name__ == "__main__":
    sys.exit(main())
