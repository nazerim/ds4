import json,math,random,importlib.util
spec=importlib.util.spec_from_file_location("r","/tmp/opus2/replay.py"); r=importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
base=r.Store.score
def mk(p,seed=1):
    rng=random.Random(seed)
    def sc(self,x,now):
        if self.policy=="smallest": return x["tok"]
        if self.policy=="largest":  return -x["tok"]
        if self.policy=="random":   return rng.random()
        if self.policy=="mru":      return -x["last_used"]
        return base(self,x,now)
    return sc
r.Store.score=mk(None)
reqs=[json.loads(l) for l in open("runs/u400/requests.jsonl")][:430]
print(f"{'policy':10} {'cold%':>6} {'rebuilt':>9} {'evict':>6} {'zh%':>6} {'files':>5}  (16384 MiB, SAME capacity)")
for p in ("upstream","smallest","largest","random","lru","mru","boost"):
    out=[]
    for s in (1,2,3):
        r.Store.score=mk(p,s); out.append(r.replay(reqs,16384,p))
    x=out[0]; cp=[o['coldpct'] for o in out]
    extra=f"  [seeds: {' '.join(f'{v:.1f}' for v in cp)}]" if p=="random" else ""
    print(f"{p:10} {x['coldpct']:6.1f} {x['rebuilt']:9d} {x['ev']:6d} {x['zhpct']:6.1f} {x['files']:5d}{extra}")
