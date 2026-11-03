"""Loop quarantine: detect + exclude thinking-loop data (plan 2026-11-02/03).

Two mechanisms, one JSON artifact (version 3):
  1. SEGMENT-level depth rule: repetition depth = pairs/unique-bigrams over a
     segment's usable records. True exact-repeat loops measured 19.6-187.6x;
     healthy 2-5x. Cut: depth > 15 AND pairs >= 300. (Unchanged, reviewed
     sound Nov-03.)
  2. SPAN-level: chop log lines mark budget/tripwire force-closes.

SPAN GEOMETRY (fixed per code review ses_e62ad1309: B1/B2, Nov-03):
Engine logs "chat ctx=A..B:S ... (gen=G)" where B is the PROMPT length fixed
at request start, while record pos == checkpoint.len advances through
GENERATION. The chopped thinking span therefore occupies positions
[B+G-T, B+G] (T = in-span token count), NOT [B-T, B].
  - budget lines carry gen= directly -> exact window.
  - tripwire lines (2fd684d format) DO NOT log gen -> recovered from the
    periodic progress lines ("chat ctx=..B:S gen=N decoding chunk=") keyed by
    identical B: last-seen gen for that request. Fallback: [B, B+T].
  - spans are attributed to (pid, kind, lo, hi); pid tracked PER LINE as the
    last-seen parity banner in that log file (B2: era rotation is not
    guaranteed to split files).
  - attributed excision intersects SEGMENTS (S2): only segments whose max pos
    reaches hi-1 (the conversation that actually ran to the close) are cut,
    so overlapping pos in unrelated short conversations survives.
Bigram convention kept historical: (seed, labels[1]) for n_label>=2.
"""
from __future__ import annotations

import glob
import json
import mmap
import os
import re
import struct
from collections import Counter

from .data_contract import CORPUS_ROOT, RECORD_BYTES, STREAK_CLAMP

HDR = struct.Struct("<IiBBBB5i")
DEPTH_CUT = 15.0
MIN_PAIRS = 300
SPAN_WINDOW_DEPTH = 8.0          # legacy unattributed windows only
BUDGET_WIN_GUARD = 4.0             # attributed budget chops: need repetition
ENFORCE_RE = re.compile(
    r"(?:chat|completion) ctx=\d+\.\.(\d+):\d+ thinking budget \d+ "
    r"enforced after (\d+) in-span tokens \(gen=(\d+)\)")
TRIP_RE = re.compile(
    r"(?:chat|completion) ctx=\d+\.\.(\d+):\d+ thinking loop tripwire: "
    r"closed span at (\d+) tokens(?: \(period \d+\))?"
    r"(?:.*?\(gen=(\d+)\))?")
PROGRESS_RE = re.compile(r"(?:chat|completion) ctx=\d+\.\.(\d+):\d+ gen=(\d+)")
PID_RE = re.compile(r"parity_pid(\d+)\.bin")


