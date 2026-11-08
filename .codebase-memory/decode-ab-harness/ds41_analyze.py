#!/usr/bin/env python3
import re, sys, datetime as dt
LOG="/Users/naz/Projects/ds4/log/ds4-v41.log"
YEAR=2026
pat_start = re.compile(r"^(\d{4}) (\d{2}:\d{2}:\d{2}) .*\bchat (\w+)? ?ctx=(\d+)\.\.(\d+):(\d+) \S* ?prompt start")
pat_dec   = re.compile(r"^(\d{4}) (\d{2}:\d{2}:\d{2}) .*decoding chunk=([\d.]+) t/s avg=([\d.]+)")
pat_done  = re.compile(r"^(\d{4}) (\d{2}:\d{2}:\d{2}) .*ctx=(\d+)\.\.(\d+):(\d+) .*prompt done ([\d.]+)s")
def ts(d,hm):
    return dt.datetime(YEAR,int(d[:2]),int(d[2:]),*map(int,hm.split(":")))
rows_s, rows_d, rows_done = [], [], []
for line in open(LOG, errors="ignore"):
    if not line.startswith("1108"): continue
    m = pat_start.match(line)
    if m: rows_s.append((ts(m.group(1),m.group(2)), int(m.group(4)), int(m.group(5)), int(m.group(6)))); continue
    m = pat_done.match(line)
    if m: rows_done.append((ts(m.group(1),m.group(2)), int(m.group(3)), int(m.group(4)), float(m.group(5)))); continue
    m = pat_dec.match(line)
    if m: rows_d.append((ts(m.group(1),m.group(2)), float(m.group(3))))

def window(name, t0, t1):
    print(f"=== {name} ===")
    d = [r for r in rows_d if t0 <= r[0] <= t1]
    if d:
        print(f"  decode samples: {len(d)}  first-5avg {sum(x[1] for x in d[:5])/min(5,len(d)):.1f}  last-15avg {sum(x[1] for x in d[-15:])/len(d[-15:]):.1f}  min {min(x[1] for x in d):.1f}")
        # hourly-ish buckets of 15 min
        buckets = {}
        for t, v in d:
            k = (t - t0).seconds // 900
            buckets.setdefault(k, []).append(v)
        print("  15-min decode buckets:", " ".join(f"{k*15}m:{sum(v)/len(v):.1f}" for k, v in sorted(buckets.items())))
    s = [r for r in rows_done if t0 <= r[0] <= t1 and r[2] != r[1]]
    ap = [(a,b,(b-a)/max(w,.001)) for t,a,b,w in s if 0 < b-a < 20000]
    if ap:
        print(f"  appends: n={len(ap)} avg {sum(x[2] for x in ap)/len(ap):.1f} t/s  cold (b-a>20000): {len([1 for t,a,b,w in s if b-a>20000])}")
    cold = [r for r in s if r[1]==0]
    print(f"  COLD full-prefills in window: {len(cold)} (should be 1 per window = session start)")

w0 = window("BASELINE (05:15-06:48)", dt.datetime(2026,11,8,5,15), dt.datetime(2026,11,8,6,48))
w1 = window("LEG d1024 (06:50-now)", dt.datetime(2026,11,8,6,50), dt.datetime.now())
