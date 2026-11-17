#!/usr/bin/env python3
"""Summarize one arm: python3 -I summarize.py <run-dir>  (server.log + requests.jsonl)."""
import gzip, json, os, re, sys, time, statistics as st, collections

d = sys.argv[1]
reqs = [json.loads(l) for l in open(f"{d}/requests.jsonl")]
ok = [r for r in reqs if "error" not in r]

line_re = re.compile(r"^(\d{2})(\d{2}) (\d\d):(\d\d):(\d\d) ds4-server: (.*)$")
yr = time.localtime().tm_year
ev = []
lp = f"{d}/server.log"
for l in (gzip.open(lp + ".gz", "rt", errors="replace") if not os.path.exists(lp) else open(lp, errors="replace")):
    m = line_re.match(l)
    if m:
        mo, dy, hh, mm, ss, rest = m.groups()
        ts = time.mktime((yr, int(mo), int(dy), int(hh), int(mm), int(ss), 0, 0, -1))
        ev.append((ts, rest))

def kv(s, k):
    m = re.search(rf"\b{k}=(\d+)", s)
    return int(m.group(1)) if m else None

cnt = collections.Counter()
for _, s in ev:
    if s.startswith("kv cache evicted"):
        cnt["evicted:" + (re.search(r"reason=(\S+)", s).group(1))] += 1
    elif s.startswith("kv cache stored"):
        cnt["stored:" + (re.search(r"reason=(\S+)", s).group(1))] += 1
    elif s.startswith("kv cache hit"):
        cnt["disk_hit"] += 1
    elif s.startswith("live kv cache miss"):
        cnt["live_miss"] += 1
    elif s.startswith("kv cache lru-retire"):
        cnt["lru_retire_candidate"] += 1
    elif s.startswith("kv cache delete deferred"):
        cnt["delete_deferred"] += 1
    elif "discarded corrupt" in s:
        cnt["discarded_corrupt"] += 1

# per-request classification (sequential => time window = [t0-1, t0+latency+1])
cls = collections.Counter()
rebuild_tokens = 0
rows = []
for r in ok:
    lo, hi = r["t0"] - 1, r["t0"] + r["latency_s"] + 1
    win = [s for t, s in ev if lo <= t <= hi]
    hit = next((kv(s, "tokens") for s in win if s.startswith("kv cache hit")), None)
    miss = next((s for s in win if s.startswith("live kv cache miss")), None)
    pt = r.get("prompt_tokens") or 0
    if miss is None and hit is None:
        c, rb = "live_reuse", 0
    elif hit is not None:
        c, rb = "disk_hit", max(0, pt - hit)
    else:
        c, rb = "cold_rebuild", max(0, pt - (kv(miss, "common") or 0))
    cls[(c, r["turn"] > 1)] += 1
    if r["turn"] > 1:
        rebuild_tokens += rb
        rows.append((r["latency_s"], c))

lat = sorted(x for x, _ in rows)
q = lambda p: lat[min(len(lat) - 1, int(len(lat) * p))] if lat else float("nan")
print(f"requests ok={len(ok)} err={len(reqs)-len(ok)} empty_replies={sum(1 for r in ok if r.get('empty'))}")
print(f"returning-turn requests={len(rows)}  latency s: mean={st.mean(lat):.1f} median={q(.5):.1f} p90={q(.9):.1f} max={lat[-1]:.1f}" if lat else "no returning turns")
print(f"returning-turn prefill tokens rebuilt (approx)={rebuild_tokens}")
for (c, ret), n in sorted(cls.items()):
    print(f"  class {c:12s} returning={ret!s:5s} n={n}")
for k, v in sorted(cnt.items()):
    print(f"  log {k} = {v}")
dur = (ok[-1]["t0"] + ok[-1]["latency_s"] - ok[0]["t0"]) / 60 if ok else 0
evs = sum(v for k, v in cnt.items() if k.startswith("evicted:"))
print(f"duration={dur:.1f} min  evictions={evs}  evictions/request={evs/max(1,len(ok)):.2f}")
