#!/usr/bin/env python3
"""S6 AB bench client for the DSpark conf-head A/B (protocol:
.codebase-memory/AB-PROTOCOL-3C-20261104.md). Night 20261104, REWORKED
per independent review (verdict REWORK): engine is 8001 (DSpark collect),
stats lines windowed by BYTE OFFSET (log ts prefix is 'MMDD HH:MM:SS',
no year - a date regex silently matches nothing), sessions==0 is a hard
error, p95 + finish_reason recorded, accept-len histograms parsed to
ints, model id discovered from /v1/models, tool-bearing prompt included.
Stdlib only. NOT YET EXECUTED against the live engine.

Usage:
  python3 tools/bench_ab_3c.py --arm B --out armB.json
"""
import argparse
import hashlib
import json
import re
import statistics
import sys
import time
import urllib.request

# Fixed prompt set: mix of code, prose, arithmetic, retrieval, and ONE
# tool-bearing request (the collect engine serves agent/tool traffic; the
# conf head was calibrated on thinking/tool-agent cycles - a plain-chat
# only set could drive ~zero draft cycles, review MAJOR 4).
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
TOOL_PROMPTS = [
    ("tool", "What is 341 * 592? You MUST call the calculator tool.",
     [{"type": "function", "function": {
         "name": "calculator",
         "description": "Evaluate an arithmetic expression",
         "parameters": {"type": "object", "properties": {
             "expr": {"type": "string"}}, "required": ["expr"]}}}]),
]

STATS_RE = re.compile(
    r"ds4: DSpark stats cycles=(\d+) first_tokens=(\d+) proposed=(\d+) "
    r"accepted_draft=(\d+) accept_rate=([\d.]+)% avg_accept=([\d.]+)")

# --- BIG-leg (v4) ------------------------------------------------------------
# Verdict leg: grow a session past >=32k tokens with DETERMINISTIC content,
# replay the identical turn script on both arms at t=0, hash completions per
# turn; scorer pairs by (seed, turn), DROPS pairs whose content sha diverges
# (paired-t0 rests on greedy+exact-sampling determinism - CHECKED, not
# assumed: ds4.c:88032, v3 precedent 12/12), then scores tok/s + prompt
# growth over the surviving pairs. Dev-run ONLY against a boot with
# DS4_TRAIN_DUMP AND DS4_TD_PARITY REDIRECTED to /tmp dirs - absent env
# RE-ARMS the real corpus via the script's ${VAR:-real} defaults;
# DS4_TD_PARITY_MAX=0 does not disable (coerces to 4096). Synthetic
# cycles must never touch the real corpus.
BIG_SEED_FILES = ["ds4.c", "ds4_server.c", "AGENT.md", "README.md"]
BIG_SEED_CHARS = (200_000, 260_000)        # ~50k-65k tokens per seed
BIG_TURNS = 6                               # tool turns after the seed ask
BIG_TOOL_SCHEMA = [{
    "type": "function", "function": {
        "name": "lookup_context",
        "description": "Fetch a chunk of previously provided context",
        "parameters": {"type": "object", "properties": {
            "chunk": {"type": "integer"}}, "required": ["chunk"]}}}]


def big_seed_text(n_chars):
    """Deterministic seed corpus: fixed manifest concatenated, then
    sha-extended to exactly n_chars (same bytes on every run/arm)."""
    buf = []
    total = 0
    for name in BIG_SEED_FILES:
        try:
            data = open(name, "r", errors="replace").read()
        except OSError:
            continue
        buf.append(f"\n==== FILE {name} ====\n" + data)
        total += len(buf[-1])
        if total >= n_chars:
            break
    text = "".join(buf)
    while len(text) < n_chars:
        h = hashlib.sha256((text[-4096:] + str(len(text))).encode()).hexdigest()
        text += h * 64
    return text[:n_chars]


def big_tool_result(seed_i, turn):
    """Deterministic ~4 KB canned tool result (identical across arms)."""
    parts, s = [], f"seed{seed_i}-turn{turn}"
    while len("".join(parts)) < 4096:
        s = hashlib.sha256(s.encode()).hexdigest()
        parts.append(s)
    return "".join(parts)[:4096]
