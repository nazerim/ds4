#!/usr/bin/env python3
"""Corpus class audit for DS4_TRAIN_DUMP harvests.

Usage: tools/td_classify.py <corpus-dir> [<corpus-dir> ...]

Classifies every usable record by the CONTROL/STRUCTURAL token ids in its
window (seed + lookahead labels), cross-tabs against depth bands, and reports
cycle-gap statistics (streak = NEXT record pos - this pos - 1; tokens
committed between prepared cycles; a scheduler-skip regime measure, not raw
draft acceptance).

Ground rules (aligned with tools/td_manifest.py + CAPTURE-SCHEMA.md):
  usable  = n_label >= 1  (single shared predicate; 255-streak records stay,
            but gap stats exclude the clamped sentinel >= 250)
  excluded records (n_label==0) still COUNT toward structural instances —
            they carry labels==none but their seed may be a control token.

Boundary visibility is lattice-limited: a record covers positions
[pos..pos+4]; consecutive-cycle holes (mean 9-13 in skip regions) hide
control tokens at phase 5-mod-6. Instance counts are reported RAW and
CORRECTED via cluster-local coverage density (reviewed estimator).

Segments are the on-disk ground truth: flags==1 marks segment starts; pos
restarts per segment, so instance dedup keys on (file, segment, pos, tok).

NOTE for this model family (DSML output mode): only ids 128825 (｜DSML｜)
and 128822 (think-END) ever appear in generated windows — chat-template tool
markers (128806-14/29), file/repo markers and role tokens are prompt-side or
emitted as plain text; zero-rows for those classes are EXPECTED, not
regressions. The set is kept for portability across chat-template models.

Control ids resolved from ds4flash.gguf (tools/vocab_ids.py).
"""
import glob
import os
import struct
import sys
from collections import defaultdict

REC = struct.Struct("<IiBBBB5i")
SZ = REC.size + 12288

DSML = 128825
TOOL = set(range(128806, 128815)) | {128829}
THINK = {128821, 128822}
FILESYS = set(range(128815, 128821))
ROLE = {128803, 128804, 128805, 128826, 128827, 128828}
PLACEHOLDER = {128823, 128824}
STREAK_SENTINEL = 250          # clamped/open gaps excluded from means


def isctl(t):
    return (t == DSML or t in TOOL or t in THINK or t in FILESYS
            or t in ROLE or t in PLACEHOLDER)


def classify(seed, labels, nlabel):
    if seed == DSML or seed in TOOL:
        return "toolcall-own"
    if seed in THINK:
        return "think-edge"
    if seed in ROLE:
        return "role-edge"
    if seed in FILESYS or seed in PLACEHOLDER:
        return "path-region"
    win = labels[:nlabel]
    if any(t == DSML or t in TOOL for t in win):
        return "pre-toolcall"
    if any(t in THINK for t in win):
        return "pre-think"
    if any(t in FILESYS or t in PLACEHOLDER for t in win):
        return "path-region"
    if any(t in ROLE for t in win):
        return "role-edge"
    return "plain"


def band(p):
    return ("0-16k" if p < 16384 else "16-64k" if p < 65536
            else "64-128k" if p < 131072 else "128k+")


BANDS = ("0-16k", "16-64k", "64-128k", "128k+")
CLASSES = ["toolcall-own", "pre-toolcall", "think-edge", "pre-think",
           "path-region", "role-edge", "plain"]


