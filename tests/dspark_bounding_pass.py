#!/usr/bin/env python3
"""Phase-3 bounding pass: server-mode arm runner.
Usage: python3 tests/dspark_bounding_pass.py <arm-tag> [logpath]
6 prompts x temp{0,1.0}, 512 toks, sequential; buckets server decode rows by
request time-window. Writes /tmp/.../bounding_<arm>.json
"""
import json, re, sys, time, urllib.request, datetime

ARM = sys.argv[1]
LOG = sys.argv[2] if len(sys.argv) > 2 else "log/ds4.log"
BASE = "http://127.0.0.1:8001/v1/chat/completions"
D = "/private/var/folders/gp/hjkf5cnx3vg4pxxv0zbd1c8h0000gn/T"
MAXT = 512

P = json.load(open(f"{D}/bounding_prompts.json"))
PROBES = {k: v for k, v in P.items()}

def send(pid, temp):
    # tools[] is REQUIRED for server-side speculation: ds4-server.c gates
    # dspark on dsml_token_id<0, which plain (tool-less) chat requests never
    # satisfy (discovery 2026-10-29). Without tools both arms measure
    # spec-off servers; this re-verify exists precisely to fix that.
    body = {"messages": [{"role": "user", "content": PROBES[pid]}],
            "tools": [{"type": "function", "function": {
                "name": "edit", "description": "Write a file to disk",
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}},
                    "required": ["path", "content"]}}}],
            "max_tokens": MAXT, "temperature": temp, "top_p": 0.95, "seed": 7700,
            "stream": False}
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=5400) as r:
        d = json.loads(r.read().decode())
    return t0, time.time(), d

def parse_window(log_lines, t_start, t_end):
    # log timestamps: "%m%d %H:%M:%S" server local
    DEC = re.compile(r'^(\d{4} \d{2}:\d{2}:\d{2}) ds4-server: chat ctx=\d+\.\.(\d+):\d+ gen=\d+(?: [A-Z_]+)* decoding chunk=([\d.]+) t/s')
    lo = time.strftime("%m%d %H:%M:%S", time.localtime(t_start - 2))  # small skew slack
    hi = time.strftime("%m%d %H:%M:%S", time.localtime(t_end + 2))
    rows = []
    for l in log_lines:
        m = DEC.search(l)
        if not m: continue
        ts = m.group(1)
        if lo <= ts <= hi:
            r = float(m.group(3))
            if r < 90:
                labs = l.split("decoding")[0]
                cls = "DSML" if "DSML" in labs else ("THINK" if "THINKING" in labs else "plain")
                rows.append((cls, r))
    return rows

def stats(rows):
    if not rows: return None
    v = [r for _, r in rows]
    hi = [r for r in v if r >= 45]
    d = [r for c, r in rows if c == "DSML"]; t = [r for c, r in rows if c == "THINK"]
    med = sorted(v)[len(v)//2]
    return {"n": len(v), "med": round(med, 1), "engaged_share": round(len(hi)/len(v), 2),
            "dsml_n": len(d), "dsml_med": round(sorted(d)[len(d)//2], 1) if d else None,
            "think_med": round(sorted(t)[len(t)//2], 1) if t else None}

print(f"# ARM {ARM}: warmup")
send("mathq", 0)  # discard (engine warm)
time.sleep(2)
log_lines = open(LOG, errors="replace").readlines()  # snapshot start; reread deltas later
results = {}
for temp in (0.0, 1.0):
    for pid in PROBES:
        try:
            t0, t1, d = send(pid, temp)
        except Exception as e:
            print(f"  !! {pid} t{temp} FAILED {type(e).__name__}: {str(e)[:60]}")
            continue
        u = d.get("usage", {})
        ct = u.get("completion_tokens", 0)
        wall = t1 - t0
        e2e = round(ct / wall, 1) if wall else None
        print(f"  t{temp} {pid}: ct={ct} wall={wall:.1f}s e2e={e2e}", flush=True)
        results[f"{pid}@t{temp}"] = {"e2e_tps": e2e, "completion": ct, "t0": t0, "t1": t1}
        time.sleep(1)
# bucket server rows
lines = open(LOG, errors="replace").readlines()
for k, r in results.items():
    r["server"] = stats(parse_window(lines, r["t0"], r["t1"]))
    del r["t0"], r["t1"]
json.dump({"arm": ARM, "results": results}, open(f"{D}/bounding_{ARM}.json", "w"), indent=1)
print(f"wrote {D}/bounding_{ARM}.json")
