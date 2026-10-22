#!/usr/bin/env python3
"""Decode throughput probe against :8002 — cold nonced prompts, avg completion tok/s.

Usage: decode_probe.py [max_tokens] [reps]
"""
import json
import random
import sys
import time
import urllib.request

MT = int(sys.argv[1]) if len(sys.argv) > 1 else 400
REPS = int(sys.argv[2]) if len(sys.argv) > 2 else 3

rates = []
for r in range(REPS):
    n = random.randrange(100000, 999999)
    prompt = (f"Write the decimal digits of the number {n} one per line, "
              f"then count upward from {n % 500 + 7} digit by digit and do "
              f"not stop early.")
    body = {"model": "qwen", "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MT, "temperature": 0.0, "stream": False,
            "think": False}
    req = urllib.request.Request("http://127.0.0.1:8002/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as resp:
        out = json.loads(resp.read().decode())
    wall = time.time() - t0
    ch = out["choices"][0]
    toks = out.get("usage", {}).get("completion_tokens", 0)
    rate = toks / wall
    rates.append(rate)
    print(f"  rep {r}: completion_tok={toks} wall={wall:.1f}s "
          f"tok/s={rate:.1f} finish={ch.get('finish_reason')}", flush=True)
print(f"DECODE avg: {sum(rates)/len(rates):.1f} tok/s over {REPS} reps")
