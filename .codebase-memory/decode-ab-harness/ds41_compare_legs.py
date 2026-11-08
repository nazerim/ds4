#!/usr/bin/env python3
"""Compare decode-degradation soak legs.

Usage: compare_legs.py <leg1> <leg2> ...
Reads /tmp/ds41_soak_<leg>.jsonl (API wall curves) and, when a window file
/tmp/ds41_leg_<leg>.window.start/.end exists, engine-side `decoding chunk`
rates from log/ds4-v41.log within that epoch window. Prints per-leg: cold/
append/decode rates, decay trajectory (turns 1-6 avg, 7-14, 15-24), floor.
"""
import json, os, re, sys, time

def api_curve(leg):
    p = f"/tmp/ds41_soak_{leg}.jsonl"
    if not os.path.exists(p): return None
    rows = [json.loads(l) for l in open(p) if l.strip()]
    return rows

def decode_rates(leg):
    s, e = f"/tmp/ds41_leg_{leg}.window.start", f"/tmp/ds41_leg_{leg}.window.end"
    if not (os.path.exists(s) and os.path.exists(e)): return None
    t0, t1 = int(open(s).read()), int(open(e).read())
    # engine log uses local HH:MM:SS with MMDD prefix
    rows = []
    for line in open("/Users/naz/Projects/ds4/log/ds4-v41.log", errors="ignore"):
        m = re.match(r"^(\d{4}) (\d{2}):(\d{2}):(\d{2}) .*\bavg=([\d.]+) t/s", line)
        if not m: continue
        mmdd, hh, mi, ss = m.group(1), *map(int, m.groups()[1:4])
        try:
            ts = time.mktime(time.strptime(f"{mmdd}{hh:02d}{mi:02d}{ss:02d}", "%Y%m%d%H%M%S")) if len(mmdd)==4 else 0
        except Exception:
            continue
        # mmdd like "1108" -> assume year 2026
        try:
            ts = time.mktime((2026, int(mmdd[:2]), int(mmdd[2:]), hh, mi, ss, 0,0,-1))
        except Exception:
            continue
        if t0 <= ts <= t1: rows.append(float(m.group(8)))
    return rows

def seg(v, a, b):
    s = [r for r in v if a <= r["turn"] <= b and "appended_tokens" in r]
    if not s: return None
    return sum(r["completion_tokens"] / r["wall_s"] * (1 - r.get("cached",0)/max(1,r.get("prompt_tokens",1))) for r in s) / len(s)

for leg in sys.argv[1:]:
    rows = api_curve(leg)
    if not rows:
        print(f"{leg:10s}: no data"); continue
    cold = next((r for r in rows if r.get("phase") == "cold"), None)
    turns = [r for r in rows if "appended_tokens" in r]
    recall = next((r for r in rows if r.get("phase") == "recall"), None)
    # per-turn effective rate = completion tokens per wall (mixes append+decode; good enough trend)
    eff = [r["completion_tokens"] / r["wall_s"] for r in turns]
    dr = decode_rates(leg)
    def tps(r):
        return r["completion_tokens"] / r["wall_s"]
    early = eff[:6]; mid = eff[6:14]; late = eff[14:]
    print(f"--- leg {leg} ---")
    if cold: print(f"  cold prefill 40k: {cold['prompt_tokens']} tok in {cold['wall_s']}s = {cold['prompt_tokens']/cold['wall_s']:.0f} t/s")
    if turns:
        print(f"  turn tps (API wall): early {sum(early)/len(early):.2f}  mid {sum(mid)/len(mid):.2f}  late {sum(late)/len(late):.2f}  floor {min(tps(r) for r in turns):.2f}")
        print(f"  last-3 turns: " + " ".join(f"{tps(r):.1f}" for r in turns[-3:]))
    if dr:
        fresh = max(dr[:20]) if len(dr) >= 20 else max(dr)
        print(f"  engine decode buckets: fresh {fresh:.1f}  late {sum(dr[-15:])/len(dr[-15:]):.1f} t/s ({len(dr)} samples)")
    if recall: print(f"  recall turn: {recall['wall_s']}s")
