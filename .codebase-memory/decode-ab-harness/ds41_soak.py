#!/usr/bin/env python3
"""V4.1 decode-degeneration soak harness (deterministic mixed agentic traffic).

Replays the profile measured in DS41-SMOKE-20261106: cold ~40k-token mixed
prefill, then N turns of {tool-result appends 300-1500 tok (code/JSON/CJK mix)
+ thinking decode}. Seeded -> byte-identical traffic across config A/B legs.

Usage: soak.py <legname> [turns]
Output: /tmp/ds41_soak_<legname>.jsonl  (per-turn rows) + stdout curve.
"""
import json, random, sys, time, urllib.request, uuid

BASE = "http://127.0.0.1:8003/v1/chat/completions"
RNG = random.Random(20261108)
SYS = ("You are a coding agent. Use tools when asked. Reason briefly before "
       "each tool call.")

import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ds41_speed as S  # corpus(chars, kind) helpers

def piece(kind, ntok):
    cpt = {"code": 3.2, "json": 1.9, "prose": 4.5, "cjk": 1.0}[kind]
    return S.corpus(int(ntok * cpt), kind=kind)

def post(messages, think, max_tokens):
    body = json.dumps({"model": "x", "think": think, "max_tokens": max_tokens,
                       "temperature": 0.3,
                       "messages": [{"role": "system", "content": SYS}] + messages,
                       }).encode()
    req = urllib.request.Request(BASE, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    d = json.load(urllib.request.urlopen(req, timeout=3600))
    wall = time.time() - t0
    u = d.get("usage", {})
    ch = d["choices"][0]["message"]
    out = u.get("completion_tokens", len((ch.get("content") or "").split()))
    return {"wall_s": round(wall, 1), "prompt_tokens": u.get("prompt_tokens", 0),
            "completion_tokens": out, "cached": u.get("prompt_tokens_details", {}).get("cached_tokens", 0)}

def main():
    leg = sys.argv[1] if len(sys.argv) > 1 else "base"
    turns = int(sys.argv[2]) if len(sys.argv) > 2 else 24
    log = open(f"/tmp/ds41_soak_{leg}.jsonl", "w")
    # Cold start: 40k mixed prefill.
    cold = " ".join([piece("prose", 12000), piece("code", 10000), piece("json", 8000),
                     "SOAKCOLD-%d Question: when I later ask, repeat the word OTTER." % 20261108])
    msgs = [{"role": "user", "content": cold}]
    r = post(msgs, think=False, max_tokens=8)
    r.update(turn=0, phase="cold"); log.write(json.dumps(r) + "\n"); log.flush()
    print(0, r, flush=True)
    kinds = ["code", "json", "cjk", "prose"]
    for t in range(1, turns + 1):
        k1, k2 = kinds[t % 4], kinds[(t * 7 + 3) % 4]
        app = 300 + RNG.randrange(0, 1200)
        tool = ("SOAKTOOL-%d-%d\n" % (20261108, t) + piece(k1, app // 2) + "\n" + piece(k2, app - app // 2))
        msgs.append({"role": "assistant", "content": "Reading the tool output and deciding next steps."})
        msgs.append({"role": "user", "content": "Tool result for step %d:\n%s\nQuestion: what token was embedded for this step? Answer in a short sentence after brief thinking. Also count up to %d." % (t, tool, 50 + t)})
        r = post(msgs, think=True, max_tokens=400 + RNG.randrange(0, 500))
        r.update(turn=t, appended_tokens=app, kinds=k1 + "+" + k2)
        log.write(json.dumps(r) + "\n"); log.flush()
        print(t, r, flush=True)
    # Final: recall probe at full context.
    msgs.append({"role": "assistant", "content": "Finalizing."})
    msgs.append({"role": "user", "content": "Repeat the embedded question token now, one word."})
    r = post(msgs, think=False, max_tokens=8)
    r.update(turn=turns + 1, phase="recall"); log.write(json.dumps(r) + "\n")
    print("RECALL", r, flush=True)
    log.close(); print("SOAK DONE", leg, flush=True)

main()
