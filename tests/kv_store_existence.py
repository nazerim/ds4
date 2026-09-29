#!/usr/bin/env python3
"""For every store the OLD process logged in this conversation's depth band,
check whether the object still exists on disk. Distinguishes
'never stored' from 'stored then deleted/evicted'."""
import glob, os, re, sys

D = "/Volumes/FireCuda520/ds4-kv-qwen"
LOGS = sys.argv[1:]
pat = re.compile(r"^(\d{4} \d{2}:\d{2}:\d{2}).*kv cache stored tokens=(\d+) .*reason=(\w+) "
                 r"key=([a-z-]+) sha=([0-9a-f]{8})")

on_disk = {os.path.basename(p)[:12] for p in glob.glob(os.path.join(D, "*.kv"))}
rows = []
for lg in LOGS:
    with open(lg, "r", errors="replace") as f:
        for line in f:
            m = pat.search(line)
            if not m:
                continue
            ts, tok, rsn, key, sha = m.group(1), int(m.group(2)), m.group(3), m.group(4), m.group(5)
            rows.append((ts, tok, rsn, key, sha))

print("total logged stores            :", len(rows))
band = [r for r in rows if 49152 <= r[1] <= 140000]
print("stores in depth band 49k..140k :", len(band))
missing = [r for r in band if not any(s.startswith(r[4]) for s in on_disk)]
present = [r for r in band if any(s.startswith(r[4]) for s in on_disk)]
print("  still on disk                :", len(present))
print("  GONE (logged, no file)       :", len(missing))

print("\nGONE objects, most recent first:")
for ts, tok, rsn, key, sha in sorted(missing, reverse=True)[:18]:
    print("   %s tokens=%-7d %-9s %-12s sha=%s" % (ts, tok, rsn, key, sha))

print("\nstores at exactly the rungs this conversation needed, whole day:")
for ts, tok, rsn, key, sha in sorted(band, key=lambda r: (r[1], r[0])):
    if tok in (49152, 65536, 81920, 98304, 114688, 131072):
        state = "present" if any(s.startswith(sha) for s in on_disk) else "GONE   "
        print("   %s tokens=%-7d %-9s %-12s sha=%s  %s" % (ts, tok, rsn, key, sha, state))
