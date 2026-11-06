"""DSpark recalibration audit - step A/B (PLAN-DSK-CALIBRATION-AUDIT v2).

Re-scores the PARITY substrate's stored gate-time rows through the SAME
head the era's engine ran, and reconciles against the engine's own
post-gate dlen:

  M1 = frac of KEPT rows (d < dlen) whose OFFLINE score < .45.
       The engine kept them => live scores said >=.45. Offline recompute
       of the identical stored row below .45 => the live tensor differed
       from the stored tensor => engine-side seam (plumbing bug). ~0 =>
       plumbing innocent; everything the gate saw is on record.
  Retained-rate = mean(dlen)/5 per era (raw block is always 5; parity
       props past dlen are zero-filled, rows past dlen stale - so the
       would-have-been-cut population is unmeasurable from parity, and
       the discriminator uses CONTRAST:
  B-discriminator:
       official-era retained-rate (pid9717) vs confcal3-era retained-rate
       (49377/49701): confcal3 materially lower => head cuts on live
       traffic => sim's 13% undersold coverage => population issue in
       the SIM (slab cycle mix); confcal3 ~= official (both ~inert) =>
       head genuinely over-predicts on live feature distributions
       => tau* re-calibration is the ONLY lever; M1>0 => engine seam bug
       (samples listed) -> ds4.c investigation, not statistics.

Pairing mirrors official3c exactly: seed = RECS r[1] else r[6]; prev_0 =
seed, prev_d = props[d-1]; w1 from the f32 tensors npz (S5-proven
dequant-identical to support gguf); blade confidence_proj = q8_0 dequant
via the repack tool's proven block loop.

Usage: python3 audit_recal_20261106.py --out /tmp/dska.json \
   <recs> <parity> <blade|official> <label> [<recs> <parity> <head> <label>...]
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import struct
import sys

import numpy as np

sys.path.insert(0, "/Users/naz/Projects/ds4/tools")
sys.path.insert(0, "/Users/naz/Projects/ds4/trainer")

from repack_dspark_tensor import q8_0_dequant, walk_tensors  # noqa: E402
from labels3c import (PARITY_HDR, PARITY_STRIDE, PAR_PROPS,  # noqa: E402
                      REC_HDR, RECORD_BYTES, N_EMBD)

THRESH, SLOT = 0.45, 5
BLADE = ("/Volumes/FireCuda520/ds4-train/repack_test/"
         "support_AB_confcal3.gguf")
NPZ = ("/Volumes/FireCuda520/ds4-train/tensors-20261101/"
       "mtp_nonexpert_f32.npz")


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def head_weights(kind):
    z = np.load(NPZ)
    w1 = z["mtp.2.markov_head.markov_w1.weight"].astype(np.float32)
    if kind == "official":
        w = z["mtp.2.confidence_head.proj.weight"].astype(
            np.float32).reshape(-1)
    elif kind == "blade":
        t = walk_tensors(BLADE)["mtp.2.confidence_head.proj.weight"]
        ne = int(np.prod(t["dims"]))
        with open(BLADE, "rb") as f:
            f.seek(t["offset"])
            blob = f.read((ne // 32) * 34)
        w = np.asarray(q8_0_dequant(blob), np.float32).reshape(-1)
    else:
        raise SystemExit(f"head kind {kind}")
    assert w.size == N_EMBD + 256, w.size
    return w[:N_EMBD].copy(), w[N_EMBD:].copy(), w1


def audit(recs_path, parity_path, w_h, w_m, w1, label):
    n = min(os.path.getsize(recs_path) // RECORD_BYTES,
            os.path.getsize(parity_path) // PARITY_STRIDE)
    fr = open(recs_path, "rb"); fp = open(parity_path, "rb")
    mr = mmap.mmap(fr.fileno(), 0, access=mmap.ACCESS_READ)
    mp = mmap.mmap(fp.fileno(), 0, access=mmap.ACCESS_READ)
    # pass 1: collect kept cycles (row stream + prev ids), like official3c
    prevs, rowmat, dlens, poss = [], [], [], []
    for i in range(n):
        r = REC_HDR.unpack_from(mr, i * RECORD_BYTES)
        nl = r[3]
        pos, seed = int(r[0]), int(r[1])
        labels = list(r[6:6 + nl])
        base = i * PARITY_STRIDE
        ppos, _hs, dlen, _st, _rr = PARITY_HDR.unpack_from(mp, base)
        if ppos != pos or dlen < 2 or dlen > SLOT:
            continue
        props = list(PAR_PROPS.unpack_from(mp, base + 12))
        seed_tok = seed if seed >= 0 else labels[0]
        rows = np.frombuffer(mp[base + 32: base + PARITY_STRIDE],
                             np.float32).reshape(SLOT, N_EMBD)[:dlen]
        rowmat.append(rows)
        prevs.extend([seed_tok] + props[:dlen - 1])
        dlens.append(dlen)
        poss.append(pos)
    mp.close(); mr.close(); fr.close(); fp.close()
    if not rowmat:
        return {label: {"cycles": 0}}
    H = np.concatenate(rowmat, 0)                    # (R,4096)
    P = np.asarray(prevs, np.int64)
    lens = np.asarray(dlens)
    R = H.shape[0]
    assert P.size == R and lens.size == sum(x > 0 for x in [1]) * 0 + len(lens)
    # pass 2: vectorized offline scores
    score = H @ w_h + w1[P] @ w_m
    p = _sigmoid(score)
    kept_below = int((p < THRESH).sum())
    # per-cycle predicted dlen = leading >=thr run; observed = lens
    ge = p >= THRESH
    pred = np.empty(len(lens), np.int64)
    off = 0
    seams = []
    for c, L in enumerate(lens):
        run = 0
        while run < L and ge[off + run]:
            run += 1
        pred[c] = run
        if run < L and len(seams) < 12:
            seams.append([int(poss[c]), int(L), int(run),
                          round(float(p[off + run]), 4)])
        off += L
    assert off == R
    bins = {}
    for c in range(len(lens)):
        b = ("0-8k" if poss[c] < 8192 else
             "8-32k" if poss[c] < 32768 else "32k+")
        d = bins.setdefault(b, {"cycles": 0, "sum_dlen": 0, "pred_lt_obs": 0})
        d["cycles"] += 1
        d["sum_dlen"] += int(lens[c])
        d["pred_lt_obs"] += int(pred[c] < lens[c])
    out = {
        "cycles": int(len(lens)), "rows": int(R),
        "kept_rows_below_thr_M1": [kept_below, int(R),
                                   round(kept_below / R, 5)],
        "mean_dlen": round(float(lens.mean()), 3),
        "retained_rate": round(float(lens.mean()) / SLOT, 4),
        "cyclic_pred_lt_obs": int(sum(1 for c in range(len(lens))
                                      if pred[c] < lens[c])),
        "seam_samples(pos,dlen,predcut,pscore)": seams,
        "by_depth": bins,
    }
    return {label: out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("quad", nargs="*")
    a = ap.parse_args()
    cache, res = {}, {}
    it = iter(a.quad)
    for recs, par, kind, label in zip(it, it, it, it):
        if kind not in cache:
            cache[kind] = head_weights(kind)
        res.update(audit(recs, par, *cache[kind], label))
        print(label, "->", json.dumps(
            {k: res[label][k] for k in ("cycles", "retained_rate",
                                        "kept_rows_below_thr_M1")}))
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    print("WROTE", a.out)


if __name__ == "__main__":
    main()
