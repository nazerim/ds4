#!/usr/bin/env python3
"""live_text A/B parse (TOKENIZER-AUDIT open question 4).

Reads the four banked refresh logs (before/after x arm1 thinking-on /
arm2 thinking-off) and prints the per-arm totals, path mix, matched-session
deltas, a linear fit of the full-render cost vs session length, and the
extrapolation to agent-scale sessions.

usage: uv run --no-project python livetext_ab.py > results/20261005_livetext_ab.log
"""
import re
import statistics

ARMS = [("1 (thinking on, <=7009 tok)", ""), ("2 (effort=none, <=3598 tok)", "2")]


def load(path):
    rows = []
    for line in open(path):
        m = re.search(r"session=(\d+) path=(\w+) wall_ms=([\d.]+)", line)
        if m:
            rows.append((int(m.group(1)), m.group(2), float(m.group(3))))
    return rows


def fit(rows):
    n = len(rows)
    sx = sum(r[0] for r in rows)
    sy = sum(r[2] for r in rows)
    sxx = sum(r[0] * r[0] for r in rows)
    sxy = sum(r[0] * r[2] for r in rows)
    slope = (sxy - sx * sy / n) / (sxx - sx * sx / n)
    return sy / n - slope * sx / n, slope  # intercept_ms, ms_per_token


for label, suffix in ARMS:
    b = load(f"tests/spec_economics/results/20261005_livetext_before{suffix}_refresh.log")
    a = load(f"tests/spec_economics/results/20261005_livetext_after{suffix}_refresh.log")
    tb, ta = sum(r[2] for r in b), sum(r[2] for r in a)
    amap = {r[0]: r[2] for r in a}
    deltas = [r[2] - amap[r[0]] for r in b if r[0] in amap]
    print(f"arm {label}")
    print(f"  before: {len(b)} refreshes paths={sorted(set(r[1] for r in b))} "
          f"total={tb:.3f} ms  max session={max(r[0] for r in b)}")
    print(f"  after:  {len(a)} refreshes paths={sorted(set(r[1] for r in a))} "
          f"total={ta:.3f} ms  max session={max(r[0] for r in a)}")
    print(f"  saved over 40 turns: {tb - ta:+.3f} ms; matched sessions {len(deltas)}, "
          f"mean delta {statistics.mean(deltas) * 1000:+.1f} us/turn, "
          f"max |delta| {max(abs(d) for d in deltas) * 1000:.1f} us")
    ic, sl = fit(b)
    print(f"  full-render fit: wall_ms ~= {ic:.4f} + {sl * 1e6:.2f} ns/token * session_len")
    for L in (7009, 100000, 300000):
        print(f"    session={L:7d}: full~{ic + sl * L:7.3f} ms/turn")
    print()
