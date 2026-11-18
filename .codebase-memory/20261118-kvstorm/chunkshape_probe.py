#!/usr/bin/env python3
"""Is the answer a function of PREFILL CHUNK SHAPE alone, with no checkpoint involved?

Why this exists. `resume_depth_probe.py` established that resuming an ~11.7k-token prompt
from the mid-prefill `continued` checkpoint (ctx=8192) changes the completion versus cold
prefill, while resuming from the boundary `cold` checkpoint (ctx=10240) is byte-identical.
The engine's own log explains the asymmetry by CHUNK SHAPE, and a static read of
`ds4_server.c:16207-16274` says why: a cold request with the disk cache ENABLED syncs to
`cold_store_len` mid-prefill so it can write the checkpoint, which splits the prefill into
8192 + 2048 + 1459. Resume at 10240 then runs the same final 1459 chunk; resume at 8192
runs 3507 as one chunk.

That yields a prediction testable WITHOUT instrumenting the engine and WITHOUT checkpoints.
The cold split is gated on `s->kv.enabled`, which is set only when `--kv-disk-dir` is passed
(`ds4_server.c:18931`, `ds4_kvstore.c:2419`). So start the same binary with that one flag
removed and cold prefill must fall back to 8192 + 3507 -- the same shape as the shallow
resume, produced by a completely different route:

  A  cold, disk ON   schedule 8192+2048+1459
  C  resume@8192     schedule 3507        (from resume_depth_probe, same prompt)
  D  cold, disk OFF  schedule 8192+3507   (this probe)

  H2 (checkpoint faithful, shape decides)  =>  D == C  and D != A
  H1 (continued checkpoint unfaithful)     =>  D != C

Both outcomes are informative, which is what makes this worth a window.

Prompts come from `resume_depth_probe.build_prompt`, imported so the two harnesses cannot
drift apart. Pass the SAME --uniq and --cpt to both phases; --cpt is written by the ON phase
to a file, because chars/token must not be re-guessed per engine or the prompts differ.
"""
import argparse
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "rdp", os.path.join(HERE, "resume_depth_probe.py"))
rdp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rdp)

CHUNK_RE = re.compile(r"prefill chunk (\d+)/(\d+)")
START_RE = re.compile(r"chat ctx=(\d+)\.\.(\d+):(\d+) prompt start")


def schedule(text):
    """(ctx_from, P, chunk sizes) from the newest 'prompt start' in a log slice."""
    st = list(START_RE.finditer(text))
    if not st:
        return None, None, []
    last = st[-1]
    chunks = [int(m.group(1)) for m in CHUNK_RE.finditer(text, last.start())]
    sizes, prev = [], 0
    for c in chunks:
        sizes.append(c - prev)
        prev = c
    return int(last.group(1)), int(last.group(2)), sizes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8003)
    ap.add_argument("--log", required=True)
    ap.add_argument("--reps", type=int, default=6)
    ap.add_argument("--target-tokens", type=int, default=12000)
    ap.add_argument("--cpt-file", required=True,
                    help="file holding the measured chars/token (written by the disk-ON phase)")
    ap.add_argument("--uniq", default="SHAPE-TEST")
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--expect-disk", choices=["on", "off"], default="off",
                    help="asserts the expected cold chunk shape, so a misconfigured engine "
                         "is reported instead of silently producing a comparison of nothing")
    ap.add_argument("--out", default="/tmp/chunkshape.jsonl")
    a = ap.parse_args()

    cpt = float(open(a.cpt_file).read().strip())
    pos = os.path.getsize(a.log)
    out = open(a.out, "w", encoding="utf-8")

    rows = []
    for i in range(a.reps):
        msgs = rdp.build_prompt(i, a.uniq, a.target_tokens, cpt)
        text, usage = rdp.chat(a.port, msgs, a.max_tokens)
        slice_, pos = rdp.tail_from(a.log, pos)
        x_from, P, sizes = schedule(slice_)
        rows.append({"i": i, "ctx_from": x_from, "P": P, "chunks": sizes,
                     "completion": usage.get("completion_tokens"), "text": text})
        out.write(json.dumps(rows[-1]) + "\n")
        out.flush()
        print("  i=%-2d P=%-6s ctx=%-6s chunks=%-22s chars=%d"
              % (i, P, x_from, "+".join(str(x) for x in sizes), len(text)))

    # Hard config assertion: a phase that did not get the shape it claims proves nothing.
    shapes = {tuple(r["chunks"]) for r in rows if r["ctx_from"] == 0}
    one_chunk_tail = all(len(s) >= 2 and s[0] == 8192 for s in shapes)
    if not one_chunk_tail:
        print("CONFIG ERROR: no 8192-led chunk schedule observed: %s" % (shapes,), file=sys.stderr)
        return 4
    split = {s for s in shapes if len(s) >= 3}
    merged = {s for s in shapes if len(s) == 2}
    if a.expect_disk == "on" and not split:
        print("CONFIG ERROR: expected the cold-store split (disk ON) but every schedule was %s"
              % (shapes,), file=sys.stderr)
        return 4
    if a.expect_disk == "off" and not merged:
        print("CONFIG ERROR: expected a merged tail (disk OFF) but every schedule was %s"
              % (shapes,), file=sys.stderr)
        return 4
    print("  schedules confirmed for expect_disk=%s: %s" % (a.expect_disk, sorted(shapes)))
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
