"""Compute official-head baseline proxies from the banked corpus.

The corpus records ONE ROW per dspark prepare cycle at frontier `pos`, with
streak = tokens committed beyond the seed before the NEXT prepared cycle.
This is a pos-GAP (scheduler-skip merges into it) — NOT the raw per-stage
acceptance the eval batteries measure (p1/p3). What it DOES give us, on the
target distribution:

  p_ge1    = P(streak >= 1)   a cycle committed at least one draft token
  p_ge3    = P(streak >= 3)   block middle kept going (proxy for p3-ish)
  rate     = mean(1+streak)   tokens advanced per prepared cycle
  share    = fraction of positions covered by prepared cycles (drafting
             engagement density; the rest is scheduler bypass)

reported per depth band + per day. Engine batteries remain the authoritative
gate; these are the TREND LINE the retrained head must beat in live traffic.

Usage: uv run python -m dspark.baseline [day ...]  (default: all banked)
Writes trainer/BASELINE.json
"""
from __future__ import annotations

import json
import os
import sys

from .data_contract import CORPUS_ROOT, STREAK_CLAMP
from .dataset import corpus_segments


def band(p: int) -> str:
    return ("0-16k" if p < 16384 else "16-64k" if p < 65536
            else "64-128k" if p < 131072 else "128k+")


def main():
    days = sys.argv[1:] or None
    agg = {}
    for s in corpus_segments(days):
        day = os.path.basename(os.path.dirname(s.file))
        for r in s.usable:
            if not r.gap_for_stats:
                continue
            k = (day, band(r.pos))
            d = agg.setdefault(k, {"cyc": 0, "ge1": 0, "ge3": 0,
                                   "tok": 0, "span_cyc": 0, "span_tok": 0})
            d["cyc"] += 1
            d["ge1"] += r.streak >= 1
            d["ge3"] += r.streak >= 3
            d["tok"] += 1 + r.streak
    out = {"note": "pos-gap proxies (skip-merged); engine battery = truth",
           "rows": {}}
    for (day, b), d in sorted(agg.items()):
        out["rows"][f"{day}|{b}"] = {
            "cycles": d["cyc"],
            "p_ge1": round(d["ge1"] / d["cyc"], 4),
            "p_ge3": round(d["ge3"] / d["cyc"], 4),
            "tokens_per_cycle": round(d["tok"] / d["cyc"], 3),
        }
    # engagement density: cycles vs span (tokens committed inside segments)
    tot_c = tot_span = 0
    for s in corpus_segments(days):
        u = [r for r in s.usable if r.gap_for_stats]
        if len(u) < 2:
            continue
        tot_c += len(u)
        tot_span += (u[-1].pos - u[0].pos)
    out["engagement_density"] = {
        "prepared_cycles": tot_c, "tokens_in_segments": tot_span,
        "cycle_per_token": round(tot_c / max(tot_span, 1), 4),
        "caveat": "span includes prefill jumps at segment starts (first "
                  "cycle pos only); treat as upper-bound density",
    }
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "BASELINE.json")
    json.dump(out, open(path, "w"), indent=1)
    for k, v in out["rows"].items():
        print(f"{k:<28} cyc={v['cycles']:>7} p_ge1={v['p_ge1']:.3f} "
              f"p_ge3={v['p_ge3']:.3f} tok/cyc={v['tokens_per_cycle']:.2f}")
    print("\n" + json.dumps(out["engagement_density"], indent=1))
    print(f"wrote {path}")


main()
