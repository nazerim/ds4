#!/usr/bin/env python3
"""Read-only blade census: where the 424 GiB actually is.

Buckets bytes by store reason, by full-vs-delta, and computes the P2.1
reclaimable set (interior nodes of single-child chains) and the P3.2 target
set (full stores written by evict/cold/shutdown paths that could chain).
"""
import glob
import os
import struct

D = "/Volumes/FireCuda520/ds4-kv-qwen"
FIXED, V2, V3 = 48, 24, 44
REASON = {0: "unknown", 1: "cold", 2: "continued", 3: "turn", 4: "evict", 5: "shutdown"}

rows = []
for p in glob.glob(os.path.join(D, "*.kv")):
    size = os.path.getsize(p)
    with open(p, "rb") as f:
        h = f.read(FIXED)
        if len(h) < FIXED or h[0:3] != b"KVC":
            continue
        ver = h[3]
        reason = REASON.get(h[5], str(h[5]))
        tokens = struct.unpack_from("<I", h, 8)[0]
        payload = struct.unpack_from("<Q", h, 40)[0]
        parent, delta_from = "", 0
        if ver in (2, 3):
            if ver == 3:
                f.seek(FIXED + V2)
                v3 = f.read(44)
                parent = v3[0:40].decode("ascii", "replace").rstrip("\x00")
                delta_from = struct.unpack_from("<I", v3, 40)[0]
        name = os.path.basename(p)[:-3]
        rows.append(dict(name=name, size=size, tokens=tokens, reason=reason,
                         ver=ver, parent=parent, dfrom=delta_from, payload=payload))

total = sum(r["size"] for r in rows)
G = 1024 ** 3
print("files=%d  total=%.1f GiB" % (len(rows), total / G))

print("\nby reason (full = delta_from 0):")
for rsn in ("continued", "turn", "cold", "evict", "shutdown", "unknown"):
    sel = [r for r in rows if r["reason"] == rsn]
    if not sel:
        continue
    full = [r for r in sel if r["dfrom"] == 0]
    dl = [r for r in sel if r["dfrom"] > 0]
    print("  %-10s n=%3d  %7.1f GiB   full=%3d (%6.1f)  delta=%3d (%6.1f)" %
          (rsn, len(sel), sum(r["size"] for r in sel) / G,
           len(full), sum(r["size"] for r in full) / G,
           len(dl), sum(r["size"] for r in dl) / G))

# v2 files have no chain fields at all (predated P1) - they are inherently full.
v2 = [r for r in rows if r["ver"] == 2]
print("\nv2 (pre-P1, no chain metadata) n=%d  %.1f GiB" % (len(v2), sum(r["size"] for r in v2) / G))

# P2.1: interior nodes whose subtree is a single chain.
kids = {}
for r in rows:
    if r["parent"]:
        kids.setdefault(r["parent"], []).append(r["name"])
byname = {r["name"]: r for r in rows}
single = [byname[p] for p, c in kids.items()
          if len(c) == 1 and p in byname and byname[p]["reason"] != "shutdown"]
branch = [byname[p] for p, c in kids.items() if len(c) > 1 and p in byname]
leaves = [r for r in rows if r["name"] not in kids]
print("\nchain topology: parents-with-1-child=%d  branching=%d  leaves=%d" %
      (len(single), len(branch), len(leaves)))
print("P2.1 reclaimable (interior single-child nodes): %.1f GiB in %d files" %
      (sum(r["size"] for r in single) / G, len(single)))
print("  of which >=8 GiB objects: %.1f GiB" %
      (sum(r["size"] for r in single if r["size"] > 8 * G) / G))

# P3.2: full stores that a chain could have shrunk, priced at the measured
# marginal cost of the equivalent delta.
big_full = [r for r in rows if r["dfrom"] == 0 and r["size"] > 3 * G]
print("\nfull stores over 3 GiB: n=%d  %.1f GiB  (P3.2 target set)" %
      (len(big_full), sum(r["size"] for r in big_full) / G))
for r in sorted(big_full, key=lambda r: -r["size"])[:10]:
    print("   %-12s tokens=%7d reason=%-9s %7.2f GiB  ver=%d" %
          (r["name"][:12], r["tokens"], r["reason"], r["size"] / G, r["ver"]))

# marginal cost model from observed deltas, for context
pairs = [(r["tokens"] - r["dfrom"], r["size"]) for r in rows if r["dfrom"] > 0]
if pairs:
    span = sum(a for a, _ in pairs)
    byts = sum(b for _, b in pairs)
    print("\nmeasured delta cost: %.2f MiB per 1k chained tokens (n=%d)" %
          (byts / max(span, 1) * 1024 / G, len(pairs)))
full_at_depth = [(r["tokens"], r["size"]) for r in rows if r["dfrom"] == 0 and r["tokens"] > 100000]
if full_at_depth:
    s = sum(t for t, _ in full_at_depth)
    b = sum(x for _, x in full_at_depth)
    print("measured full cost:  %.2f MiB per 1k tokens at depth (n=%d)" %
          (b / max(s, 1) * 1024 / G, len(full_at_depth)))
