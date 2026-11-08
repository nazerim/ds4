#!/usr/bin/env python3
"""V4.1 speed matrix: prompt variety + prefill threshold scan + std suite."""
import json, os, random, sys, time, urllib.request, uuid, glob

URL = "http://127.0.0.1:8003/v1/chat/completions"

def post(content, think=False, max_tokens=8):
    body = json.dumps({"model": "x", "think": think, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": content}]}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    d = json.load(urllib.request.urlopen(req, timeout=3600))
    wall = time.time() - t0
    u = d["usage"]; pt = u["prompt_tokens"]
    det = u.get("prompt_tokens_details", {})
    return {"prompt_tokens": pt, "wall_s": round(wall, 1),
            "tps": round(pt / max(wall, .01), 1), "cached": det.get("cached_tokens", 0),
            "ans": (d["choices"][0]["message"].get("content") or "")[:50]}

def corpus(md_chars, code_chars=0, kind="prose", rng=None):
    rng = rng or random.Random(42)
    if kind == "prose":
        parts = []
        files = sorted(glob.glob("docs/*.md") + glob.glob("*.md"))
        i = 0
        while sum(len(p) for p in parts) < md_chars:
            parts.append(open(files[i % len(files)], errors="ignore").read())
            i += 1
        return "".join(parts)[:md_chars]
    if kind == "code":
        parts = []
        files = sorted(glob.glob("*.c") + glob.glob("*.h") + glob.glob("*.py"))
        i = 0
        while sum(len(p) for p in parts) < md_chars:
            parts.append(open(files[i % len(files)], errors="ignore").read())
            i += 1
        return "".join(parts)[:md_chars]
    if kind == "json":
        parts = []
        while sum(len(p) for p in parts) < md_chars:
            parts.append(json.dumps({"records": [
                {"id": rng.randint(0, 10**9), "name": f"item_{rng.randrange(10**6)}",
                 "vals": [round(rng.random() * 1000, 3) for _ in range(8)],
                 "tags": [uuid.uuid4().hex[:6] for _ in range(3)]} for _ in range(200)]}))
        return "".join(parts)[:md_chars]
    if kind == "cjk":
        base = "深度学习模型的推理优化涉及内存带宽、算子融合与缓存调度。量化策略在精度与吞吐之间权衡。"
        return (base * (md_chars // len(base) + 1))[:md_chars]
    if kind == "random":
        rng2 = random.Random(7)
        return "".join(f"{rng2.getrandbits(128):032x} " for _ in range(md_chars // 33 + 1))[:md_chars]
    raise ValueError(kind)

def probe(kind, approx_tokens, chars_per_tok=4.5):
    text = corpus(int(approx_tokens * chars_per_tok), kind=kind)
    # salt defeats KV anchor reuse; question at the end
    salt = f"[probe {kind} run {uuid.uuid4().hex}]"
    return post(f"{salt} {text} Q: repeat the token 'ZED' once.", max_tokens=4)

if __name__ == "__main__":
    mode = sys.argv[1]
    results = []
    def run(label, *a, **kw):
        r = {"case": label}; r.update(*a, **kw); print(r, flush=True); return r
    if mode == "variety":
        for kind, cpt in [("prose", 4.5), ("code", 3.2), ("json", 2.9), ("cjk", 1.4), ("random", 8.0)]:
            r = {"kind": kind}; r.update(probe(kind, 36000, cpt)); results.append(r); print(r, flush=True)
    elif mode == "threshold":
        for tok in (8000, 16000, 24000, 32000, 48000, 64000):
            r = {"target": tok}; r.update(probe("prose", tok)); results.append(r); print(r, flush=True)
    elif mode == "std":  # comparable across config restarts
        for kind, cpt in [("code", 3.2), ("prose", 4.5)]:
            run("std_" + kind, probe(kind, 36000, cpt))
        # warm decode test: same prompt twice, measure 2nd; 48 tok output
        prompt = "std-decode-marker " + uuid.uuid4().hex + " Explain in exactly two sentences why the sky appears blue."
        post(prompt, max_tokens=48)                      # warm content/conversation
        run("std_decode48", post(prompt, max_tokens=48)) # same conv, cached
        # thinking-on turn cost at default effort
        run("std_think", post("Q: 21*43? Think briefly.", think=True, max_tokens=220))
    print("DONE", mode)
