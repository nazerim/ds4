#!/usr/bin/env python3
"""Compare arms on the COMMON PREFIX of requests. python3 -I compare.py <runs-dir> N arm...
Separates eviction behavior (token-denominated, speed-independent) from engine speed
(seconds per rebuilt token), because the fork and upstream engines differ in raw speed."""
import gzip, json, os, re, sys, time, statistics as st, collections

runs, N, arms = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
yr = time.localtime().tm_year
line_re = re.compile(r"^(\d{2})(\d{2}) (\d\d):(\d\d):(\d\d) ds4-server: (.*)$")
kv = lambda s, k: (lambda m: int(m.group(1)) if m else None)(re.search(rf"\b{k}=(\d+)", s))

def load(a):
    rs = [json.loads(l) for l in open(f"{runs}/{a}/requests.jsonl")][:N]
    ev = []
    lp = f"{runs}/{a}/server.log"
    for l in (gzip.open(lp + ".gz", "rt", errors="replace") if not os.path.exists(lp) else open(lp, errors="replace")):
        m = line_re.match(l)
        if m:
            mo, dy, hh, mm, ss, rest = m.groups()
            ev.append((time.mktime((yr, int(mo), int(dy), int(hh), int(mm), int(ss), 0, 0, -1)), rest))
    return rs, ev

res = {}
for a in arms:
    rs, ev = load(a)
    t_end = rs[-1]["t0"] + rs[-1]["latency_s"] + 1
    rows, evict = [], collections.Counter()
    for r in rs:
        lo, hi = r["t0"] - 1, r["t0"] + r["latency_s"] + 1
        win = [s for t, s in ev if lo <= t <= hi]
        hit = next((kv(s, "tokens") for s in win if s.startswith("kv cache hit")), None)
        miss = next((s for s in win if s.startswith("live kv cache miss")), None)
        pt = r["prompt_tokens"]
        if miss is None and hit is None: c, rb = "live", 0
        elif hit is not None: c, rb = "disk", max(0, pt - hit)
        else: c, rb = "cold", max(0, pt - (kv(miss, "common") or 0))
        rows.append((r, c, rb, pt))
    for t, s in ev:
        if t <= t_end and s.startswith("kv cache evicted"):
            evict[re.search(r"reason=(\S+)", s).group(1)] += 1
    ret = [x for x in rows if x[0]["turn"] > 1]
    lat = sorted(x[0]["latency_s"] for x in ret)
    q = lambda p: lat[min(len(lat) - 1, int(len(lat) * p))]
    cold = [x for x in ret if x[1] == "cold"]
    rebuilt = sum(x[2] for x in ret)
    total_prompt = sum(x[3] for x in ret)
    cold_s = sum(x[0]["latency_s"] for x in cold)
    cold_tok = sum(x[2] for x in cold)
    res[a] = dict(n=len(rs), ret=len(ret), mean=st.mean(lat), med=q(.5), p90=q(.9),
                  cold_frac=len(cold) / len(ret), rebuilt=rebuilt, rebuilt_frac=rebuilt / total_prompt,
                  cold_tok_per_s=(cold_tok / cold_s if cold_s else float("nan")),
                  wall=sum(x[0]["latency_s"] for x in rows) / 60, evict=sum(evict.values()),
                  ret_cls=collections.Counter(x[1] for x in ret), evk=evict)
print(f"common prefix = first {N} requests of each arm\n")
hdr = f"{'arm':10s} {'ret':>4s} {'mean_s':>7s} {'med_s':>6s} {'p90_s':>6s} {'cold%':>6s} {'rebuilt_tok':>11s} {'rebuilt%':>9s} {'cold_tok/s':>10s} {'evict':>6s} {'wall_min':>8s}"
print(hdr)
for a in arms:
    r = res[a]
    print(f"{a:10s} {r['ret']:4d} {r['mean']:7.2f} {r['med']:6.2f} {r['p90']:6.2f} {100*r['cold_frac']:6.1f} {r['rebuilt']:11d} {100*r['rebuilt_frac']:9.1f} {r['cold_tok_per_s']:10.0f} {r['evict']:6d} {r['wall']:8.1f}")
print()
for a in arms:
    print(a, dict(res[a]["ret_cls"]), dict(res[a]["evk"]))
# reply agreement between arms, per request index
print()
data = {a: {r["i"]: r["reply_sha"] for r in load(a)[0]} for a in arms}
for i, a in enumerate(arms):
    for b in arms[i + 1:]:
        same = sum(1 for k in data[a] if data[a][k] == data[b].get(k))
        print(f"reply_sha agreement {a} vs {b}: {same}/{len(data[a])}")
