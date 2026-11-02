"""Markov transition head: official baseline vs corpus-adapted fine-tune.

The dspark markov head scores draft candidates by a rank-256 bilinear over
vocab embeddings: logits = W2 @ W1[prev_token]. Night-run experiment #1:
evaluate the OFFICIAL head on our real agent traffic (its true deployment
distribution), then fine-tune on banked bigrams and measure holdout gains —
engine-free. Bigrams are (seed, labels[1]) of every usable record with
n_label>=2, i.e. (v[pos], v[pos+1]) from the committed stream.

Split: train = day1+day2+day4 (+ day3 optional), holdout = day3 (+active).
Eval metrics per split AND per class stratum (control-window vs plain) —
the weak-class lens from the corpus audit.

Usage: uv run python -m dspark.markov_ft [--epochs N] [--lr X] [--subsets]
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import torch
from torch import nn

from .data_contract import VOCAB
from .dataset import corpus_segments
from .model import GGUF_TO_MODULE  # noqa (import guard: contract exists)

TENSOR_DIR = "/Volumes/FireCuda520/ds4-train/tensors-20261101"
NPZ = os.path.join(TENSOR_DIR, "mtp_nonexpert_f32.npz")
CORPUS = "/Volumes/FireCuda520/ds4-train"
# holdout is FROZEN day-3 only: 'active' is append-only live data and would
# make results irreproducible between runs (review MAJOR, night-run #2)
TRAIN_DAYS = ["20261029-day1", "20261029-day2", "20261101-day4"]
HOLD_DAYS = ["20261029-day3"]
CTL = {128806, 128807, 128808, 128809, 128810, 128811, 128812, 128813,
       128814, 128825, 128829}


def collect_bigrams(days):
    xs, ys, ctl = [], [], []
    for s in corpus_segments(days, root=CORPUS):
        for r in s.usable:
            if r.n_label >= 2 and 0 <= r.seed < VOCAB and 0 <= r.labels[1] < VOCAB:
                xs.append(r.seed)
                ys.append(r.labels[1])
                # class lens: does the cycle's window touch a boundary?
                ctl.append(any(t in CTL for t in r.labels[:r.n_label]) or
                           r.seed in CTL)
    return (torch.tensor(xs, dtype=torch.long),
            torch.tensor(ys, dtype=torch.long),
            torch.tensor(ctl, dtype=torch.bool))


def load_official():
    src = np.load(NPZ)
    w1 = torch.from_numpy(src["mtp.2.markov_head.markov_w1.weight"])  # [V,256]
    w2 = torch.from_numpy(src["mtp.2.markov_head.markov_w2.weight"])
    return w1, w2


@torch.no_grad()
def topk_hits(w1, w2, x, y, dev, batch=1024):
    w1 = w1.to(dev)
    w2 = w2.to(dev)
    hits1 = hits3 = 0
    for i in range(0, len(x), batch):
        xb = x[i:i + batch].to(dev)
        yb = y[i:i + batch].to(dev)
        z = (w1[xb] @ w2.T)                   # [B, V]
        m3 = z.topk(3, dim=-1).indices
        hits1 += int((m3[:, 0] == yb).sum())
        hits3 += int((m3 == yb[:, None]).any(dim=-1).sum())
    n = len(x)
    return hits1 / n, hits3 / n


def train_markov(w1, w2, x, y, dev, epochs=3, lr=1e-4, batch=1024):
    w1t = w1.clone().to(dev).requires_grad_(True)
    w2t = w2.clone().to(dev).requires_grad_(True)
    opt = torch.optim.Adam([w1t, w2t], lr=lr)
    n = len(x)
    for ep in range(epochs):
        perm = torch.randperm(n)
        tot = 0.0
        for i in range(0, n, batch):
            idx = perm[i:i + batch]
            xb = x[idx].to(dev)
            yb = y[idx].to(dev)
            z = w1t[xb] @ w2t.T                       # [B, V]
            loss = nn.functional.cross_entropy(z, yb)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            tot += float(loss.detach()) * len(idx)
        print(f"  epoch {ep}: loss {tot/n:.4f}", flush=True)
    return w1t.detach().cpu(), w2t.detach().cpu()


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(max(0, c - h), 4), round(min(1, c + h), 4)]


def main():
    torch.manual_seed(0)
    np.random.seed(0)
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"device={dev}")
    w1, w2 = load_official()
    xtr, ytr, ctr = collect_bigrams(TRAIN_DAYS)
    xho, yho, cho = collect_bigrams(HOLD_DAYS)
    print(f"train bigrams={len(xtr)} holdout={len(xho)} "
          f"(holdout boundary-class={int(cho.sum())})")

    o1, o3 = topk_hits(w1, w2, xtr, ytr, dev)
    h1, h3 = topk_hits(w1, w2, xho, yho, dev)
    print(f"OFFICIAL  train top1={o1:.3f} top3={o3:.3f} | "
          f"holdout top1={h1:.3f} top3={h3:.3f}")
    hb1, hb3 = topk_hits(w1, w2, xho[cho], yho[cho], dev) if cho.any() else (0, 0)
    hp1, hp3 = topk_hits(w1, w2, xho[~cho], yho[~cho], dev)
    print(f"OFFICIAL  holdout boundary top1={hb1:.3f} | plain top1={hp1:.3f}")

    epochs = int(sys.argv[sys.argv.index("--epochs") + 1]) \
        if "--epochs" in sys.argv else 3
    results = {"protocol": {
        "holdout": "frozen day-3; subsets nested-prefix randperm seed=0; "
                   "each subset re-inits from OFFICIAL (fair curve); "
                   "2 epochs per subset point (gradient budget rises with "
                   "size — knee is a joint size+epochs statement)",
        "train_days": TRAIN_DAYS, "holdout_days": HOLD_DAYS},
        "official": {"train_top1": round(o1, 4), "train_top3": round(o3, 4),
                     "holdout_top1": round(h1, 4), "holdout_top3": round(h3, 4),
                     "holdout_boundary_top1": round(hb1, 4),
                     "holdout_boundary_n": int(cho.sum()),
                     "holdout_plain_top1": round(hp1, 4)}}

    curve = []
    for frac in (0.0625, 0.125, 0.25, 0.5, 1.0):
        k = int(len(xtr) * frac)
        sub = torch.randperm(len(xtr))[:k]
        a1, a2 = train_markov(w1, w2, xtr[sub], ytr[sub], dev, epochs=2)
        s1, s3 = topk_hits(a1, a2, xho, yho, dev)
        curve.append({"frac": frac, "pairs": k, "top1": round(s1, 4),
                      "top3": round(s3, 4)})
        print(f"  subset {frac:>6} pairs={k:>7} -> top1={s1:.3f} "
              f"top3={s3:.3f}", flush=True)
    results["scaling"] = curve

    a1, a2 = train_markov(w1, w2, xtr, ytr, dev, epochs=epochs)
    f1, f3 = topk_hits(a1, a2, xho, yho, dev)
    fb1, _ = topk_hits(a1, a2, xho[cho], yho[cho], dev) if cho.any() else (0, 0)
    fp1, _ = topk_hits(a1, a2, xho[~cho], yho[~cho], dev)
    print(f"FINETUNE  holdout top1={f1:.3f} top3={f3:.3f} | "
          f"boundary top1={fb1:.3f} plain top1={fp1:.3f}")
    nb = int(cho.sum())
    results["finetune"] = {"holdout_top1": round(f1, 4),
                           "holdout_top3": round(f3, 4),
                           "holdout_boundary_top1": round(fb1, 4),
                           "holdout_boundary_ci95": wilson(round(fb1 * nb), nb),
                           "holdout_plain_top1": round(fp1, 4)}
    torch.save({"markov_w1": a1, "markov_w2": a2},
               os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "markov_ft.pt"))
    out = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "MARKOV_RESULTS.json")
    json.dump(results, open(out, "w"), indent=1, allow_nan=False)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
