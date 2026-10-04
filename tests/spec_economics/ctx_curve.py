#!/usr/bin/env python3
"""Decode throughput vs context depth against :8002.

For each target ctx size, sends a fresh nonce-prefixed filler prompt sized
so (prompt + max_tokens) lands near the target, generates N greedy tokens,
and reports total and decode-phase rates.  Decode t/s is read from the
server's own chunk lines (log passed via --log) attributed to the newest
completion after the probe's marker time.

Usage: ctx_curve.py [--log log/ds4-qwen.log] 32768 65536 131072 ...
"""
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from prefill_probe import FILLER  # noqa: E402

URL = "http://127.0.0.1:8002/v1/completions"
GEN = 64


def probe(target: int, nonce: str) -> tuple:
    approx_words = int((target - GEN) / 1.3)
    lines = approx_words // len(FILLER.split()) + 2
    prompt = f"[unique-{nonce}] Answer briefly.\n" + (FILLER + "\n") * lines
    body = __import__("json").dumps({"model": "qwen", "prompt": prompt,
                                     "max_tokens": GEN, "temperature": 0.0,
                                     "stream": False}).encode()
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=3600) as r:
        out = __import__("json").loads(r.read())
    wall = time.time() - t0
    u = out.get("usage", {})
    pt = u.get("prompt_tokens", 0)
    ct = u.get("completion_tokens", GEN)
    return pt, ct, wall


def decode_rate(log_path: str, since: float) -> list:
    """Chunk t/s lines from the server log newer than the epoch marker."""
    out = subprocess.run(["tail", "-n", "200", log_path], capture_output=True,
                         text=True).stdout
    return [float(m.group(2)) for m in re.finditer(
        r"decoding chunk=([0-9.]+) t/s|decoding chunk=[0-9.]+ t/s avg=([0-9.]+)", out)]


def main() -> int:
    log = "log/ds4-qwen.log"
    args = sys.argv[1:]
    if args[:1] == ["--log"]:
        log, args = args[1], args[2:]
    for a in args:
        target = int(a)
        pt, ct, wall = probe(target, f"cc{int(time.time())}")
        prefill_est = pt / 1350.0
        decode_est = (ct / 2.2) * 0.0225  # rough cycles; real rate from log
        print(f"target={target} prompt_tokens={pt} gen={ct} wall={wall:.2f}s "
              f"e2e_tok_s={(pt + ct) / wall:.1f} (prefill share ~{prefill_est:.1f}s)")
        tail = subprocess.run(["tail", "-n", "30", log], capture_output=True,
                              text=True).stdout
        rates = re.findall(r"decoding chunk=([0-9.]+) t/s avg=([0-9.]+)", tail)
        if rates:
            last = rates[-1]
            print(f"  log decode @ctx~{pt}: chunk={last[0]} t/s avg={last[1]} t/s")
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
