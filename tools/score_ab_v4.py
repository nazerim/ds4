#!/usr/bin/env python3
"""AB v4 scorer: mechanically applies the pre-registered decision rule.

Usage:
  python3 tools/score_ab_v4.py \
    --era-a LOG_A --era-b LOG_B \
    --big-a BIGJSON_A --big-b BIGJSON_B \
    [--parity-a J --parity-b J] [--a1b-pass]

All rules from .codebase-memory/AB-PROTOCOL-3C-20261104.md "Decision
rule (v4)" - implement, don't re-interpret. Era LOGs are the ROTATED
per-arm boot-era logs (one 'ds4: DSpark stats' aggregate line each;
parsed at offset 0). BIG JSONs are --big-leg outputs. Pairing is by
(seed,turn) at t=0; pairs whose content_sha256 differ are DROPPED and
counted (determinism check, not assumption). Prints per-condition
verdicts + overall ADVANCE/HOLD. Stdlib only.
"""
import argparse
import json
import re
import statistics
import sys

STATS_RE = re.compile(
    r"ds4: DSpark stats cycles=(\d+) first_tokens=(\d+) proposed=(\d+) "
    r"accepted_draft=(\d+) accept_rate=([\d.]+)% avg_accept=([\d.]+)")
N_BINS = 17


def era_stats(path):
    hits = []
    for line in open(path, errors="replace"):
        m = STATS_RE.search(line)
        if not m:
            continue
        d = dict(zip(("cycles", "first_tokens", "proposed", "accepted",
                      "accept_pct", "avg_accept"),
                     (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                      int(m.group(4)), float(m.group(5)),
                      float(m.group(6)))))
        ah = re.search(r"accepted_len_hist=([0-9:, ]+)", line)
        bins = [0] * N_BINS
        if ah:
            for part in ah.group(1).split(","):
                if ":" in part:
                    k, v = part.split(":", 1)
                    try:
                        if 0 <= int(k) < N_BINS:
                            bins[int(k)] += int(v)
                    except ValueError:
                        pass
        d["hist"] = bins
        d["errors"] = int(re.search(r"errors=(\d+)", line).group(1)) \
            if re.search(r"errors=(\d+)", line) else -1
        d["invalid"] = int(re.search(r"invalid=(\d+)", line).group(1)) \
            if re.search(r"invalid=(\d+)", line) else -1
        hits.append(d)
    if not hits:
        raise SystemExit(f"{path}: no 'DSpark stats' era line "
                         "(rotated era log expected)")
    tot = {"cycles": 0, "first_tokens": 0, "proposed": 0, "accepted": 0,
           "hist": [0] * N_BINS, "errors": 0, "invalid": 0}
    for h in hits:
        for k in ("cycles", "first_tokens", "proposed", "accepted",
                  "errors", "invalid"):
            tot[k] += h[k]
        tot["hist"] = [a + b for a, b in zip(tot["hist"], h["hist"])]
    tot["accept_pct"] = 100.0 * tot["accepted"] / max(1, tot["proposed"])
    tot["prop_per_cycle"] = tot["proposed"] / max(1, tot["cycles"])
    tot["full5_share"] = tot["hist"][5] / max(1, sum(tot["hist"]))
    tot["n_lines"] = len(hits)
    return tot


