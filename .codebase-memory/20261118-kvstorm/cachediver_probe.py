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

PARA = ("recorded observations from the survey season, cross-checked against the neighbouring "
        "stations and the earlier returns. ")

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

import os as _os
# Overridable so the same harness can be pushed to the prompt sizes where the eviction arms
# actually diverged (they ran to 30k+ tokens; the default tops out at 16k).
CPT = None   # chars per token; MEASURED at startup, never assumed (see calibrate)
UNIQ = ["baseline"]
TARGETS = [int(x) for x in _os.environ.get("CACHEDIVER_TARGETS", "4096,6144,8192,12288,16384").split(",")]

FILLER = [{"role": "user", "content": "Name a random colour and one number, nothing else."}]



def calibrate(port, max_tokens=8):
    """Measure chars/token for THIS corpus instead of assuming a language-modeling average.

    Two earlier versions of this harness hard-coded 3.4 and then 2.0 and silently tested
    roughly half and a quarter of the intended prompt size; this corpus is heavily repeated
    prose, which BPE compresses to ~6 chars/token, so no generic constant is safe.
    """
    probe_text = PARA * 40                      # ~16k chars
    msgs = [{"role": "system", "content": "Calibration. " + probe_text},
            {"role": "user", "content": "Reply with the single word: ok"}]
    _, usage = chat(port, msgs, max_tokens)
    pt = usage.get("prompt_tokens") or 0
    if pt <= 0:
        raise SystemExit("calibration failed: no prompt_tokens returned")
    return len(probe_text) / float(pt)


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


