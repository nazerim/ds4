"""Confidence-signal probe: does the captured h (3 taps, fp8) predict
whether the next dspark cycle commits deep (streak >= 1 / >= 3)?

Night-run experiment #2. Two purposes:
  1. feature-quality audit — proves the fp8 capture carries usable signal
     for the planned confidence-BCE objective;
  2. ceiling sketch — an oracle-ish AUC bounds how good a retrained
     confidence head can get from h alone at frontier time.

Split: train day1+2+4, holdout day3+active. MLP 12288->512->2 (tasks
streak>=1, >=3). Reports AUC per task, stratified by boundary-touch.

Usage: uv run python -m dspark.confidence_probe [--n N]
"""
from __future__ import annotations

import glob
import json
import os
import struct
import sys

import numpy as np
import torch
from torch import nn

from .data_contract import (HEADER_BYTES, RECORD_BYTES, STREAK_CLAMP,
                            decode_e4m3)

CORPUS = "/Volumes/FireCuda520/ds4-train"
TRAIN_DAYS = ["20261029-day1", "20261029-day2", "20261101-day4"]
HOLD_DAYS = ["20261029-day3", "active"]
HDR = struct.Struct("<IiBBBB5i")
CTL = {128806, 128807, 128808, 128809, 128810, 128811, 128812, 128813,
       128814, 128825, 128829}


def sample(days, n_want, seed=0):
    """Collect (file, idx) of usable, streak-known records; then load feats."""
    rng = np.random.default_rng(seed)
    pool = []
    for d in days:
        for rp in sorted(glob.glob(os.path.join(CORPUS, d, "train_pid*.recs"))):
            size = os.path.getsize(rp)
            n = size // RECORD_BYTES
            with open(rp, "rb") as fd:
                for i in range(n):
                    fd.seek(i * RECORD_BYTES)
                    r = HDR.unpack(fd.read(HEADER_BYTES))
                    pos, seed_tok, streak, nlab, nfeat = r[:5]
                    if nlab == 0 or streak >= STREAK_CLAMP or nfeat != 3:
                        continue
                    pool.append((rp, i, streak,
                                 any(t in CTL for t in r[6:11])))
    rng.shuffle(pool)
    pool = pool[:n_want]
    feats = np.empty((len(pool), 12288), dtype=np.float32)
    y1 = np.zeros(len(pool), dtype=np.float32)
    y3 = np.zeros(len(pool), dtype=np.float32)
    ctl = np.zeros(len(pool), dtype=bool)
    for j, (rp, i, streak, c) in enumerate(pool):
        with open(rp, "rb") as fd:
            fd.seek(i * RECORD_BYTES + HEADER_BYTES)
            feats[j] = decode_e4m3(memoryview(fd.read(12288)))
        y1[j] = streak >= 1
        y3[j] = streak >= 3
        ctl[j] = c
    return feats, y1, y3, ctl


def auc(score, y):
    """Tie-safe Mann-Whitney AUC (average midranks)."""
    y = y.astype(int)
    n1, n0 = int(y.sum()), int((1 - y).sum())
    if n1 == 0 or n0 == 0:
        return None
    _, inv, counts = np.unique(score, return_inverse=True,
                               return_counts=True)
    cum = counts.cumsum()
    ranks = cum[inv] - (counts[inv] + 1) / 2.0 + 1.0
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def main():
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 15000
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"device={dev} n={n}")
    xtr, a1, a3, ctr = sample(TRAIN_DAYS, n)
    xho, b1, b3, cho = sample(HOLD_DAYS, max(n // 4, 2000), seed=1)
    print(f"train {xtr.shape} pos1={a1.mean():.3f} pos3={a3.mean():.3f} "
          f"boundary={ctr.mean():.3f} | holdout {xho.shape} "
          f"(HOLD_DAYS includes live append-only 'active': holdout grows "
          f"between runs; freeze by banking before decision runs)")

    t_xtr = torch.from_numpy(xtr).to(dev)
    model = nn.Sequential(nn.Linear(12288, 512), nn.ReLU(),
                          nn.Linear(512, 2)).to(dev)
    tgt = torch.stack([torch.from_numpy(a1), torch.from_numpy(a3)], 1).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=1e-2)
    lossf = nn.BCEWithLogitsLoss()
    bs = 512
    N = len(t_xtr)
    for ep in range(8):
        perm = torch.randperm(N)
        tot = 0.0
        for i in range(0, N, bs):
            idx = perm[i:i + bs]
            opt.zero_grad(set_to_none=True)
            loss = lossf(model(t_xtr[idx]), tgt[idx])
            loss.backward()
            opt.step()
            tot += float(loss.detach()) * len(idx)
        print(f"  epoch {ep} loss {tot/N:.4f}", flush=True)

    with torch.no_grad():
        s = model(torch.from_numpy(xho).to(dev)).cpu().numpy()
    def strat(score, y, mask, name, kmin=100):
        v = auc(score[mask], y[mask])
        if v is not None and mask.sum() >= kmin:
            r[name] = round(v, 4)

    r = {"train_pos_rates": [float(a1.mean()), float(a3.mean())],
         "holdout_auc_ge1": round(auc(s[:, 0], b1) or float("nan"), 4),
         "holdout_auc_ge3": round(auc(s[:, 1], b3) or float("nan"), 4)}
    strat(s[:, 0], b1, cho, "holdout_auc_ge1_boundary")
    strat(s[:, 0], b1, ~cho, "holdout_auc_ge1_plain")
    print(json.dumps(r, indent=1))
    r = {k: v for k, v in r.items() if v is not None
           and not (isinstance(v, float) and np.isnan(v))}
    out = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "CONFIDENCE_PROBE.json")
    with open(out, "w") as fd:
        json.dump(r, fd, indent=1, allow_nan=False)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