def paired_t0(a_rows, b_rows):
    ka = {(r["seed"], r["turn"]): r for r in a_rows
          if r["temperature"] == 0.0}
    kb = {(r["seed"], r["turn"]): r for r in b_rows
          if r["temperature"] == 0.0}
    shared = sorted(set(ka) & set(kb))
    matched, dropped = [], []
    for k in shared:
        if (ka[k]["content_sha256"] == kb[k]["content_sha256"]
                and ka[k]["completion_tokens"] == kb[k]["completion_tokens"]
                and ka[k]["completion_tokens"] > 0):
            matched.append(k)
        else:
            dropped.append(k)
    ratios, walls = [], []
    for k in matched:
        ra = ka[k].get("first_token_s") or ka[k]["wall_s"]
        rb = kb[k].get("first_token_s") or kb[k]["wall_s"]
        ratios.append(ka[k]["wall_s"] / kb[k]["wall_s"])   # >1 = B faster
        walls.append(rb - ra)                              # <0 = B faster
    return matched, dropped, ratios, walls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--era-a", required=True)
    ap.add_argument("--era-b", required=True)
    ap.add_argument("--big-a", required=True)
    ap.add_argument("--big-b", required=True)
    ap.add_argument("--parity-a", default=None)
    ap.add_argument("--parity-b", default=None)
    ap.add_argument("--a1b-pass", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    A, B = era_stats(a.era_a), era_stats(a.era_b)
    ba = json.load(open(a.big_a)).get("big_leg", [])
    bb = json.load(open(a.big_b)).get("big_leg", [])
    matched, dropped, ratios, walls = paired_t0(ba, bb)
    if not matched:
        raise SystemExit("no matched t=0 pairs - determinism or wiring "
                         "broken; HOLD, do not score")
    med_ratio = statistics.median(ratios)
    conds = {}
    conds["i_paired_t0_speed(B>=A)"] = (med_ratio >= 1.0,
                                        f"median A/B wall ratio {med_ratio:.3f}"
                                        f" (n={len(matched)}, dropped {len(dropped)})")
    ratio_pp = B["prop_per_cycle"] / max(1e-9, A["prop_per_cycle"])
    conds["ii_proposed_per_cycle_drop>=8%"] = (ratio_pp <= 0.92,
                                               f"B/A {ratio_pp:.3f}")
    dacc = B["accept_pct"] - A["accept_pct"]
    conds["iii_kept_accept_not_below_A-1pp"] = (dacc >= -1.0,
                                                f"B {B['accept_pct']:.2f}%"
                                                f" vs A {A['accept_pct']:.2f}%"
                                                f" ({dacc:+.2f}pp)")
    d5 = 100.0 * (B["full5_share"] - A["full5_share"])
    conds["iv_full5_not_below_A-3pp"] = (d5 >= -3.0, f"{d5:+.2f}pp")
    conds["v_a1b_confcal3"] = (a.a1b_pass, "--a1b-pass flag")
    stops = {}
    stops["paired>5%regression"] = med_ratio < 0.95
    cov_ratio = (B["prop_per_cycle"] / max(1e-9, A["prop_per_cycle"]))
    stops["coverage_overshoot(keep>.9538-equiv)"] = cov_ratio > 0.954
    stops["engine_errors"] = (A["errors"] or B["errors"]
                              or A["invalid"] or B["invalid"])
    parity_note = "n/a"
    if a.parity_a and a.parity_b:
        pa = json.load(open(a.parity_a))
        pb = json.load(open(a.parity_b))
        try:
            ta = [r["tok_s"] for r in pa["requests"] if r["temperature"] == 0.0]
            tb = [r["tok_s"] for r in pb["requests"] if r["temperature"] == 0.0]
            m = statistics.median(tb) / statistics.median(ta)
            parity_note = f"median A/B ratio {m:.3f}"
            stops["parity_leg>5%"] = m < 0.95
        except Exception as e:            # noqa: BLE001
            stops["parity_leg>5%"] = False
            parity_note = f"parse issue: {e}"
    verdict = {
        "A": {k: A[k] for k in ("cycles", "proposed", "accept_pct",
                                "prop_per_cycle", "full5_share")},
        "B": {k: B[k] for k in ("cycles", "proposed", "accept_pct",
                                "prop_per_cycle", "full5_share")},
        "conditions": {k: {"pass": p, "detail": d} for k, (p, d)
                       in conds.items()},
        "stop_flags": stops,
        "parity_leg": parity_note,
        "matched_pairs": len(matched), "dropped_pairs": len(dropped),
        "ADVANCE": (all(p for p, _ in conds.values())
                    and not any(stops.values())),
    }
    txt = json.dumps(verdict, indent=1)
    print(txt)
    if a.out:
        open(a.out, "w").write(txt)
    return 0 if verdict["ADVANCE"] else 1


if __name__ == "__main__":
    sys.exit(main())