# REAL engine format (ds4_format_len_hist, ds4.c:75285-75300): "k:count"
# PAIRS with zero bins skipped, e.g. accepted_len_hist=1:900,2:450,3:120,
# 4:40,5:12 - a [0-9,] class truncates at the first ':' and captures bin
# INDICES (S6 re-check MAJOR; v1 synthetic comma-only exemplar hid it).
# Bins 0..16 (DS4_DSPARK_MAX_BLOCK_SIZE=16).
ACCEPT_HIST_RE = re.compile(r"accepted_len_hist=([0-9:,\s]+)")
DRAFT_HIST_RE = re.compile(r"draft_len_hist=([0-9:,\s]+)")
N_HIST_BINS = 17


def parse_len_hist(s):
    """'k:v,...' pair format -> 17-bin list; tolerates legacy comma-only
    positional lists. Unparseable garbage returns None (loud upstream)."""
    bins = [0] * N_HIST_BINS
    seen = False
    for j, part in enumerate(s.strip().split(",")):
        part = part.strip()
        if not part:
            continue
        try:
            if ":" in part:
                k, v = part.split(":", 1)
                ki, vi = int(k), int(v)
            else:                       # legacy positional
                ki, vi = j, int(part)
        except ValueError:
            return None
        if 0 <= ki < N_HIST_BINS:
            bins[ki] += vi
            seen = True
    return bins if seen else None


def http_json(port, path, body=None, timeout=600):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def discover_model(port):
    """Model id from /v1/models (server returns ds4_engine_model_name;
    a hardcoded id risks silently hitting the wrong profile)."""
    d = http_json(port, "/v1/models")
    ids = [m.get("id") for m in d.get("data", []) if m.get("id")]
    if not ids:
        raise SystemExit(f"/v1/models returned no ids: {d}")
    return ids


def call(port, model, prompt, temperature, tools=None, max_tokens=256):
    body = {"model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature, "max_tokens": max_tokens,
            "stream": False}
    if tools:
        body["tools"] = tools
    t0 = time.perf_counter()
    resp = http_json(port, "/v1/chat/completions", body)
    wall = time.perf_counter() - t0
    usage = resp.get("usage") or {}
    ctok = int(usage.get("completion_tokens", 0))
    choice = (resp.get("choices") or [{}])[0]
    return {"wall_s": round(wall, 3), "completion_tokens": ctok,
            "prompt_tokens": int(usage.get("prompt_tokens", 0)),
            "finish_reason": choice.get("finish_reason"),
            "tok_s": round(ctok / wall, 2) if wall > 0 else None}