def run_multiturn(port, nturns, doc, max_tokens):
    """Walk a conversation turn by turn, then replay it after the live slot is gone.

    Pass 1 exercises the LIVE-SESSION path (each turn extends the resident session, so
    almost nothing is re-read from disk). Pass 2, after fillers have evicted that session,
    must resume from an on-disk checkpoint. So this compares exactly the two paths whose
    outputs diverged between the eviction arms -- single-turn cold-vs-hit does not.

    UNIQ[0] tags the system message so a preflight run sharing this engine cannot seed
    checkpoints that the measured run would then load.
    """
    hist = [{"role": "system", "content": "[%s] Archive MT. %s" % (UNIQ[0], doc)}]
    outs = []
    for k in range(nturns):
        hist.append({"role": "user", "content":
                     "Follow-up %d: name the specific mechanism in this archive that would most "
                     "change the outcome, and explain it in about sixty words." % (k + 1)})
        reply, usage = chat(port, hist, max_tokens)
        outs.append({"turn": k + 1, "prompt_tokens": usage.get("prompt_tokens"),
                     "completion": usage.get("completion_tokens"), "text": reply})
        hist.append({"role": "assistant", "content": reply})
    return outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8003)
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--model", default="gguf/Qwen3.8-Flash-Next-Q4.gguf")
    ap.add_argument("--attach", action="store_true", help="server is already running")
    ap.add_argument("--out", default="/tmp/cachediver-results.jsonl")
    ap.add_argument("--unique-prefix", default="baseline",
                    help="tag embedded in every system message. A preflight run and the real "
                         "run share one engine and one KV dir, so without distinct tags the "
                         "preflight stores checkpoints the real run then loads -- which made an "
                         "earlier version of this harness compare resume-at-depth-10240 against "
                         "resume-at-depth-8192 while believing pass 1 was cold.")
    ap.add_argument("--turns", type=int, default=0,
                    help="if >0, run a multi-turn conversation twice (live-continue vs disk-resume)")
    ap.add_argument("--max-tokens", type=int, default=32,
                    help="continuation length; 32 is the weak case -- FP divergence amplifies with length")
    a = ap.parse_args()

    if not wait_ready(a.port, timeout=10 if a.attach else 900):
        print("server not reachable on port %d" % a.port)
        return 1

    global CPT, UNIQ
    UNIQ[0] = a.unique_prefix
    if CPT is None:
        CPT = calibrate(a.port)
        print("measured corpus ratio: %.2f chars/token" % CPT, file=sys.stderr)
    else:
        CPT = float(CPT)

    out = open(a.out, "w", encoding="utf-8")
    same = diff = unknown = 0

    if a.turns > 0:
        para = PARA
        doc = (para * 900)[:16000]
        total = bad = 0
        for rep in range(3):                       # a few conversations is all this can afford
            p1 = run_multiturn(a.port, a.turns, doc + " Variant %d." % rep, a.max_tokens)
            for _ in range(3):
                chat(a.port, FILLER)               # evict the live session slot
            p2 = run_multiturn(a.port, a.turns, doc + " Variant %d." % rep, a.max_tokens)
            for x, y in zip(p1, p2):
                total += 1
                eq = bool(x["text"]) and x["text"] == y["text"]
                bad += (not eq)
                out.write(json.dumps({"conv": rep, "turn": x["turn"], "equal": eq,
                                      "prompt_tokens": x["prompt_tokens"],
                                      "completion_cold": x["completion"],
                                      "completion_hit": y["completion"],
                                      "first_diff_char": next(
                                          (k for k in range(min(len(x["text"]), len(y["text"])))
                                           if x["text"][k] != y["text"][k]),
                                          min(len(x["text"]), len(y["text"]))),
                                      "cold": x["text"][:3000], "hit": y["text"][:3000]}) + "\n")
                out.flush()
                print("  conv=%d turn=%d P=%-6s %s" % (rep, x["turn"], x["prompt_tokens"],
                                                       "SAME" if eq else "DIFFERENT"))
        print("\nmulti-turn turns compared: %d   divergent: %d" % (total, bad))
        return 0 if bad == 0 else 1


    for i in range(a.n):
        title, stem = DOCS[i % len(DOCS)]
        # Size the context to a target token count. This is the whole point of the harness:
        # a checkpoint is only written once the prompt crosses the store alignment step
        # (floor(P/2048)*2048), so a short prompt never exercises the disk path at all --
        # an earlier version of this file used 70-390 token prompts and silently compared
        # cold against cold. ~3.4 chars/token for this prose.
        target = TARGETS[i % len(TARGETS)]
        cpt = CPT            # measured, not assumed
        # Repeat exactly the unit that calibrate() measured, so the chars/token ratio applies;
        # the topic stem is prepended once rather than folded into the repeated block.
        doc = stem + (PARA * (1 + int(target * cpt // len(PARA)))).encode("utf-8")[:int(target * cpt)].decode("utf-8", "ignore")
        # ^ length derived from the measured ratio, so "target tokens" means what it says
        msgs = [{"role": "system", "content": "[%s] Archive %d. %s" % (a.unique_prefix, i, doc)},
                {"role": "user", "content":
                    "Enumerate every causal factor implied by this archive. Number them 1 to 40 "
                    "and give each a two-sentence explanation. Keep going until the list is "
                    "complete; do not summarise and do not stop early."}]
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
        fd = next((k for k in range(min(len(c1), len(c2))) if c1[k] != c2[k]),
                  min(len(c1), len(c2)))
        rec = {"i": i, "prompt_tokens": u1.get("prompt_tokens"), "equal": equal,
               "max_tokens": a.max_tokens, "completion_cold": u1.get("completion_tokens"),
               "completion_hit": u2.get("completion_tokens"),
               "len_cold": len(c1), "len_hit": len(c2), "first_diff_char": fd,
               "cold": c1[:4000], "hit": c2[:4000]}
        out.write(json.dumps(rec) + "\n")
        out.flush()
        print("  %-3d P=%-6s target=%-6d %s" % (i, u1.get("prompt_tokens"), target,
                                                 "SAME" if equal else "DIFFERENT"))
        if target and abs((u1.get("prompt_tokens") or 0) - target) > 0.25 * target:
            print("      SIZING WARNING: targeted %d tokens, got %s (chars/token assumption is off)"
                  % (target, u1.get("prompt_tokens")), file=sys.stderr)
        if (u1.get("prompt_tokens") or 0) < 2048:
            print("      ^ below the 2048-token store step: this prompt cannot produce a "
                  "checkpoint, so the comparison is vacuous", file=sys.stderr)

    n = same + diff
    print("\nprompts compared: %d   identical: %d (%.1f%%)   divergent: %d (%.1f%%)"
          % (n, same, 100.0 * same / n if n else 0, diff, 100.0 * diff / n if n else 0))
    done = [json.loads(l) for l in open(a.out)]
    comp = sorted(x.get("completion_cold") or 0 for x in done)
    if comp:
        med = comp[len(comp) // 2]
        print("median completion_tokens=%d (max_tokens=%d)" % (med, a.max_tokens))
        if med < 100:
            print("  WARNING: replies stopped early, so long-generation divergence is NOT tested."
                  " Make the prompt demand more output or raise the ceiling.", file=sys.stderr)
    print("results: %s  -- cross-check the server log for 'kv cache hit' on each pass-2 request" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
