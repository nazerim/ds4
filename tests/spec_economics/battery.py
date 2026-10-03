#!/usr/bin/env python3
"""Acceptance-economics driver: varied greedy completions against :8002.

Single-session mode + DS4_QWEN4_SPEC_TRACE=1 on the server makes every MTP
verify cycle log `ds4: spec pos ... accept/reject [+accept2/+reject2]`.
This script just generates the traffic; parsing is parse_spec_trace.py.
"""
import json
import time
import urllib.request

URL = "http://127.0.0.1:8002/v1/completions"

PROMPTS = [
    # coding-style (agent traffic profile)
    "Write a C function that parses a null-terminated string of comma-separated integers and returns the sum, handling malformed entries by skipping them. Then explain the edge cases.",
    "Implement a Python function that merges two sorted lists of tuples by the second element, stable, without using the sort method. Show tests.",
    "Review this snippet for bugs:\n\nint divide(int a, int b) {\n    int r = a / b;\n    return r;\n}\n\nList every problem you find, then give a corrected version.",
    "Explain how a hash table with open addressing and linear probing degrades as the load factor approaches 1, and write pseudocode for a resize operation.",
    # prose / reasoning
    "Describe the economic causes of the 1970s stagflation in three paragraphs, then summarize each paragraph in one sentence.",
    "A train leaves station A at 60 km/h. Two hours later a second train leaves the same station in the same direction at 90 km/h. How long after the second train departs does it catch the first? Show the reasoning step by step.",
    "Write a short story (about 300 words) about a lighthouse keeper who discovers the sea has begun to recede permanently, and end with a twist.",
    "Summarize the argument for and against universal basic income as a replacement for existing welfare programs. Give the three strongest points on each side.",
    # mixed / tool-like
    "Convert the following JSON schema to a TypeScript interface and point out which fields are optional:\n{\"type\":\"object\",\"properties\":{\"id\":{\"type\":\"string\"},\"count\":{\"type\":\"integer\"}},\"required\":[\"id\"]}",
    "List the first 20 prime numbers, then compute their sum, then factor the sum.",
]


def run(prompt: str, max_tokens: int = 400, sink: list | None = None) -> None:
    body = json.dumps({
        "model": "qwen",
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.loads(r.read())
    dt = time.time() - t0
    usage = out.get("usage", {})
    print(f"prompt[:40]={prompt[:40]!r} wall={dt:.1f}s "
          f"completion_tokens={usage.get('completion_tokens')}")
    if sink is not None:
        sink.append(out["choices"][0]["text"])


def main() -> None:
    import sys
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"== acceptance run start {time.strftime('%H:%M:%S')} ==")
    texts = []
    t0 = time.time()
    for p in PROMPTS:
        run(p, sink=texts)
    total = time.time() - t0
    print(f"== acceptance run done {time.strftime('%H:%M:%S')} wall={total:.1f}s ==")
    if out_path:
        json.dump({"wall_s": round(total, 1), "texts": texts},
                  open(out_path, "w"))


if __name__ == "__main__":
    main()
