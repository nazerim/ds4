#!/usr/bin/env python3
"""3-way decode A/B: per-interval chunk rates (primary, artifact-filtered)
vs per-turn avg-derived rates (cross-check). Usage: ds41_ab3.py FILE [FILE...]"""
import sys, re, statistics as st
DEC = re.compile(r'gen=(\d+) .*decoding chunk=([\d.]+) t/s avg=([\d.]+) t/s ([\d.]+)s')
FIN = re.compile(r'gen=(\d+) .*finish=\w+ ([\d.]+)s')
q = lambda a,p: sorted(a)[min(int(len(a)*p), len(a)-1)] if a else 0
for path in sys.argv[1:]:
    chunks = []; turn_last = []; prev_gen = prev_t = None; tot_wall = 0.0
    for line in open(path, errors="ignore"):
        if 'prompt start' in line: prev_gen = prev_t = None
        m = DEC.search(line)
        if m:
            g, ck, av, t = int(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
            if prev_gen is not None and g > prev_gen:
                rate = (g - prev_gen) / (t - prev_t)
                if rate <= 60: chunks.append(rate)
            else:
                if prev_gen is not None: turn_last.append(prev_gen / prev_t)
            prev_gen, prev_t = g, t
        m = FIN.search(line)
        if m and prev_gen: turn_last.append(prev_gen / prev_t); prev_gen = prev_t = None
    if prev_gen and turn_last and turn_last[-1]*0 != 0: pass
    print(f"== {path.split('/')[-1]}")
    print(f"   intervals (filtered): n={len(chunks)}  p10 {q(chunks,.1):.1f}  MED {q(chunks,.5):.1f}  p90 {q(chunks,.9):.1f}  p99 {q(chunks,.99):.1f}  max {max(chunks):.1f}")
    print(f"   per-turn gen/decode-wall: n={len(turn_last)}  p10 {q(turn_last,.1):.1f}  MED {q(turn_last,.5):.1f}  mean {st.mean(turn_last):.1f}")
