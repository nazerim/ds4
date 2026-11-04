#!/usr/bin/env python3
"""Force the server's generation-rewind route by re-rendering tool calls.

repro_toolcycle_dsml.py appends the assistant message exactly as the server
returned it, so the next request shares the whole stored prefix and the server
never needs to truncate: both A/B arms came back with `generation rewind`
count 0, which is why that run could not separate the fixes.

Real agent traffic does not echo the server's rendering - a client re-serializes
tool arguments (key order, spacing, unicode escapes) before sending them back.
The canonicalized prefix then diverges from the stored one, and the server must
truncate to the common prefix and continue: that is route=truncation, and it is
the route whose edge position (truncate == window end) caused the 22:43:00 live
stall.  This driver perturbs the rendering between turns on purpose.
"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, ".codebase-memory")
from repro_toolcycle_dsml import turn
from repro_dsml_rewind import build_messages, TOOLS


def render_variants(arguments, n):
    """Same visible text, different bytes - without assuming the arguments parse.

    They do not: the canonicalized tool-call text can be cut mid-string by the
    token budget, so json.loads() raised "Unterminated string starting at".  A
    client re-rendering a tool call changes bytes the same way (leading space,
    trailing newline, spacing), and any byte change shortens the common prefix
    the server can reuse, which is what forces route=truncation.
    """
    if n == 0:
        return arguments
    if n == 1:
        return arguments + "\n"
    return " " + arguments


def turn_thinking(port, messages, max_tokens, temperature, seed, gap_threshold):
    """Same request as repro_toolcycle_dsml.turn(), but WITHOUT
    "thinking": {"type": "disabled"}.

    That flag is why every synthetic run of this investigation came back clean:
    with thinking off there is no thinking<->payload mode boundary in the turn,
    so the server never asks for a resample rewind - the route whose window was
    being stranded.  The live stalls were all on `TOOLS DSML_START` / `THINKING`
    turns, so the mode boundary has to be present to be reproducible.
    """
    body = {"model": "ds4", "messages": messages, "tools": TOOLS,
            "tool_choice": "auto", "temperature": temperature,
            "max_tokens": max_tokens, "seed": seed, "stream": True}
    t0 = time.time()
    last = t0
    worst = 0.0
    content = ""
    calls = {}
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "text/event-stream"}, method="POST")
    with urllib.request.urlopen(req, timeout=3600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            now = time.time()
            if now - last > gap_threshold:
                worst = max(worst, now - last)
                print(f"    [t={now - t0:7.1f}s] GAP {now - last:.1f}s", flush=True)
            last = now
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                delta = json.loads(payload)["choices"][0]["delta"]
            except (KeyError, IndexError, ValueError):
                continue
            content += delta.get("content") or ""
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", len(calls)), {
                    "id": "", "type": "function",
                    "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
    msg = {"role": "assistant", "content": content or None}
    if calls:
        msg["tool_calls"] = [calls[k] for k in sorted(calls)]
    return msg, worst, time.time() - t0


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--kb", type=int, default=60)
    ap.add_argument("--turns", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=1200)
    ap.add_argument("--gap", type=float, default=6.0)
    ap.add_argument("--branch", type=int, default=2,
                    help="re-sends of the earliest history (prefix -> truncation)")
    args = ap.parse_args()

    messages = build_messages(args.kb)
    total_worst = 0.0
    branch_base = list(messages)      # the shortest history we ever sent: every
                                      # later request is a strict extension of it,
                                      # so re-sending THIS one makes the incoming
                                      # prompt a strict prefix of the stored
                                      # session -> the server must truncate, which
                                      # is route=truncation, the route that stalled
                                      # at 22:43:00.  Agents do this when they
                                      # branch or retry from an earlier state.
    for n in range(1, args.turns + 1):
        msg, worst, secs = turn_thinking(args.port, messages, args.max_tokens,
                                        0.8, 42 + n, args.gap)
        total_worst = max(total_worst, worst)
        calls = msg.get("tool_calls") or []
        print(f"turn {n}: {secs:.1f}s worst_gap={worst:.1f}s "
              f"tool_calls={len(calls)}", flush=True)
        if not calls:
            print("  no tool call: cycle ended", flush=True)
            break
        # Re-render each call's arguments the way a client would, then echo them
        # back as tool results: the stored prefix and the incoming one now differ.
        for c in calls:
            c["function"]["arguments"] = render_variants(
                c["function"]["arguments"], n % 3)
        messages.append(msg)
        for c in calls:
            messages.append({"role": "tool", "tool_call_id": c["id"],
                             "content": "OK: file written, 0 problems reported."})
        messages.append({"role": "user",
                         "content": "Good. Call write_file again, same file, add "
                                    "a --json output mode and a retry loop; keep "
                                    "it complete, no stubs."})
    if args.branch:
        for i in range(args.branch):
            _, w, secs = turn_thinking(args.port, branch_base, args.max_tokens,
                                       0.8, 7 + i, args.gap)
            total_worst = max(total_worst, w)
            print(f"branch replay {i + 1}: {secs:.1f}s worst_gap={w:.1f}s "
                  f"(re-send of the shortest history -> truncate expected)",
                  flush=True)
    print(f"worst client-side gap across run: {total_worst:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
