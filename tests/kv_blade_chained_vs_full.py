#!/usr/bin/env python3
"""v3-only census: what the steady state actually costs, and what P2.1/P3.2
would change going forward (as opposed to legacy v2 bulk that both supersede)."""
import glob
import os
import struct

D = "/Volumes/FireCuda520/ds4-kv-qwen"
FIXED, V2, V3 = 48, 24, 44
# Authoritative map: see tests/kv_reason.py (mirrors ds4_kvstore.h). The hand-written
# map this file used to carry was shifted from value 3 upward and invented a "turn"
# reason, so every reason-based population below was mislabelled.
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kv_reason import REASON
G = 1024 ** 3

rows = []
for p in glob.glob(os.path.join(D, "*.kv")):
    with open(p, "rb") as f:
        h = f.read(FIXED)
        if len(h) < FIXED or h[0:3] != b"KVC":
            continue
        ver = h[3]
        reason = REASON.get(h[5], "code" + str(h[5]))
        tokens = struct.unpack_from("<I", h, 8)[0]
        payload = struct.unpack_from("<Q", h, 40)[0]
        parent, dfrom = "", 0
        if ver == 3:
            f.seek(FIXED + V2)
            v3 = f.read(44)
            parent = v3[0:40].decode("ascii", "replace").rstrip("\x00")
            dfrom = struct.unpack_from("<I", v3, 40)[0]
        rows.append(dict(name=os.path.basename(p)[:-3], size=os.path.getsize(p),
                         tokens=tokens, reason=reason, ver=ver, parent=parent,
                         dfrom=dfrom, payload=payload))

v2 = [r for r in rows if r["ver"] == 2]
v3 = [r for r in rows if r["ver"] == 3]
# Header version is NOT an age signal: the current binary writes v2 for a full
# store (no verified ancestor) and v3 for a delta. Reading v2 as "legacy" once
# produced a wrong "90% of the blade is pre-P1" claim.
print("v2 = full store    : n=%3d  %7.1f GiB   (no parent: whole-session payload)" % (len(v2), sum(r["size"] for r in v2) / G))
print("v3 = chained delta : n=%3d  %7.1f GiB   (rows [parent..N) only)" % (len(v3), sum(r["size"] for r in v3) / G))
print("bytes per checkpoint: full %.2f GiB avg   chained %.2f GiB avg   ratio %.1fx" %
      (sum(r["size"] for r in v2) / G / len(v2), sum(r["size"] for r in v3) / G / len(v3),
       (sum(r["size"] for r in v2) / len(v2)) / (sum(r["size"] for r in v3) / len(v3))))

print("\nv3 breakdown by reason:")
for rsn in sorted({r["reason"] for r in v3}):
    sel = [r for r in v3 if r["reason"] == rsn]
    full = [r for r in sel if r["dfrom"] == 0]
    ch = [r for r in sel if r["dfrom"] > 0]
    print("  %-10s n=%3d %7.1f GiB | chained %3d (%5.1f)  full %3d (%6.1f)" %
          (rsn, len(sel), sum(r["size"] for r in sel) / G,
           len(ch), sum(r["size"] for r in ch) / G,
           len(full), sum(r["size"] for r in full) / G))

# P3.2 target: v3 full stores whose reason could chain (cold/evict/shutdown).
t32 = [r for r in v3 if r["dfrom"] == 0 and r["reason"] in
       ("cold", "evict", "shutdown", "agent-system", "agent-session")]
# price them as chains: same tokens span from a plausible anchor = marginal rows
marg = [r for r in v3 if r["dfrom"] > 0 and (r["tokens"] - r["dfrom"]) > 0]
per_tok = sum(r["size"] for r in marg) / sum(r["tokens"] - r["dfrom"] for r in marg)
floor = min(r["size"] for r in marg) if marg else 0
avg_chained = (sum(r["size"] for r in marg) / len(marg)) if marg else 0
est = len(t32) * avg_chained  # price each at the observed average chained store
print("\nP3.2 target set (v3 full stores that could chain): n=%d  %.1f GiB now" %
      (len(t32), sum(r["size"] for r in t32) / G))
print("   observed average chained store: %.2f GiB -> priced at %.1f GiB" %
      (avg_chained / G, est / G))

# P2.1: interior single-child nodes within v3.
kids = {}
for r in v3:
    if r["parent"]:
        kids.setdefault(r["parent"], []).append(r["name"])
byname = {r["name"]: r for r in v3}
interior = [byname[p] for p, c in kids.items() if len(c) == 1 and p in byname]
depth = 0
for p, c in kids.items():
    pass
chain_len = 0
leaf = [r for r in v3 if r["name"] not in kids]
if leaf:
    deepest = max(leaf, key=lambda r: r["tokens"])
    n, cur = 0, deepest
    while cur and cur["parent"] in byname:
        n += 1
        cur = byname[cur["parent"]]
        if n > 200:
            break
    chain_len = n
print("\nP2.1: interior single-child nodes = %d (%.1f GiB); deepest chain hops = %d" %
      (len(interior), sum(r["size"] for r in interior) / G, chain_len))
print("   stitch load time scales with hops: today's deepest resume was 2.5 s at 336019 tokens")
print("\nbiggest v3 objects:")
for r in sorted(v3, key=lambda r: -r["size"])[:6]:
    print("   %-12s tokens=%7d %-10s %7.2f GiB dfrom=%d" %
          (r["name"][:12], r["tokens"], r["reason"], r["size"] / G, r["dfrom"]))
