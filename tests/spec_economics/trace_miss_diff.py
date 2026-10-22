#!/usr/bin/env python3
"""Analyze a ds4 --trace file: per-request cache decisions + first-miss diff.

Usage: trace_miss_diff.py <trace-file> [--last N] [--diff]

Prints a table of the last N requests: ts, prompt_tokens, live_tokens_before,
live_prompt_common, tool_replay, cache_source, cached_tokens. With --diff, for
the first request whose live_prompt_common < live_tokens_before (a live miss),
byte-diffs its rendered prompt against the previous request's rendered prompt
and prints the divergence point with context.
"""
import re
import sys


def sections(path):
    cur = None
    with open(path, "r", errors="replace") as f:
        for line in f:
            m = re.match(r"===== request (\d+) (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)"
                         r" =====", line)
            if m:
                if cur:
                    yield cur
                cur = {"id": m.group(1), "ts": m.group(2), "text": ""}
                continue
            if cur is not None:
                cur["text"] += line
    if cur:
        yield cur


def field(sec, name):
    m = re.search(rf"^{name}: (.*)$", sec["text"], re.M)
    return m.group(1).strip() if m else ""


def rendered_prompt(sec):
    parts = sec["text"].split("--- rendered prompt ---\n", 1)
    return parts[1] if len(parts) == 2 else None


def main():
    path = sys.argv[1]
    last = 20
    do_diff = "--diff" in sys.argv
    if "--last" in sys.argv:
        last = int(sys.argv[sys.argv.index("--last") + 1])
    secs = list(sections(path))
    rows = []
    for s in secs:
        rows.append({
            "id": s["id"], "ts": s["ts"],
            "prompt": field(s, "prompt_tokens"),
            "live": field(s, "live_tokens_before"),
            "common": field(s, "live_prompt_common"),
            "replay": field(s, "tool_replay"),
            "source": field(s, "cache_source"),
            "cached": field(s, "cached_tokens"),
            "sec": s,
        })
    print(f"{len(rows)} requests in trace; last {min(last, len(rows))}:")
    print(f"{'id':>4} {'time':<8} {'prompt':>7} {'live':>7} {'common':>7} "
          f"{'cached':>7}  source / tool_replay")
    for r in rows[-last:]:
        print(f"{r['id']:>4} {r['ts'][11:]:<8} {r['prompt']:>7} {r['live']:>7} "
              f"{r['common']:>7} {r['cached']:>7}  {r['source']} / "
              f"{r['replay']}")
    if not do_diff or not rows:
        return
    miss = None
    prev = None
    for r in rows:
        if (r["live"].isdigit() and r["common"].isdigit()
                and int(r["live"]) > 0 and int(r["common"]) < int(r["live"])):
            miss = r
            break
        prev = r
    if not miss:
        print("no live miss found in trace")
        return
    print(f"\nfirst live miss: request {miss['id']} at {miss['ts']}: "
          f"live={miss['live']} common={miss['common']} "
          f"(divergence {int(miss['live']) - int(miss['common'])} tokens "
          f"before live end)")
    a = rendered_prompt(miss["sec"])
    b = rendered_prompt(prev["sec"]) if prev else None
    if a is None or b is None:
        print("rendered prompt missing in trace section")
        return
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    print(f"rendered-prompt byte divergence at offset {i} "
          f"(prev request {prev['id']}, miss request {miss['id']}):")
    lo = max(0, i - 200)
    print(f"  ...prev  [{lo}:{i+200}]: {b[lo:i+200]!r}")
    print(f"  ...miss  [{lo}:{i+200}]: {a[lo:i+200]!r}")


if __name__ == "__main__":
    main()
