import re, sys, time, collections, statistics as st
arm, = sys.argv[1:]
yr=time.localtime().tm_year
L=re.compile(r"^(\d\d)(\d\d) (\d\d):(\d\d):(\d\d) ds4-server: (.*)$")
def t(m): return time.mktime((yr,int(m[0]),int(m[1]),int(m[2]),int(m[3]),int(m[4]),0,0,-1))
stores=collections.defaultdict(list)   # (tokens,size)->[store time]
life=[]; hits0=0; n=0; evict_unmatched=0
first_store={}
import gzip, os
_p=f"{arm}/server.log"
for raw in (gzip.open(_p+".gz","rt",errors="replace") if not os.path.exists(_p) else open(_p,errors="replace")):
    m=L.match(raw)
    if not m: continue
    ts=t(m.groups()[:5]); s=m.group(6)
    if s.startswith("kv cache stored"):
        k=(re.search(r"tokens=(\d+)",s).group(1), re.search(r"size=([0-9.]+) MiB",s).group(1))
        stores[k].append(ts)
    elif s.startswith("kv cache evicted") and "disk-cache-full" in s:
        k=(re.search(r"tokens=(\d+)",s).group(1), re.search(r"size=([0-9.]+) MiB",s).group(1))
        h=int(re.search(r"hits=(\d+)",s).group(1)); n+=1
        if stores[k]:
            life.append(ts-stores[k].pop(0)); hits0 += (h==0)
        else: evict_unmatched+=1
life.sort()
q=lambda p: life[min(len(life)-1,int(len(life)*p))]
print(f"{arm}: disk-cache-full evictions={n} matched-to-a-store={len(life)} hits==0: {hits0} ({100*hits0/max(1,len(life)):.0f}%)")
print(f"   store->evict lifetime s: median={q(.5):.0f} p25={q(.25):.0f} p75={q(.75):.0f} p90={q(.9):.0f}; evicted within 10s of store: {100*sum(1 for x in life if x<=10)/len(life):.0f}%  within 30s: {100*sum(1 for x in life if x<=30)/len(life):.0f}%")
