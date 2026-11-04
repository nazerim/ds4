#!/usr/bin/env python3
"""Two-turn tool-cycle driver for the DSML rewind stall (PLAN-DSML-REWIND M0).

The single-turn repro does not stall; real agent traffic does.  The difference
is the tool cycle: turn 1 commits a tool call (which canonicalizes the tool
checkpoint and re-syncs the session), then turn 2 continues from that rewritten
frontier and must cross the greedy<->payload mode line again.  That is when the
verify window can be stale.  This driver replays exactly that shape and reports
inter-chunk gaps so they can be lined up with the engine log.
"""
import argparse
import json
import sys
import time
import urllib.request

from repro_dsml_rewind import build_messages, TOOLS


def post(port, body, stream):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions", data=data,
        headers={"Content-Type": "application/json",
                 "Accept": "text/event-stream" if stream else "application/json"},
        method="POST")
    return urllib.request.urlopen(req, timeout=3600)


def turn(port, messages, max_tokens, temperature, seed, stream, gap_threshold):
    body = {"model": "ds4", "messages": messages, "tools": TOOLS,
            "tool_choice": "auto", "temperature": temperature,
            "max_tokens": max_tokens, "seed": seed, "stream": stream,
            "thinking": {"type": "disabled"}}
    t0 = time.time()
    last = t0
    worst = 0.0
    worst_at = 0.0
    content = ""
    calls = {}
    with post(port, body, stream) as resp:
        if not stream:
            evt = json.loads(resp.read().decode())
            msg = evt["choices"][0]["message"]
            return msg, 0.0, time.time() - t0
        for raw in resp:
            now = time.time()
            gap = now - last
            last = now
            if gap > worst:
                worst, worst_at = gap, now - t0
            if gap > gap_threshold:
                print(f"    [t={now - t0:7.1f}s] GAP {gap:.1f}s", flush=True)
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            for ch in obj.get("choices") or []:
                d = ch.get("delta") or {}
                content += d.get("content") or ""
                for c in d.get("tool_calls") or []:
                    idx = c.get("index", 0)
                    slot = calls.setdefault(idx, {"id": None, "name": None,
                                                  "arguments": ""})
                    if c.get("id"):
                        slot["id"] = c["id"]
                    fn = c.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    slot["arguments"] += (fn.get("arguments") or "")
    msg = {"role": "assistant", "content": content or None}
    if calls:
        msg["tool_calls"] = [
            {"id": v["id"] or f"call_{i}", "type": "function",
             "function": {"name": v["name"] or "write_file",
                          "arguments": v["arguments"]}}
            for i, v in sorted(calls.items())]
    return msg, worst, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--kb", type=int, default=350)
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--gap", type=float, default=8.0)
    args = ap.parse_args()

    messages = build_messages(args.kb)
    for n in range(1, args.turns + 1):
        msg, worst, secs = turn(args.port, messages, args.max_tokens,
                                0.8, 42 + n, True, args.gap)
        calls = msg.get("tool_calls") or []
        print(f"turn {n}: {secs:.1f}s worst_gap={worst:.1f}s "
              f"tool_calls={len(calls)} content={len(msg.get('content') or '')} "
              f"args={sum(len(c['function']['arguments']) for c in calls)} chars",
              flush=True)
        messages.append(msg)
        if not calls:
            print("  no tool call: cycle ended", flush=True)
            break
        for c in calls:
            messages.append({"role": "tool", "tool_call_id": c["id"],
                             "content": "OK: file written, 0 problems reported."})
        messages.append({"role": "user",
                         "content": "Good. Now call write_file again with the "
                                    "same file rewritten to add a --json output "
                                    "mode and a retry loop; keep it complete, no "
                                    "stubs."})
    return 0


if __name__ == "__main__":
    sys.exit(main())
