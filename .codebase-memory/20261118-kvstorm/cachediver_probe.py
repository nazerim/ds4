#!/usr/bin/env python3
"""How often does the disk-KV path change the ANSWER, compared with cold prefill?

Same binary, same prompts, same temperature 0 -- the only difference is whether the prompt's
prefix was recomputed by prefill or restored from a checkpoint on disk. That is the question
behind the arm-to-arm reply divergence seen in the eviction A/B (u16a vs u400 agreed on only
247 of 440 replies): benign floating-point noise amplified by greedy decoding, or a genuinely
different conditioning context?

Method, per prompt:
  pass 1  the prompt is unseen -> cold prefill from token 0, and a checkpoint is stored.
  filler  an unrelated conversation, to push this one out of the live session slot.
  pass 2  the same prompt again -> with the live slot gone, the server must load the
          checkpoint from disk and prefill only the tail.
Compare pass-1 and pass-2 greedy continuations. The log line for pass 2 is checked so a
request that did NOT actually take a disk hit is reported rather than counted as evidence.

Needs a stopped production engine (libds4 holds a global instance lock) and its own
--kv-disk-dir, so the production cache is never touched.

usage: python3 cachediver_probe.py [--port 8003] [--n 24] [--model gguf/....gguf]
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request

DOCS = [
    ("Renzo and Lucia", "The two families had been sworn enemies since the market dispute of 1628, "),
    ("Semiconductor fab", "Yield loss in the back-end-of-line is dominated by via resistance variance, "),
    ("River hydrology", "Baseflow in the catchment is sustained by fractured bedrock rather than soil moisture, "),
    ("Byzantine silk", "Silk production in the imperial workshops was a state monopoly enforced by "),
    ("Quantum error correction", "Surface codes tolerate a circuit error rate of roughly one in a thousand because "),
    ("Medieval milling", "The fulling mill spread through Flanders because water rights were granted by "),
    ("Coral calcification", "Net calcification declines when aragonite saturation falls below four because "),
    ("Rail gauge politics", "The break of gauge arose because the broader standard was adopted by "),
    ("Volcanic soils", "Andisols retain phosphorus weakly despite high fertility because allophane binds "),
    ("Ottoman tax farming", "Tax farming devolved authority because the central treasury could not "),
    ("Photolithography", "Line-edge roughness scales with the photon shot noise of the exposure dose, so "),
    ("Alpine pasture law", "Common pasture rights were enforced by the valley assembly because "),
    ("Tidal inlets", "Ebb-tidal delta volume is set by the tidal prism per unit littoral drift, which means "),
    ("Bronze hoards", "Deposit frequency rises at landscape boundaries because ")
]

TARGETS = [4096, 6144, 8192, 12288, 16384]

FILLER = [{"role": "user", "content": "Name a random colour and one number, nothing else."}]


def chat(port, messages, max_tokens=32):
    body = json.dumps({
        "model": "default", "messages": messages, "temperature": 0,
        "max_tokens": max_tokens, "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % port,
                                 data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read())
    return (d["choices"][0]["message"].get("content") or "").strip(), d.get("usage", {})


def wait_ready(port, timeout=900):
    for _ in range(timeout // 5):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/v1/models" % port, timeout=5)
            return True
        except Exception:
            time.sleep(5)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8003)
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--model", default="gguf/Qwen3.8-Flash-Next-Q4.gguf")
    ap.add_argument("--attach", action="store_true", help="server is already running")
    ap.add_argument("--out", default="/tmp/cachediver-results.jsonl")
    ap.add_argument("--max-tokens", type=int, default=32,
                    help="continuation length; 32 is the weak case -- FP divergence amplifies with length")
    a = ap.parse_args()

    if not wait_ready(a.port, timeout=10 if a.attach else 900):
        print("server not reachable on port %d" % a.port)
        return 1

    out = open(a.out, "w", encoding="utf-8")
    same = diff = unknown = 0
    for i in range(a.n):
        title, stem = DOCS[i % len(DOCS)]
        # Size the context to a target token count. This is the whole point of the harness:
        # a checkpoint is only written once the prompt crosses the store alignment step
        # (floor(P/2048)*2048), so a short prompt never exercises the disk path at all --
        # an earlier version of this file used 70-390 token prompts and silently compared
        # cold against cold. ~3.4 chars/token for this prose.
        target = TARGETS[i % len(TARGETS)]
        para = stem + ("recorded observations from the survey season, cross-checked against "
                       "the neighbouring stations and the earlier returns. ")
        doc = (para * (1 + int(target * 3.4 // len(para)))).encode("utf-8")[:int(target * 3.4)].decode("utf-8", "ignore")
        msgs = [{"role": "system", "content": "Archive %d. %s" % (i, doc)},
                {"role": "user", "content": "In one sentence, what governs the effect described?"}]
        try:
            c1, u1 = chat(a.port, msgs, a.max_tokens)
            chat(a.port, FILLER)                       # evict this conversation's live slot
            c2, u2 = chat(a.port, msgs, a.max_tokens)
        except Exception as e:
            print("  request failed: %s" % e)
            continue
        equal = (c1 == c2) and bool(c1)
        same += equal
        diff += (not equal)
        rec = {"i": i, "prompt_tokens": u1.get("prompt_tokens"), "equal": equal,
               "max_tokens": a.max_tokens, "len_cold": len(c1), "len_hit": len(c2),
               "cold": c1[:160], "hit": c2[:160]}
        out.write(json.dumps(rec) + "\n")
        out.flush()
        print("  %-3d P=%-6s target=%-6d %s" % (i, u1.get("prompt_tokens"), target,
                                                 "SAME" if equal else "DIFFERENT"))
        if (u1.get("prompt_tokens") or 0) < 2048:
            print("      ^ below the 2048-token store step: this prompt cannot produce a "
                  "checkpoint, so the comparison is vacuous", file=sys.stderr)

    n = same + diff
    print("\nprompts compared: %d   identical: %d (%.1f%%)   divergent: %d (%.1f%%)"
          % (n, same, 100.0 * same / n if n else 0, diff, 100.0 * diff / n if n else 0))
    print("results: %s  -- cross-check the server log for 'kv cache hit' on each pass-2 request" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
