#!/usr/bin/env python3
"""Slot-ordering probe: run a chosen sequence of battery prompts against
:8002 and record every completion text, in order.

battery.py runs the fixed 10-prompt sequence; this tool runs an arbitrary
subset/order so we can bisect how earlier requests in the same engine/slot
affect a later prompt's stream (the prompt-3@884 per-row residual,
.codebase-memory/QWEN4-VERIFY-IDENTITY-20261003.md).

Usage: order_probe.py <out.json> <idx[,idx...]>   # indices into battery.PROMPTS
"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from battery import PROMPTS  # noqa: E402

URL = "http://127.0.0.1:8002/v1/completions"


def run(prompt: str, sink: list) -> None:
    body = json.dumps({
        "model": "qwen",
        "prompt": prompt,
        "max_tokens": 400,
        "temperature": 0.0,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.loads(r.read())
    usage = out.get("usage", {})
    print(f"wall={time.time() - t0:.1f}s tok={usage.get('completion_tokens')}")
    sink.append(out["choices"][0]["text"])


def main() -> None:
    out_path = sys.argv[1]
    idxs = [int(x) for x in sys.argv[2].split(",")]
    texts = []
    t0 = time.time()
    for i in idxs:
        print(f"== prompt {i} ==")
        run(PROMPTS[i], texts)
    total = time.time() - t0
    json.dump({"wall_s": round(total, 1), "order": idxs, "texts": texts},
              open(out_path, "w"))
    print(f"== done wall={total:.1f}s -> {out_path} ==")


if __name__ == "__main__":
    main()