def load(rpath):
    data = open(rpath, "rb").read()
    if len(data) % SZ:
        print(f"WARNING {rpath}: framing remainder {len(data) % SZ}",
              file=sys.stderr)
    return [REC.unpack_from(data, i * SZ) for i in range(len(data) // SZ)]


def audit_dir(d):
    st = defaultdict(lambda: [0, 0, 0])     # class -> [recs, gap_sum, gaps_n]
    bands = defaultdict(lambda: defaultdict(int))
    instances = set()
    rec_with_ctl = 0
    corrected_total = 0.0
    usable = excl = segs = 0
    for rpath in sorted(glob.glob(os.path.join(d, "train_pid*.recs"))):
        rows = load(rpath)
        n = len(rows)
        starts = [i for i in range(n) if rows[i][5] == 1] or [0]
        starts.append(n)
        for s0, s1 in zip(starts[:-1], starts[1:]):
            seg = rows[s0:s1]
            if not seg:
                continue
            segs += 1
            lo = min(r[0] for r in seg)
            hi = max(r[0] for r in seg) + 4
            # coverage lattice: any pos covered by >=1 record's [pos..pos+4]
            cov = bytearray(hi - lo + 1)
            for r in seg:
                for q in range(r[0], min(r[0] + 5, hi + 1)):
                    cov[q - lo] = 1
            for idx, r in enumerate(seg):
                pos, seed, streak, nlab = r[0], r[1], r[2], r[3]
                labels = r[6:11]
                if isctl(seed):
                    instances.add((rpath, s0, pos, seed))
                for j in range(nlab):
                    if isctl(labels[j]):
                        instances.add((rpath, s0, pos + j, labels[j]))
                has_ctl = isctl(seed) or any(isctl(t) for t in labels[:nlab])
                if nlab == 0:
                    excl += 1
                    continue
                usable += 1
                if has_ctl:
                    rec_with_ctl += 1
                c = classify(seed, labels, nlab)
                st[c][0] += 1
                bands[c][band(pos)] += 1
                if streak < STREAK_SENTINEL:
                    st[c][1] += streak
                    st[c][2] += 1
            # cluster-local coverage correction for DSML/THINK instances here
            pts = sorted(p for (rp, s, p, t) in instances
                         if rp == rpath and s == s0)
            cl, cur = [], []
            for p in pts:
                if cur and p - cur[-1] > 30:
                    cl.append(cur)
                    cur = []
                cur.append(p)
            if cur:
                cl.append(cur)
            for cur in cl:
                a = max(lo, cur[0] - 5)
                b = min(hi, cur[-1] + 5)
                dens = sum(cov[a - lo:b - lo + 1]) / max(b - a + 1, 1)
                corrected_total += len(cur) / max(dens, 0.05)
    return st, bands, usable, excl, segs, len(instances), \
        corrected_total, rec_with_ctl


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    g = defaultdict(lambda: [0, 0, 0])
    gtot = gi = gcorr = gctl = gusable = 0
    for d in sys.argv[1:]:
        st, bands, usable, excl, segs, ninst, corr, rec_ctl = audit_dir(d)
        gtot += usable
        gi += ninst
        gcorr += corr
        gctl += rec_ctl
        for c, v in st.items():
            for k in range(3):
                g[c][k] += v[k]
        print(f"\n=== {d}  segments={segs} usable={usable} "
              f"excluded(n_label=0)={excl}")
        print(f"    control instances: raw={ninst}  corrected~{corr:.0f} "
              f"(lattice coverage)   records touching boundary: {rec_ctl} "
              f"({100*rec_ctl/max(usable,1):.2f}%)")
        if not usable:
            continue
        print(f"{'class':<14} {'recs':>7} {'share':>6} {'mean-gap':>9}  "
              + " ".join(f"{b:>7}" for b in BANDS))
        for c in CLASSES:
            v = st.get(c)
            if not v or not v[0]:
                continue
            mg = v[1] / v[2] if v[2] else float("nan")
            print(f"{c:<14} {v[0]:>7} {100*v[0]/usable:>5.1f}% {mg:>9.2f}  "
                  + " ".join(f"{bands[c].get(b, 0):>7}" for b in BANDS))
    print(f"\n=== TOTALS usable={gtot} segments-corrected-instances~{gcorr:.0f} "
          f"raw={gi}  boundary-touching-records={gctl} "
          f"({100*gctl/max(gtot,1):.2f}%)")
    for c in CLASSES[:-1]:
        v = g.get(c, [0, 0, 0])
        if v[0]:
            mg = v[1] / v[2] if v[2] else float("nan")
            print(f"  {c:<14} {v[0]:>7}  mean-gap {mg:>6.2f}")


main()
