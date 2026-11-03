#!/usr/bin/env python3
"""S6 AB bench client for the DSpark conf-head A/B (protocol:
.codebase-memory/AB-PROTOCOL-3C-20261104.md). WRITTEN NIGHT 20261104,
NOT YET EXECUTED against the live engine.

Measures, for the current engine boot (one arm):
  * client-side tok/s per prompt (non-streamed completion_tokens / wall);
  * engine-side DSpark acceptance stats by parsing 'ds4: DSpark stats'
    lines from the server log, WINDOWED by timestamp since --since (boot
    ts) - sessions flush their stats at session end, so run this AFTER the
    prompt sweep and it picks up the matching lines.

Requires the server booted with DS4_DSPARK_STATS=1 for stats lines
(restart-collect env can add it). Stdlib only.

Usage:
  python3 tools/bench_ab_3c.py --port 8002 --arm B \
      --log log/ds4.log --since "2026-11-04 07:30:00" --out armB.json
"""
import argparse
import datetime as dt
import json
import re
import statistics
import sys
import time
import urllib.request

# Fixed prompt set: mix of code, prose, arithmetic replay, and long-tail
# retrieval. Deterministic; identical across arms. Kept small for A/B
# windows (each prompt is one short chat completion).
PROMPTS = [
    ("code", "Write a C function that reverses a NULL-terminated linked "
             "list in place. Include a doc comment and a test main()."),
    ("code", "Explain the difference between mmap MAP_SHARED and "
             "MAP_PRIVATE, then give a minimal Python example using "
             "mmap.mmap ACCESS_READ."),
    ("prose", "Summarize, in exactly five bullets, why speculative "
              "decoding acceptance rates depend on draft confidence "
              "calibration, not only on draft top-1 accuracy."),
    ("arith", "Compute step by step: 17*23, then 121/11, then "
              "(3^4 - 80)*7. State each intermediate."),
    ("long", "List the Linux syscalls involved in the lifecycle of an "
             "mmap'd file-backed read-only region, from open() to munmap"
             "(), annotating which can block, which fail with ENOMEM, "
             "and what SIGBUS has to do with it."),
    ("retr", "Quote the opening sentence of the GNU Manifesto and "
             "attribute it correctly."),
]

STATS_RE = re.compile(
    r"ds4: DSpark stats cycles=(\d+) first_tokens=(\d+) proposed=(\d+) "
    r"accepted_draft=(\d+) accept_rate=([\d.]+)% avg_accept=([\d.]+)")
ACCEPT_HIST_RE = re.compile(r"accept[a-z_ ]*hist[^=]*=([^\r\n]+)")


def call(port, model, prompt, temperature, max_tokens=256):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read().decode())
    wall = time.perf_counter() - t0
    usage = resp.get("usage") or {}
    ctok = int(usage.get("completion_tokens", 0))
    return {"wall_s": round(wall, 3), "completion_tokens": ctok,
            "tok_s": round(ctok / wall, 2) if wall > 0 else None}


def parse_stats(log_path, since_ts):
    """Windowed engine stats lines (per-session flushes)."""
    out = []
    try:
        with open(log_path, "r", errors="replace") as f:
            for line in f:
                m = STATS_RE.search(line)
                if not m:
                    continue
                # log lines look like "[ts] ds4: ..." OR bare stderr; if a
                # timestamp is present enforce --since, else take it (arm
                # isolation is then guaranteed by the caller's windowing).
                ts_m = re.match(r".?(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)",
                                line)
                if ts_m and since_ts:
                    ts = dt.datetime.fromisoformat(
                        ts_m.group(1).replace(" ", "T"))
                    if ts < since_ts:
                        continue
                cy, ft, prop, accd, arate, aacc = m.groups()
                out.append({"cycles": int(cy), "proposed": int(prop),
                            "accepted_draft": int(accd),
                            "accept_rate_pct": float(arate),
                            "avg_accept": float(aacc),
                            "accept_hist": (ACCEPT_HIST_RE.search(line)
                                            or [None, None])[1]})
    except FileNotFoundError:
        print(f"warn: log {log_path} not found; engine stats skipped",
              file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8002)
    ap.add_argument("--model", default="qwen")
    ap.add_argument("--arm", default="X", help="free-form arm tag")
    ap.add_argument("--log", default="log/ds4.log")
    ap.add_argument("--since", default=None,
                    help="boot timestamp filter, e.g. '2026-11-04 07:30:00'")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--temps", default="0,1")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    since = (dt.datetime.fromisoformat(a.since.replace(" ", "T"))
             if a.since else None)
    report = {"arm": a.arm, "port": a.port, "model": a.model,
              "started": dt.datetime.now().isoformat(timespec="seconds"),
              "requests": []}
    for temp in [float(t) for t in a.temps.split(",")]:
        for rep in range(a.reps):
            for tag, p in PROMPTS:
                r = call(a.port, a.model, p, temp)
                r.update({"prompt_tag": tag, "temperature": temp,
                          "rep": rep})
                report["requests"].append(r)
                print(f"t{temp} rep{rep} {tag}: {r['tok_s']} tok/s "
                      f"({r['completion_tokens']} tok, {r['wall_s']} s)",
                      flush=True)
    tt = {}
    for temp in {r["temperature"] for r in report["requests"]}:
        vals = [r["tok_s"] for r in report["requests"]
                if r["temperature"] == temp and r["tok_s"]]
        if vals:
            tt[str(temp)] = {"mean_tok_s": round(statistics.mean(vals), 2),
                             "median_tok_s": round(statistics.median(vals),
                                                   2),
                             "n": len(vals)}
    report["tok_s_by_temp"] = tt
    # give sessions a beat to flush stats to the log
    time.sleep(2)
    report["engine_stats"] = parse_stats(a.log, since)
    tot_prop = sum(s["proposed"] for s in report["engine_stats"])
    tot_acc = sum(s["accepted_draft"] for s in report["engine_stats"])
    report["engine_pooled"] = {
        "sessions": len(report["engine_stats"]),
        "proposed": tot_prop,
        "accepted_draft": tot_acc,
        "pooled_accept_rate_pct": round(100.0 * tot_acc / tot_prop, 3)
        if tot_prop else None}
    with open(a.out, "w") as f:
        json.dump(report, f, indent=1)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
