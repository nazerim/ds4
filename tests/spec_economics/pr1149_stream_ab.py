#!/usr/bin/env python3
"""PR1149 step-2: production-stream bit-identity A/B probe.

Posts the pinned golden prompt (tests/long_context_story_prompt.txt, the
local-golden.vec case) plus the 10 battery prompts to :8002, greedy,
and banks all completion texts.  Run once against a server with NAX on
and once with DS4_QWEN4_NO_ATTN_MM_NAX=1 (classic kernel); the two
transcripts are then diffed to answer: does the production greedy stream
move when the tensor-unit attention path is enabled?

usage: uv run --no-project python pr1149_stream_ab.py <tag>
"""
import json
import pathlib
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8002/v1/completions"
PROMPTS_DIR = pathlib.Path("tests/spec_economics/prompts")
GOLDEN = pathlib.Path("tests/long_context_story_prompt.txt")


def run(prompt: str, max_tokens: int) -> dict:
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
    with urllib.request.urlopen(req, timeout=1800) as r:
        out = json.loads(r.read())
    return {
        "text": out["choices"][0]["text"],
        "completion_tokens": out.get("usage", {}).get("completion_tokens"),
        "wall_s": round(time.time() - t0, 1),
    }


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else "run"
    results = {}
    if GOLDEN.exists():
        r = run(GOLDEN.read_text(), 128)
        results["golden_long_story_4096"] = r
        print(f"golden_long_story_4096: {r['completion_tokens']} tok, "
              f"{r['wall_s']}s", flush=True)
    else:
        print(f"WARNING: {GOLDEN} missing — golden prompt skipped", flush=True)
    for p in sorted(PROMPTS_DIR.glob("*.txt")):
        r = run(p.read_text(), 400)
        results[p.stem] = r
        print(f"{p.stem}: {r['completion_tokens']} tok, {r['wall_s']}s", flush=True)
    path = f"tests/spec_economics/results/20261008_stream_ab_{tag}.json"
    with open(path, "w") as f:
        json.dump({"tag": tag, "results": results}, f)
    print(f"banked {path}")


if __name__ == "__main__":
    main()
