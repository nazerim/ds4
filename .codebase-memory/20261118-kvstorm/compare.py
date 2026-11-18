#!/usr/bin/env python3
"""Compare arms on the COMMON PREFIX of requests.
usage: python3 -I compare.py <runs-dir> N arm [arm...]
Log lines are attributed to requests by finish-line segmentation (kvparse.py), not by time.
Token-denominated metrics (cold %, rebuilt tokens) are speed-independent; seconds are not.
Eviction counts are NOT like-for-like across trees (different reason classes); reasons are shown."""
import collections, json, math, os, statistics as st, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kvparse import segments, classify

runs, N, arms = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
pct = lambda xs, p: xs[max(0, math.ceil(p * len(xs)) - 1)]          # nearest-rank
res, data, warn = {}, {}, []
for a in arms:
    rs = [json.loads(l) for l in open(f"{runs}/{a}/requests.jsonl")]
    bad = [r for r in rs[:N] if "error" in r]
    if bad:
        raise SystemExit(f"{a}: {len(bad)} errored requests in the first {N}; refusing")
    if len(rs) < N:
        raise SystemExit(f"{a}: only {len(rs)} requests (< N={N})")
    sg = segments(f"{runs}/{a}/server.log", len(rs))[:N]     # hard error unless finish lines == ALL requests
    rs = rs[:N]
    data[a] = {r["i"]: r.get("reply_sha") for r in rs}
    rows, evk = [], collections.Counter()
    for r, s in zip(rs, sg):
        sp = s["start_prompt"]
        if sp is None:
            warn.append(f"{a} req {r['i']}: no 'prompt start' line in segment")
        elif sp != r["prompt_tokens"]:
            warn.append(f"{a} req {r['i']}: log prompt {sp} != usage prompt_tokens {r['prompt_tokens']}")
        c, rb = classify(s, r["prompt_tokens"])
        if rb < 0:
            warn.append(f"{a} req {r['i']}: unusable rebuilt tokens {rb} ({c})")
        rows.append((r, s, c, rb))
        evk.update(s["evict"])
    ret = [x for x in rows if x[0]["turn"] > 1]
    lat = sorted(x[0]["latency_s"] for x in ret)
    cold = [x for x in ret if x[2] == "cold"]
    pf = [(x[3], x[1]["prefill_s"]) for x in cold if x[1]["prefill_s"]]
    res[a] = dict(
        ret=len(ret), mean=st.mean(lat), med=st.median(lat), p90=pct(lat, .9),   # median = true median; p90 = nearest-rank
        cold_frac=len(cold) / len(ret), rebuilt=sum(x[3] for x in ret),
        rebuilt_frac=sum(x[3] for x in ret) / sum(x[0]["prompt_tokens"] for x in ret),
        pf_tok_s=(sum(t for t, _ in pf) / sum(s for _, s in pf)) if pf else float("nan"),
        wall=sum(x[0]["latency_s"] for x in rows) / 60, evict=sum(evk.values()), evk=evk,
        cls=collections.Counter(x[2] for x in ret),
        first_turn_hits=sum(1 for x in rows if x[0]["turn"] == 1 and x[1]["hit"] is not None),
        prompt_total=sum(x[0]["prompt_tokens"] for x in ret))

print(f"common prefix = first {N} requests of each arm (log lines attributed by finish-line segmentation)\n")
print(f"{'arm':9s} {'ret':>4s} {'mean_s':>7s} {'med_s':>6s} {'p90_s':>6s} {'cold%':>6s} {'rebuilt_tok':>11s} {'rebuilt%':>9s} {'prefill_t/s*':>11s} {'evict':>6s} {'wall_min':>8s}")
for a in arms:
    r = res[a]
    print(f"{a:9s} {r['ret']:4d} {r['mean']:7.2f} {r['med']:6.2f} {r['p90']:6.2f} {100*r['cold_frac']:6.1f} "
          f"{r['rebuilt']:11d} {100*r['rebuilt_frac']:9.1f} {r['pf_tok_s']:11.0f} {r['evict']:6d} {r['wall']:8.1f}")
print()
for a in arms:
    r = res[a]
    print(f"{a}: returning classes {dict(r['cls'])}  evictions {dict(r['evk'])}  first-turn disk hits {r['first_turn_hits']}  returning prompt tokens {r['prompt_total']}")
print()
for i, a in enumerate(arms):
    for b in arms[i + 1:]:
        same = sum(1 for k in data[a] if data[a][k] == data[b].get(k))
        print(f"reply_sha agreement {a} vs {b}: {same}/{len(data[a])}")
print("\n* prefill_t/s = rebuilt tokens / log 'prompt done' seconds over cold requests; includes cache-save stalls (the fork saves more often), so it slightly UNDERSTATES fork speed.")
print(f"attribution warnings: {len(warn)}")
for w in warn[:8]:
    print("  ", w)
