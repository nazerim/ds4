#!/usr/bin/env python3
"""Deliberately test whether CHECKPOINT RESUME DEPTH changes the model's answer.

Background: in one contaminated run, the same 11,650-token prompt resumed at depth 10240 in one
request and 8192 in the next, and the replies were identical for 298 characters then substantially
different. Everything controlled since then (161 comparisons) shows cold == resume, so resume DEPTH
is the one variable never tested on purpose.

Method, per prompt, against a running engine with its own scratch KV dir:
  A  send the prompt            -> cold prefill (nothing cached yet), stores an aligned checkpoint
  B  evict the live slot, resend -> resumes DEEP; note the file the log names for that load
  C  delete exactly that file, evict the live slot, resend
                                   -> resumes at the next-shortest prefix (or cold)
Compare A vs B (expect identical: cold == resume) and B vs C (the question).

The log is read after every request so each outcome is classified from what the server actually did
('prompt start ctx=X..P'), never from an assumption. C is discarded as uninformative unless it
genuinely resumed at a shallower X.

Needs the engine on --port and its KV dir at --kvdir; run with production stopped.
"""
import argparse
import difflib
import json
import os
import re
import sys
import time
import urllib.request

PARA = ("recorded observations from the survey season, cross-checked against the neighbouring "
        "stations and the earlier returns. ")
START_RE = re.compile(r"chat ctx=(\d+)\.\.(\d+):\d+ prompt start")
HIT_RE = re.compile(r"kv cache hit text tokens=(\d+).*?file=(\S+)")


QUESTION = ("Enumerate every causal factor implied here. Number them "
            "1 to 30, two sentences each, do not stop early.")


def calibrate_chat(port, probe_text):
    """One short request to measure chars/token for THIS corpus (never assume a constant)."""
    _, u = chat(port, [{"role": "system", "content": "Cal. " + probe_text},
                       {"role": "user", "content": "Reply: ok"}], 8)
    pt = u.get("prompt_tokens") or 0
    return (len(probe_text) / float(pt)) if pt > 0 else None


