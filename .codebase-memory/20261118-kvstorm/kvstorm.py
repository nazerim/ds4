#!/usr/bin/env python3
"""Deterministic multi-conversation workload for KV-cache eviction A/B runs.

Purpose: drive a ds4-server hard enough that the disk-KV evictor fires constantly
("evict-storm density") using the SAME request schedule for every arm, so the fork
and upstream evictors can be compared on identical traffic.

Design rules
  * The schedule (which conversation speaks at step i, and its user text) is fully
    precomputed from --seed and never depends on server replies.
  * Assistant turns in history are the model's real replies (temperature 0), because
    canned replies would break live-session continuity every turn and distort the
    result.  Reply hashes are logged so arms can be checked for divergence.
  * Strictly sequential (concurrency 1): log lines map to requests by timestamp.
  * Source text is speed-bench/promessi_sposi.txt; every conversation starts with a
    unique header line so conversations never share a token prefix.
Stdlib only.
"""
import argparse, hashlib, json, math, random, sys, time, urllib.request, urllib.error

ACTIVE_TARGET = 12      # conversations alive at once
P_NEW = 0.10            # chance a step starts a brand-new conversation
ZIPF_S = 1.1            # revisit skew over active conversations


def build_schedule(seed, n_requests):
    rng = random.Random(f"sched-{seed}")
    active, steps, next_id = [], [], 0
    while len(steps) < n_requests:
        if len(active) < ACTIVE_TARGET or rng.random() < P_NEW:
            c = {"id": next_id, "turn": 0, "target": rng.randint(4, 8), "hot": rng.random()}
            next_id += 1
            active.append(c)
            pick = c
        else:
            ranked = sorted(active, key=lambda c: c["hot"])
            w = [1.0 / (i + 1) ** ZIPF_S for i in range(len(ranked))]
            pick = rng.choices(ranked, weights=w, k=1)[0]
        pick["turn"] += 1
        steps.append((pick["id"], pick["turn"]))
        if pick["turn"] >= pick["target"]:
            active.remove(pick)
    return steps


def conv_material(text, seed, cid):
    r = random.Random(f"conv-{seed}-{cid}")
    doc_chars = int(min(60000, max(12000, r.lognormvariate(math.log(28000), 0.5))))
    off = r.randrange(0, len(text) - doc_chars - 1)
    doc = text[off:off + doc_chars]
    qs = []
    for _ in range(10):
        qo = r.randrange(0, len(text) - 1800)
        qs.append(text[qo:qo + r.randint(800, 1800)])
    return doc, qs


def post(url, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8003/v1/chat/completions")
    ap.add_argument("--text", default="speed-bench/promessi_sposi.txt")
    ap.add_argument("--out", required=True, help="jsonl, one record per request")
    ap.add_argument("--seed", type=int, default=20261118)
    ap.add_argument("--requests", type=int, default=240)
    ap.add_argument("--max-minutes", type=float, default=95)
    ap.add_argument("--max-tokens", type=int, default=24)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--dry-schedule", action="store_true",
                    help="print schedule statistics and exit (no server needed)")
    a = ap.parse_args()

    steps = build_schedule(a.seed, a.requests)
    convs = sorted({c for c, _ in steps})
    if a.dry_schedule:
        last, gaps = {}, []
        for i, (c, t) in enumerate(steps):
            if t > 1:
                gaps.append(i - last[c])
            last[c] = i
        gaps.sort()
        print(f"requests={len(steps)} conversations={len(convs)} "
              f"first_turns={sum(1 for _, t in steps if t == 1)}")
        print(f"revisit gap (steps) median={gaps[len(gaps)//2]} p90={gaps[int(len(gaps)*.9)]} "
              f"max={gaps[-1]} distinct_conv_in_last_12={len({c for c, _ in steps[:12]})}")
        return 0

    text = open(a.text, encoding="utf-8", errors="replace").read()
    mats, hist = {}, {}
    t_end = time.time() + a.max_minutes * 60
    fails = 0
    out = open(a.out, "w", encoding="utf-8")
    for i, (cid, turn) in enumerate(steps):
        if time.time() > t_end:
            print(f"stopping at step {i}: --max-minutes reached", file=sys.stderr)
            break
        if cid not in mats:
            mats[cid] = conv_material(text, a.seed, cid)
            doc = mats[cid][0]
            hist[cid] = [{"role": "system",
                          "content": f"Session kvstorm-{a.seed}-{cid:04d}. Reference text follows.\n\n{doc}"}]
        hist[cid].append({"role": "user",
                          "content": f"Question {turn}: consider this passage.\n\n"
                                     f"{mats[cid][1][turn-1]}\n\nReply with one short sentence."})
        body = {"model": "default", "messages": hist[cid], "temperature": 0,
                "max_tokens": a.max_tokens, "stream": False,
                "chat_template_kwargs": {"enable_thinking": False}}
        rec = {"i": i, "conv": cid, "turn": turn, "t0": time.time()}
        try:
            try:
                d = post(a.url, body, a.timeout)
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(5)
                d = post(a.url, body, a.timeout)
            rec["latency_s"] = round(time.time() - rec["t0"], 3)
            msg = d["choices"][0]["message"]
            reply = msg.get("content") or ""
            rec["empty"] = not reply.strip()
            rec["reply_sha"] = hashlib.sha1(reply.encode()).hexdigest()[:10]
            u = d.get("usage", {})
            rec["prompt_tokens"] = u.get("prompt_tokens")
            rec["completion_tokens"] = u.get("completion_tokens")
            hist[cid].append({"role": "assistant", "content": reply if reply.strip() else "OK."})
            fails = 0
        except Exception as e:  # keep the schedule moving; history stays consistent
            rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
            hist[cid].pop()          # drop the unanswered user turn
            fails += 1
        out.write(json.dumps(rec) + "\n")
        out.flush()
        if fails >= 5:
            print("5 consecutive failures; aborting", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