def _segments(recs_path):
    """(start_idx, [rec-tuples]) using dataset boundary semantics (mmap)."""
    from .dataset import FLAG_SEGMENT_START
    size = os.path.getsize(recs_path)
    n = size // RECORD_BYTES
    segs = []
    with open(recs_path, "rb") as fd:
        mm = mmap.mmap(fd.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            cur, cur_start, prev_pos = None, None, None
            for i in range(n):
                r = HDR.unpack_from(mm, i * RECORD_BYTES)
                boundary = (cur is None or (r[5] & FLAG_SEGMENT_START)
                            or (prev_pos is not None and r[0] <= prev_pos))
                if boundary:
                    if cur:
                        segs.append((cur_start, cur))
                    cur, cur_start = [], i
                cur.append(r)
                prev_pos = r[0]
            if cur:
                segs.append((cur_start, cur))
        finally:
            mm.close()
    return segs


def _depth_from(recs):
    cnt = Counter()
    for r in recs:
        if r[3] < 2 or r[2] >= STREAK_CLAMP or r[4] != 3:
            continue
        cnt[(r[1], r[7])] += 1
    t = sum(cnt.values())
    return t, len(cnt)


def parse_spans(log_patterns):
    """[lo, hi, kind, pid] per chop, with per-line pid tracking and gen
    recovery for tripwire lines. Day-independent (pos windows + pid key)."""
    spans = []
    for pat in (log_patterns or []):
        for lp in (glob.glob(pat) if any(c in pat for c in "*?[") else [pat]):
            if not os.path.exists(lp):
                continue
            pid = None
            last_gen = {}          # B -> latest gen seen for that request
            for line in open(lp, errors="replace"):
                pm = PID_RE.search(line)
                if pm:
                    pid = int(pm.group(1))
                    continue
                g = PROGRESS_RE.search(line)
                if g and "decoding chunk" in line:
                    last_gen[int(g.group(1))] = int(g.group(2))
                m = ENFORCE_RE.search(line)
                if m:
                    b, tok, gen = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    hi = b + gen
                    spans.append([max(0, hi - tok), hi, "budget", pid])
                    last_gen[b] = gen
                    continue
                m = TRIP_RE.search(line)
                if m:
                    b, tok = int(m.group(1)), int(m.group(2))
                    gen = (int(m.group(3)) if m.group(3)
                           else last_gen.get(b, tok))
                    hi = b + gen
                    spans.append([max(0, hi - tok), hi, "trip", pid])
                    last_gen[b] = gen
    return spans


def build_quarantine(days=None, root=CORPUS_ROOT, log_patterns=None,
                     depth_cut=DEPTH_CUT, min_pairs=MIN_PAIRS):
    if days is None:
        days = sorted(d for d in os.listdir(root)
                      if os.path.isdir(os.path.join(root, d))
                      and not d.startswith(".") and "tensors" not in d)
    segments = {}          # "day/stem" -> [start_idx,...]
    flagged = []
    for d in days:
        for rp in sorted(glob.glob(os.path.join(root, d, "train_pid*.recs"))):
            key = f"{d}/{os.path.basename(rp).removesuffix('.recs')}"
            for start, recs in _segments(rp):
                t, u = _depth_from(recs)
                if t >= min_pairs and t / max(u, 1) > depth_cut:
                    segments.setdefault(key, []).append(start)
                    flagged.append({"day": d, "stem": key.split("/")[-1],
                                    "start": start, "pairs": t, "uniq": u,
                                    "depth": round(t / max(u, 1), 1)})
    q = {"version": 3,
         "rule": {"depth_gt": depth_cut, "min_pairs": min_pairs,
                  "span_geometry": "pos [B+G-T, B+G]; pid-per-line; "
                                   "segment-intersected"},
         "segments": segments, "segment_detail": flagged,
         "spans": parse_spans(log_patterns)}
    return q


def file_exclusions(recs_path, q):
    """Set of record indices excluded in this file by the quarantine."""
    stem = os.path.basename(recs_path).removesuffix(".recs")
    day = os.path.basename(os.path.dirname(recs_path))
    dead_starts = set(q.get("segments", {}).get(f"{day}/{stem}", []))
    spans = [tuple(s) for s in q.get("spans", [])]
    excl = set()
    segs = _segments(recs_path)
    all_recs = []
    for start, recs in segs:
        if start in dead_starts:
            excl.update(range(start, start + len(recs)))
            continue
        all_recs.extend(enumerate(recs, start))
    stem_pid = int(stem.rsplit("pid", 1)[1]) if "pid" in stem else None
    for span in spans:
        lo, hi = span[0], span[1]
        kind = span[2] if len(span) > 2 else "budget"
        sp_pid = span[3] if len(span) > 3 else None
        if sp_pid is not None and sp_pid != stem_pid:
            continue
        # S2: intersect with the conversation(s) that actually reached hi.
        for start, recs in segs:
            if start in dead_starts:
                continue
            maxpos = max(r[0] for r in recs)
            if maxpos < hi - 1:
                continue
            win = [r for r in recs if lo <= r[0] <= hi]
            if not win:
                continue
            if sp_pid is not None:
                if kind == "budget":
                    # Budget chops fire for loops AND for legitimate long
                    # thinking (measured Nov-03: day-10 windows split 1.9-3.8x
                    # legit vs 4.1-11.6x soft-loop). Require repetition
                    # evidence at load: excise window only when it measures
                    # >= BUDGET_WIN_GUARD depth. Trip chops stay unconditional
                    # (their firing IS cycle evidence).
                    win = [r for r in recs if lo <= r[0] <= hi]
                    if len(win) < 50:
                        continue
                    t, u = _depth_from(win)
                    if not t or t / max(u, 1) < BUDGET_WIN_GUARD:
                        continue
                excl.update(start + j for j, r in enumerate(recs)
                            if lo <= r[0] <= hi)
            else:
                if len(win) < 50:
                    continue
                t, u = _depth_from(win)
                guard = 4.0 if kind == "trip" else SPAN_WINDOW_DEPTH
                if t and t / max(u, 1) >= guard:
                    excl.update(start + j for j, r in enumerate(recs)
                                if lo <= r[0] <= hi)
    return excl


def load_quarantine(path):
    if not path:
        return None
    q = json.load(open(path))
    if q.get("version") != 3:
        raise RuntimeError(f"quarantine version {q.get('version')} != 3; "
                           "regenerate with python -m dspark.loops --audit")
    return q


def main():
    import argparse
    ap = argparse.ArgumentParser(description="loop quarantine audit")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--root", default=CORPUS_ROOT)
    ap.add_argument("--days", default=None)
    ap.add_argument("--logs", default=None)
    a = ap.parse_args()
    days = a.days.split(",") if a.days else None
    logs = a.logs.split(",") if a.logs else None
    q = build_quarantine(days=days, root=a.root, log_patterns=logs)
    tot_seg_pairs = sum(f["pairs"] for f in q["segment_detail"])
    from collections import Counter as C
    kinds = C(s[2] for s in q["spans"])
    print(f"segments flagged: {len(q['segment_detail'])} "
          f"({tot_seg_pairs:,} pairs) | spans: {len(q['spans'])} {dict(kinds)}")
    for f in q["segment_detail"]:
        print(f"  {f['day']} {f['stem']}:{f['start']}  pairs={f['pairs']}"
              f" uniq={f['uniq']} depth={f['depth']}x")
    if a.out:
        json.dump(q, open(a.out, "w"), indent=1)
        print("wrote", a.out)


if __name__ == "__main__":
    main()
