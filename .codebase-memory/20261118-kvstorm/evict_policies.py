#!/usr/bin/env python3
"""Control policies against the same recorded stream: is upstream's victim choice merely
bad, or worse than random?  Run from this directory: python3 evict_policies.py [src] [N] [MiB]

The point of the controls: `random` is the floor any sane policy should beat, and `mru`
is deliberately adversarial. Upstream loses to both at identical capacity, which is what
makes this a defect rather than a design trade-off.
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evict_replay as r

BASE = r.Store.score
SRC = sys.argv[1] if len(sys.argv) > 1 else "runs/u400/requests.jsonl"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 430
BUDGET = int(sys.argv[3]) if len(sys.argv) > 3 else 16384


def make(policy, seed=1):
    rng = random.Random(seed)

    def sc(self, x, now):
        if policy == "smallest":
            return x["tok"]
        if policy == "largest":
            return -x["tok"]
        if policy == "random":
            return rng.random()
        if policy == "mru":
            return -x["last_used"]
        # upstream / lru / boost are implemented inside evict_replay.Store.score, which
        # switches on self.policy -- so they must be passed through, not overridden here.
        return BASE(self, x, now)
    return sc


reqs = [json.loads(l) for l in open(SRC)][:N]
print("%s N=%d budget=%d MiB -- identical capacity, only victim choice differs" % (SRC, N, BUDGET))
print("%-10s %6s %9s %6s %6s %5s" % ("policy", "cold%", "rebuilt", "evict", "zh%", "files"))
for p in ("upstream", "smallest", "largest", "random", "lru", "mru", "boost"):
    seeds = (1, 2, 3) if p == "random" else (1,)
    runs = []
    for sd in seeds:
        r.Store.score = make(p, sd)
        runs.append(r.replay(reqs, BUDGET, p))
    r.Store.score = BASE
    x = runs[0]
    tail = "   [seeds: %s]" % " ".join("%.1f" % o["coldpct"] for o in runs) if len(runs) > 1 else ""
    print("%-10s %6.1f %9d %6d %6.1f %5d%s"
          % (p, x["coldpct"], x["rebuilt"], x["ev"], x["zhpct"], x["files"], tail))