def parse_stats(log_path, start_offset, allow_empty=False):
    """Engine stats lines from BYTE OFFSET (recorded at bench start) to
    EOF. Byte-offset windowing is arm isolation by construction: boot
    truncates the log (size shrinks) - detected and refused.
    CORRECTED 20261105: the session-flush point is ds4_session_free
    (POOL EVICT / SHUTDOWN, ds4.c:76419+76445) - an arm's aggregate
    stats line lands in ITS boot-era log at the NEXT boot's rotation
    boundary, NOT during live benching. The night assumption of
    per-request flush (76362) matched the then-code, not this build.
    Pass allow_empty=True during a boot era and re-parse the rotated
    era log afterwards (parse_stats with --skip-requests --offset 0)."""
    size = __import__("os").path.getsize(log_path)
    if size < start_offset:
        raise SystemExit(f"log shrank ({start_offset}->{size}): engine "
                         "restarted mid-window; results invalid")
    out = []
    with open(log_path, "r", errors="replace") as f:
        f.seek(start_offset)
        for line in f:
            m = STATS_RE.search(line)
            if not m:
                continue
            cy, _ft, prop, accd, arate, aacc = m.groups()
            ah = ACCEPT_HIST_RE.search(line)
            dh = DRAFT_HIST_RE.search(line)
            alh = parse_len_hist(ah.group(1)) if ah else None
            dlh = parse_len_hist(dh.group(1)) if dh else None
            if ah and alh is None:
                raise SystemExit(f"unparseable accepted_len_hist: "
                                 f"{ah.group(1)!r} - check engine format")
            if dh and dlh is None:
                raise SystemExit(f"unparseable draft_len_hist: "
                                 f"{dh.group(1)!r} - check engine format")
            out.append({"cycles": int(cy), "proposed": int(prop),
                        "accepted_draft": int(accd),
                        "accept_rate_pct": float(arate),
                        "avg_accept": float(aacc),
                        "accept_len_hist": alh,
                        "draft_len_hist": dlh})
    if not out:
        if allow_empty:
            return []
        raise SystemExit(
            "ZERO 'ds4: DSpark stats' lines in window: is the engine "
            "booted with DS4_DSPARK_STATS=1? (never set historically - "
            "0 such lines exist in log history). Do NOT proceed to "
            "scoring without >=1 line with cycles>0.")
    if not any(s["cycles"] > 0 for s in out):
        raise SystemExit("stats lines exist but all cycles==0: the prompt "
                         "set produced no draft cycles (tool-traffic "
                         "gate?). Add tool-bearing load before scoring.")
    return out