def build_prompt(i, uniq, target_tokens, cpt):
    """Byte-exact prompt. Shared with chunkshape_probe.py via import, because the two
    harnesses must generate the SAME prompt across engine configs or the comparison
    is meaningless. Keep this the single source of truth."""
    doc = ("case %d uniq %s. " % (i, uniq)) + \
          (PARA * (1 + int(target_tokens * cpt // len(PARA))))[:int(target_tokens * cpt)]
    return [{"role": "system", "content": doc},
            {"role": "user", "content": QUESTION}]


def chat(port, msgs, max_tokens):
    body = json.dumps({"model": "default", "messages": msgs, "temperature": 0,
                       "max_tokens": max_tokens, "stream": False,
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % port,
                                 data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        d = json.loads(r.read())
    return (d["choices"][0]["message"].get("content") or "").strip(), d.get("usage", {})


def tail_from(path, pos):
    """Return (new_text, new_pos) so each request's log slice is unambiguous."""
    with open(path, "r", errors="replace") as fh:
        fh.seek(pos)
        data = fh.read()
        new_pos = fh.tell()          # must be read inside the with-block; the handle is closed after
    return data, new_pos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8003)
    ap.add_argument("--log", required=True)
    ap.add_argument("--kvdir", required=True)
    ap.add_argument("--reps", type=int, default=12)
    ap.add_argument("--target-tokens", type=int, default=12000)
    ap.add_argument("--cpt", type=float, default=0.0,
                    help="chars/token; 0 = measure once from the engine")
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--out", default="/tmp/resumedepth.jsonl")
    ap.add_argument("--cpt-file", default=None,
                    help="write the measured chars/token here so a second engine phase can "
                         "reuse the EXACT same prompt sizing instead of re-estimating it")
    a = ap.parse_args()

    pos = os.path.getsize(a.log)

    def classify(text):
        m = None
        for m in START_RE.finditer(text):
            pass
        if not m:
            return None, None
        return int(m.group(1)), int(m.group(2))

    # calibration: measure chars/token from this corpus against the live engine
    if a.cpt <= 0:
        probe = PARA * 40
        _, u = chat(a.port, [{"role": "system", "content": "Cal. " + probe},
                             {"role": "user", "content": "Reply: ok"}], 8)
        pt = u.get("prompt_tokens") or 0
        if pt <= 0:
            print("calibration failed", file=sys.stderr)
            return 1
        cpt = len(probe) / float(pt)
        print("measured %.2f chars/token" % cpt, file=sys.stderr)
        if a.cpt_file:
            with open(a.cpt_file, "w") as fh:
                fh.write("%.6f\n" % cpt)
    else:
        cpt = a.cpt

    out = open(a.out, "w", encoding="utf-8")
    ab_same = ab_diff = bc_same = bc_diff = skipped = 0
    for i in range(a.reps):
        msgs = build_prompt(i, os.environ.get("UNIQ", "x"), a.target_tokens, cpt)
        rec = {"i": i}

        # A: cold
        tA, uA = chat(a.port, msgs, a.max_tokens)
        logA, pos = tail_from(a.log, pos)
        xA, PA = classify(logA)
        rec.update(A_ctx_from=xA, P=PA, compA=uA.get("completion_tokens"))

        # B: deep resume (filler clears the live slot)
        chat(a.port, [{"role": "user", "content": "One colour and one number only."}], 12)
        tB, uB = chat(a.port, msgs, a.max_tokens)
        logB, pos = tail_from(a.log, pos)
        xB, _ = classify(logB)
        hits = HIT_RE.findall(logB)
        rec.update(B_ctx_from=xB, compB=uB.get("completion_tokens"),
                   B_hit_tokens=[int(h[0]) for h in hits],
                   B_files=[h[1] for h in hits])

        # C: remove the file that served the deepest load, then resume again
        removed = []
        for f in dict.fromkeys(rec["B_files"]):
            try:
                if f.startswith(a.kvdir) and os.path.exists(f):
                    os.remove(f)
                    removed.append(f)
            except OSError:
                pass
        rec["removed"] = len(removed)
        if not removed:
            rec["note"] = "no file removable; C uninformative"
            skipped += 1
            out.write(json.dumps(rec) + "\n"); out.flush()
            print("  i=%-2d SKIP (nothing removable)  B resumed at %s of %s" % (i, xB, PA))
            continue

        chat(a.port, [{"role": "user", "content": "One colour and one number only."}], 12)
        tC, uC = chat(a.port, msgs, a.max_tokens)
        logC, pos = tail_from(a.log, pos)
        xC, _ = classify(logC)
        rec.update(C_ctx_from=xC, compC=uC.get("completion_tokens"))

        # A shallower resume must be a real resume: xC == 0 is a cold rebuild, which only
        # re-tests cold-vs-resume (already answered 16/16) and would silently inflate the
        # deep-vs-shallow denominator.
        if xC is None or xC >= xB or xC == 0:
            rec["note"] = "C not a shallower resume (xB=%s xC=%s)" % (xB, xC)
            skipped += 1
        else:
            deep_shallow_differ = (tB != tC)
            bc_diff += deep_shallow_differ
            bc_same += not deep_shallow_differ
        if xA == 0 and xB and xB > 0:
            ab_same += (tA == tB)
            ab_diff += (tA != tB)

        # Capture the texts and where they part: "differs" without magnitude is not evidence,
        # and A==C is what turns "deep != shallow" into "shallow != cold".
        def fd(x, y):
            return next((k for k in range(min(len(x), len(y))) if x[k] != y[k]),
                        min(len(x), len(y)))
        rec["textA"], rec["textB"], rec["textC"] = tA[:3000], tB[:3000], tC[:3000]
        rec["first_diff_AB"], rec["first_diff_BC"] = fd(tA, tB), fd(tB, tC)
        rec["similarity_BC"] = round(difflib.SequenceMatcher(None, tB, tC).ratio(), 3)
        rec["same_AC"] = (tA == tC)

        out.write(json.dumps(rec) + "\n"); out.flush()
        print("  i=%-2d P=%-6s A(ctx0=%s) B@%s C@%s  A==B:%-5s B==C:%-5s%s"
              % (i, PA, xA == 0, xB, xC, tA == tB, tB == tC,
                 "  " + str(rec.get("note", "")) if rec.get("note") else ""))

    print("\nRESULT")
    print("  A vs B (cold vs deep resume):   same=%d differ=%d" % (ab_same, ab_diff))
    print("  B vs C (deep vs shallow resume): same=%d differ=%d  -> divergence rate %s"
          % (bc_same, bc_diff, ("%.0f%%" % (100.0 * bc_diff / (bc_same + bc_diff)))
             if (bc_same + bc_diff) else "n/a"))
    print("  uninformative (no shallower resume achieved): %d" % skipped)
    ac = [json.loads(l) for l in open(a.out)]
    ac = [r for r in ac if r.get("same_AC") is not None]
    print("  A vs C (cold vs shallow resume):  same=%d differ=%d   <- the case that matters for production"
          % (sum(1 for r in ac if r["same_AC"]), sum(1 for r in ac if not r["same_AC"])))
    fdb = [r["first_diff_BC"] for r in ac if r.get("first_diff_BC") is not None and not r.get("same_AC", True)]
    if fdb:
        print("  where deep vs shallow first part (chars): median=%d min=%d max=%d"
              % (sorted(fdb)[len(fdb) // 2], min(fdb), max(fdb)))
    sims = [r.get("similarity_BC") for r in ac if r.get("similarity_BC") is not None]
    if sims:
        print("  similarity of deep vs shallow replies: median=%.3f min=%.3f"
              % (sorted(sims)[len(sims) // 2], min(sims)))
    print("  detail: %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
