#!/usr/bin/env python3
"""Cold-prefill throughput probe against :8002 (QSA widening attribution).

Builds nonce-prefixed prompts of a target token size (fresh text per run so
the KV disk/session cache never replays), requests exactly one completion
token, and reports TTFT-implied prefill tok/s.  Pair with server-side
DS4_QWEN4_TIMING=2 log lines (stage buckets per 8192-token chunk) for the
where-does-it-go decomposition.

Usage: prefill_probe.py <tokens> <reps> [target2 <tokens2> <reps2> ...]
  e.g. prefill_probe.py 16384 2 65536 2
"""
import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8002/v1/completions"

# ~11-word filler line; scaled to hit the target token count (~1.35 tok/word
# with the line's separators; usage.prompt_tokens is the ground truth).
FILLER = ("market quarterly growth inflation reserve policy tariff supply chain "
          "bond yield freight grain energy copper futures trading desk " * 4)


def make_prompt(target_tokens: int, nonce: str) -> str:
    approx_words = int(target_tokens / 1.3)
    one_line_words = len(FILLER.split())
    lines = approx_words // one_line_words + 2
    body = (FILLER + "\n") * lines
    return f"[unique-{nonce}] Answer with one word.\n{body}"


def run(target_tokens: int, nonce: str) -> None:
    prompt = make_prompt(target_tokens, nonce)
    body = json.dumps({
        "model": "qwen",
        "prompt": prompt,
        "max_tokens": 1,
        "temperature": 0.0,
        "stream": False,
    }).encode()
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        out = json.loads(r.read())
    wall = time.time() - t0
    pt = out.get("usage", {}).get("prompt_tokens", 0)
    print(f"nonce={nonce} target={target_tokens} prompt_tokens={pt} "
          f"ttft={wall:.2f}s prefill_tok_s={pt / wall:.1f}")


def run_text(target_tokens: int, nonce: str, out_path: str) -> None:
    """Greedy 48-token continuation of a long cold prompt: the sparse-regime
    text oracle (bit-identical texts across prefill-shape configs == the
    wave/dispatch reshaping moved no committed token)."""
    prompt = make_prompt(target_tokens, nonce)
    body = json.dumps({"model": "qwen", "prompt": prompt, "max_tokens": 48,
                       "temperature": 0.0, "stream": False}).encode()
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        out = json.loads(r.read())
    text = out["choices"][0]["text"]
    with open(out_path, "w") as f:
        json.dump({"prompt_tokens": out.get("usage", {}).get("prompt_tokens"),
                   "text": text}, f)
    print(f"text saved -> {out_path} (prompt_tokens="
          f"{out.get('usage', {}).get('prompt_tokens')})")


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "text":
        # fixed nonce (args[3]) so the SAME prompt replays across engines --
        # that is what makes cross-config text comparison meaningful
        nonce = args[3] if len(args) > 3 else f"t{int(time.time())}"
        run_text(int(args[1]), nonce, args[2])
        return 0
    for i in range(0, len(args), 2):
        tokens = int(args[i])
        reps = int(args[i + 1])
        for rep in range(reps):
            run(tokens, f"{int(time.time())}-{tokens}-{rep}")
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