def http_json_timed(port, path, body=None, timeout=1200):
    """Returns (json, ttfb_s): time-to-first-byte approximates server work
    completion for non-streaming POSTs; used for first_token_s in BIG leg."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        ttfb = time.perf_counter() - t0
    return json.loads(raw.decode()), ttfb


def call_messages(port, model, messages, temperature, max_tokens=200,
                  tools=None):
    body = {"model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
            "tools": tools if tools is not None else BIG_TOOL_SCHEMA,
            "stream": False}
    t0 = time.perf_counter()
    resp, ttfb = http_json_timed(port, "/v1/chat/completions", body)
    wall = time.perf_counter() - t0
    usage = resp.get("usage") or {}
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    calls = msg.get("tool_calls") or []
    tc_blob = json.dumps([{"name": (c.get("function") or {}).get("name"),
                           "args": (c.get("function") or {}).get("arguments")}
                          for c in calls], sort_keys=True)
    ctok = int(usage.get("completion_tokens", 0))
    return {"wall_s": round(wall, 3), "first_token_s": round(ttfb, 3),
            "completion_tokens": ctok,
            "prompt_tokens": int(usage.get("prompt_tokens", 0)),
            "finish_reason": choice.get("finish_reason"),
            "content_sha256": hashlib.sha256(
                (content + tc_blob).encode()).hexdigest()[:16],
            "tok_s": round(ctok / wall, 2) if wall > 0 else None}


def run_big_leg(port, model, temps):
    """Turn script per seed: [seed-ask] then N tool-call turns; history is
    FULLY CANNED (user-role growth only, canned assistant echo - no
    role=tool id protocol) so identical bytes reach both arms and any
    per-turn divergence isolates to that turn's pair (scorer drops and
    flags divergent pairs before touching tok/s)."""
    rows = []
    for seed_i, n_chars in enumerate(BIG_SEED_CHARS):
        text = big_seed_text(n_chars)
        for temp in temps:
            msgs = [{"role": "user",
                     "content": "This is a long reference document. Reply "
                                "with a one-line acknowledgement only.\n\n"
                                + text}]
            first = call_messages(port, model, msgs, temp, max_tokens=64)
            rows.append(dict(seed=seed_i, temperature=temp, turn=0,
                             crossed_32k=first["prompt_tokens"] >= 32768,
                             **first))
            print(f"seed{seed_i} t{temp} turn0: "
                  f"prompt={first['prompt_tokens']} {first['tok_s']} tok/s",
                  flush=True)
            for turn in range(1, BIG_TURNS + 1):
                if turn == 1:
                    msgs.append({"role": "assistant",
                                 "content": "Acknowledged."})
                else:
                    # REAL tool-history shape (assistant tool_calls +
                    # tool-role result), all canned => arm-independent.
                    # (v4 smoke finding: prose assistant echoes taught the
                    # model to stop calling tools from turn 2 on.)
                    msgs.append({
                        "role": "assistant", "content": "",
                        "tool_calls": [{
                            "id": f"call_smoke{seed_i}_{turn - 1}",
                            "type": "function",
                            "function": {
                                "name": "lookup_context",
                                "arguments": json.dumps(
                                    {"chunk": turn - 1})}}]})
                    msgs.append({"role": "tool",
                                 "tool_call_id":
                                     f"call_smoke{seed_i}_{turn - 1}",
                                 "name": "lookup_context",
                                 "content": big_tool_result(
                                     seed_i, turn - 1)})
                msgs.append({"role": "user",
                             "content": "Call lookup_context with chunk="
                                        f"{turn} immediately. Do not "
                                        "answer in prose."})
                r = call_messages(port, model, msgs, temp, max_tokens=300)
                r.update({"crossed_32k": r["prompt_tokens"] >= 32768})
                rows.append(dict(seed=seed_i, temperature=temp,
                                 turn=turn, **r))
                print(f"seed{seed_i} t{temp} turn{turn}: "
                      f"prompt={r['prompt_tokens']} {r['tok_s']} tok/s "
                      f"finish={r['finish_reason']}", flush=True)
    return rows


def run_replay(port, model, directory, temps):
    """v5 verdict leg: replay recorded REAL conversation prefixes
    (/tmp corpus built by build_corpus.py; masked ids, secrets-screened,
    600 perms). Same pairing keys as big_leg; scorer consumes 'big_leg'."""
    import glob
    import os
    rows = []
    files = sorted(glob.glob(os.path.join(directory, "conv_*.json")))
    if not files:
        raise SystemExit(f"--replay: no conv_*.json under {directory}")
    for fp in files:
        conv = json.load(open(fp))
        ci = conv["conversation"]
        for temp in temps:
            for k, prefix in enumerate(conv["checkpoints"]):
                r = call_messages(port, model, prefix, temp,
                                  max_tokens=300, tools=conv["tools"])
                r.update({"crossed_32k": r["prompt_tokens"] >= 32768})
                rows.append(dict(seed=ci, temperature=temp, turn=k, **r))
                print(f"conv{ci} t{temp} ckpt{k}: prompt={r['prompt_tokens']} "
                      f"ftok={r['first_token_s']}s "
                      f"finish={r['finish_reason']}", flush=True)
    return rows


def hist_add(hists):
    tot = [0] * N_HIST_BINS
    for h in hists:
        if h:
            for i, v in enumerate(h[:N_HIST_BINS]):
                tot[i] += v
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001,
                    help="DSpark collect engine is 8001; 8002 (qwen) has "
                         "NO DSpark -> no stats ever")
    ap.add_argument("--model", default=None,
                    help="default: discover via /v1/models")
    ap.add_argument("--arm", default="X", help="free-form arm tag")
    ap.add_argument("--log", default="log/ds4.log")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--temps", default="0,1")
    ap.add_argument("--skip-requests", action="store_true",
                    help="stats-parse only (from --offset)")
    ap.add_argument("--allow-no-stats", action="store_true",
                    help="valid mid-boot-era: engine flushes DSpark stats "
                         "at session free (pool evict/shutdown) - the "
                         "era's line appears only in the ROTATED log "
                         "after the next boot; request-side results still "
                         "worth recording now")
    ap.add_argument("--offset", type=int, default=0,
                    help="byte offset for --skip-requests mode")
    ap.add_argument("--big-leg", action="store_true",
                    help="v4 verdict leg ONLY: canned growing-context "
                         "tool-turn sessions at >=32k (run from repo "
                         "root - seed manifest reads ./ds4.c etc.); "
                         "pairing/hashing per run_big_leg docstring")
    ap.add_argument("--replay", default=None, metavar="DIR",
                    help="v5 verdict leg: replay REAL recorded "
                         "conversation prefixes from /tmp corpus DIR "
                         "(masked, secret-screened); emits 'big_leg' "
                         "rows so score_ab_v4 consumes it unchanged")
    ap.add_argument("--big-temps", default="0,1",
                    help="temperatures for --big-leg (t=0 is the "
                         "verdict core, t=1 directional)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    report = {"arm": a.arm, "port": a.port,
              "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "requests": []}

    if a.skip_requests:
        offset = a.offset
    else:
        ids = discover_model(a.port)
        model = a.model or ids[0]
        report["model"] = model
        report["model_ids_seen"] = ids
        import os
        offset = os.path.getsize(a.log)   # byte window starts NOW
        report["log_offset"] = offset
        if a.replay:
            report["big_leg"] = run_replay(
                a.port, model, a.replay,
                [float(t) for t in a.big_temps.split(",")])
            time.sleep(3)
        elif a.big_leg:
            report["big_leg"] = run_big_leg(
                a.port, model, [float(t) for t in a.big_temps.split(",")])
            time.sleep(3)
        else:
            for temp in [float(t) for t in a.temps.split(",")]:
                for rep in range(a.reps):
                    for tag, p in PROMPTS:
                        r = call(a.port, model, p, temp)
                        r.update({"prompt_tag": tag, "temperature": temp,
                                  "rep": rep})
                        report["requests"].append(r)
                        print(f"t{temp} rep{rep} {tag}: {r['tok_s']} tok/s "
                              f"({r['completion_tokens']} tok, "
                              f"finish={r['finish_reason']})", flush=True)
                    for tag, p, tools in TOOL_PROMPTS:
                        r = call(a.port, model, p, temp, tools=tools)
                        r.update({"prompt_tag": tag, "temperature": temp,
                                  "rep": rep, "tools": True})
                        report["requests"].append(r)
                        print(f"t{temp} rep{rep} {tag}: {r['tok_s']} tok/s "
                              f"({r['completion_tokens']} tok, "
                              f"finish={r['finish_reason']})", flush=True)
        # sessions flush at request end; a beat covers in-flight ones
        time.sleep(3)

    tt = {}
    for temp in sorted({r["temperature"] for r in report["requests"]}):
        vals = sorted(r["tok_s"] for r in report["requests"]
                      if r["temperature"] == temp and r["tok_s"])
        if vals:
            n = len(vals)
            tt[str(temp)] = {
                "mean_tok_s": round(statistics.mean(vals), 2),
                "median_tok_s": round(statistics.median(vals), 2),
                "p95_tok_s": (vals[min(n - 1, round(0.95 * (n - 1)))]
                              if n > 1 else vals[0]),
                "n": n,
                "note": "DIRECTIONAL ONLY under live traffic (review "
                        "MAJOR 2); n small, wall time includes queue"}
    report["tok_s_by_temp"] = tt
    report["finish_reasons"] = {
        fr: sum(1 for r in report["requests"]
                if r["finish_reason"] == fr)
        for fr in {r["finish_reason"] for r in report["requests"]}}

    report["engine_stats"] = parse_stats(a.log, offset,
                                         allow_empty=(a.allow_no_stats
                                                      or a.big_leg
                                                      or a.replay))
    tot_prop = sum(s["proposed"] for s in report["engine_stats"])
    tot_acc = sum(s["accepted_draft"] for s in report["engine_stats"])
    report["engine_pooled"] = {
        "sessions": len(report["engine_stats"]),
        "cycles": sum(s["cycles"] for s in report["engine_stats"]),
        "proposed": tot_prop,
        "accepted_draft": tot_acc,
        "pooled_accept_rate_pct": round(100.0 * tot_acc / tot_prop, 3)
        if tot_prop else None,
        "accept_len_hist": hist_add(
            [s["accept_len_hist"] for s in report["engine_stats"]]),
        "draft_len_hist": hist_add(
            [s["draft_len_hist"] for s in report["engine_stats"]])}
    with open(a.out, "w") as f:
        json.dump(report, f, indent=1)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
