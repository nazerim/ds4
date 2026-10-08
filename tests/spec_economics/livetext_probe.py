#!/usr/bin/env python3
"""Multi-turn live_text sizing probe (TOKENIZER-AUDIT open question 4).

Drives ONE session through many prefix-extending turns against :8002 so the
slot's live_text checkpoint grows turn over turn, then the per-turn
slot_refresh_live_text wall time (trace-gated instrumentation in
ds4_server.c) sizes the incremental fix (58c549c) against the full re-render
path via the DS4_LIVE_TEXT_FULL A/B knob - one binary, two server runs.

Turn k prompt = base + all previous (user, assistant) turns + a new short
user line; greedy (temperature 0), max_tokens per turn.  Completions are
byte-identical across the A/B binaries (the detok table is byte-identical),
so both runs see identical traffic.

usage: uv run --no-project python livetext_probe.py <tag> [turns] [max_tokens]
tag is banked into the results transcript name.
"""
import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8002/v1/completions"

BASE = (
    "The following is a long reference document about a fictional harbor "
    "town. Use it as context for the questions that follow. "
) + (
    "The harbor town of Saltmere sits on a rocky coastline where the tidal "
    "range reaches nine meters. Its economy has always depended on the sea: "
    "fishing, shipwright work, and since the last century, a small museum "
    "that documents the wrecks scattered along the outer reef. Every "
    "spring the town holds a festival, and every winter the council debates "
    "how to fund the seawall repairs. "
) * 24  # ~1.2-1.5k tokens

FOLLOWUPS = [
    "Summarize the document in one sentence.",
    "List three facts about the town's economy.",
    "What does the museum document? Answer briefly.",
    "Name two things the town debates every winter.",
    "Describe the tidal range and why it matters.",
    "Give one sentence about the festival.",
    "What work does the shipwright yard do?",
    "Why is the seawall important?",
    "How many meters is the tidal range?",
    "What is the outer reef? Answer in a few words.",
]


def run_turn(prompt: str, max_tokens: int) -> dict:
    body = json.dumps({
        "model": "qwen",
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
        # thinking off: the returned text is then exactly the raw generated
        # tokens' text, so the next turn's prompt re-tokenizes the completion
        # cleanly and the live_text append predicate can actually fire.
        "reasoning_effort": "none",
    }).encode()
    req = urllib.request.Request(
        URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        out = json.loads(r.read())
    dt = time.time() - t0
    usage = out.get("usage", {})
    return {
        "wall_s": round(dt, 2),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "text": out["choices"][0]["text"],
    }


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else "run"
    turns = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    max_tokens = int(sys.argv[3]) if len(sys.argv) > 3 else 400
    convo = BASE
    records = []
    t_all = time.time()
    for k in range(turns):
        q = FOLLOWUPS[k % len(FOLLOWUPS)]
        r = run_turn(convo + f"\n\nUser: {q}\n\nAssistant: ", max_tokens)
        records.append({"turn": k, "question": q, **{kk: r[kk] for kk in
                                                     ("wall_s", "prompt_tokens", "completion_tokens")}})
        print(f"turn {k:3d} wall={r['wall_s']:6.1f}s prompt={r['prompt_tokens']} "
              f"completion={r['completion_tokens']}", flush=True)
        convo += f"\n\nUser: {q}\n\nAssistant: {r['text']}"
    total = time.time() - t_all
    print(f"== {tag}: {turns} turns, total wall={total:.1f}s, final session ~"
          f"{records[-1]['prompt_tokens'] + records[-1]['completion_tokens']} tokens ==")
    path = f"tests/spec_economics/results/20261005_livetext_{tag}.json"
    with open(path, "w") as f:
        json.dump({"tag": tag, "turns": turns, "max_tokens": max_tokens,
                   "wall_s": round(total, 1), "records": records}, f)
    print(f"transcript banked: {path}")


if __name__ == "__main__":
    main()
