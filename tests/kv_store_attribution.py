#!/usr/bin/env python3
"""Attribute every KV store to the conversation it belongs to, using the nearest
preceding 'chat ctx=' line. Answers: which conversation stopped laddering rungs,
and did any conversation grow deep without storing?  Reads logs only (no blade)."""
import re, sys

store_re = re.compile(r"^(\d{4} \d{2}:\d{2}:\d{2}).*kv cache stored tokens=(\d+) trimmed=(\d+) "
                      r"reason=(\w+) key=(\S+) sha=([0-9a-f]{8})(?: delta=(\d+)\.\.(\d+))?")
ctx_re = re.compile(r"^(\d{4} \d{2}:\d{2}:\d{2}).*chat ctx=(\d+)\.\.(\d+):(\d+)")
hit_re = re.compile(r"^(\d{4} \d{2}:\d{2}:\d{2}).*kv cache hit text tokens=(\d+)")

cur_ctx = None
events = []
for path in sys.argv[1:]:
    with open(path, "r", errors="replace") as f:
        for line in f:
            m = ctx_re.search(line)
            if m:
                cur_ctx = (int(m.group(2)), int(m.group(3)))
                continue
            m = store_re.search(line)
            if m:
                ts = m.group(1)
                tok = int(m.group(2))
                reason = m.group(4)
                sha = m.group(6)
                d0 = int(m.group(7)) if m.group(7) else 0
                d1 = int(m.group(8)) if m.group(8) else tok
                events.append((ts, tok, reason, sha, d0, d1, cur_ctx))
                continue
            m = hit_re.search(line)
            if m:
                events.append((m.group(1), int(m.group(2)), "HIT", "-", 0, 0, cur_ctx))

print("stores+hits parsed:", len(events))
print("\n%-12s %7s %-9s %-9s %-16s %s" % ("time", "tokens", "reason", "sha", "delta", "ctx of conversation"))
for ts, tok, reason, sha, d0, d1, ctx in events:
    cs = "%d..%d" % ctx if ctx else "-"
    print("%-12s %7d %-9s %-9s %-16s %s" %
          (ts, tok, reason, sha, ("%d..%d" % (d0, d1)) if d0 else "-", cs))

# Which conversations (by ctx start) stored rungs, and how far each ladder went?
print("\nper-conversation ladder summary (keyed by ctx start of first sighting):")
by = {}
for ts, tok, reason, sha, d0, d1, ctx in events:
    if reason == "HIT" or ctx is None:
        continue
    by.setdefault(ctx[0], []).append((ts, tok, reason))
for start in sorted(by):
    v = by[start]
    toks = [t for _, t, _ in v]
    print("  ctx_start=%-7d n_stores=%-3d min=%-7d max=%-7d  %s .. %s" %
          (start, len(v), min(toks), max(toks), v[0][0], v[-1][0]))
