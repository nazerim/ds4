"""S3 conf-only calibration run (3C-NIGHT-PLAN-20261103).

Trains a DEPLOYABLE confidence head on the S1a conf slab: single linear
over the 4096 parity conf-row + bias, BCE on the D-S0 three-case labels.
Deploy shape: official confidence_head.proj.weight is (1,4352) =
[h 4096 | markov-state 256]; we train the h-half and keep the markov-half
ZERO - a zero block is layout-valid and provably immune to any
engine-side markov-feature uncertainty. The markov half can be retrained
in S4 once the slab stores per-slot prev-token features.

Split: conv-disjoint (conv % 10 == 0 -> val; ~10%), never per-row.
Gate (from plan, S2.5-reviewed): val AUC must BEAT positional-prior
baseline (per-slot .5 / pooled ~.546) and be calibrated (ECE reported;
target small, esp. near the .45 gate). Milestone .7418/.7933 = ceiling
note only. Eval through eval3c.gate_stats/auc (shared machinery).

Memory: conf rows f16 -> MPS ~800 MB; batches f32. Well under L0 cap.

Usage: uv run python -m dspark.conf3c --slab conf_s1a_20261103.npz
       --out-dir DIR [--epochs N] [--lr F] [--seed S]
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import torch

from .confidence_probe import auc
from .eval3c import gate_stats, masked_views


def split(conv, val_mod=10):
    """Hashed conv split (S3 review MINOR-3): distribution-matched, not
    creation-order. conv = meta col 2."""
    conv = conv.astype(np.int64)
    h = ((conv * np.int64(2654435761)) >> np.int64(24)) % val_mod
    return h == 0


def ece(score, y, bins=10):
    """Expected calibration error, equal-width bins."""
    edges = np.linspace(0, 1, bins + 1)
    tot, acc = 0.0, 0.0
    for i in range(bins):
        m = (score >= edges[i]) & (score < edges[i + 1])
        n = int(m.sum())
        if n:
            tot += n * abs(score[m].mean() - y[m].mean())
            acc += n
    return round(tot / max(acc, 1), 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slab", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--wd", type=float, default=1e-4)
    ap.add_argument("--seed", type=int, default=20261104)
    ap.add_argument("--bias", action="store_true",
                    help="train a bias term (NOT deployable: engine conf is "
                         "a pure matvec, validator pins [4352,1] - S3 review "
                         "MAJOR; default off = deploy-faithful)")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    torch.manual_seed(a.seed)
    np.random.seed(a.seed)
    dev = "mps" if torch.backends.mps.is_available() else "cpu"

    slab = np.load(a.slab, allow_pickle=True)
    slot_idx, commit, valid, meta = masked_views(slab)
    rows = slab["conf_rows"]                      # f16 (total_slots, 4096)
    is_val_cycle = split(meta[:, 2])
    # row-level masks: cycle membership + unmasked label
    valc = is_val_cycle[slab["slot_cycle"].astype(np.int64)]
    row_val = valc & valid
    row_tr = (~valc) & valid
    y = torch.from_numpy(commit.astype(np.float32))

    x = torch.from_numpy(rows).to(dev)            # f16 on device
    w = torch.zeros(4096, 1, device=dev, requires_grad=True)
    params = [w]
    b = None
    if a.bias:
        b = torch.zeros(1, device=dev, requires_grad=True)
        params.append(b)
    opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=a.wd)
    xtr = x[row_tr].float()
    ytr = y.to(dev)[row_tr]
    n = xtr.shape[0]
    bs = 2048
    for ep in range(a.epochs):
        perm = torch.randperm(n, device=dev)
        tot = 0.0
        for i in range(0, n, bs):
            idx = perm[i:i + bs]
            opt.zero_grad(set_to_none=True)
            logit = xtr[idx] @ w
            if b is not None:
                logit = logit + b
            loss = torch.nn.functional.binary_cross_entropy_with_logits(
                logit.squeeze(1), ytr[idx])
            loss.backward()
            opt.step()
            tot += float(loss.detach()) * len(idx)
        print(f"epoch {ep} loss {tot/n:.4f}", flush=True)

    with torch.no_grad():
        alllogit = (x.float() @ w)
        if b is not None:
            alllogit = alllogit + b
        allsc = torch.sigmoid(alllogit).squeeze(1).cpu().numpy()
    # val-population positional-prior baseline (S3 review MINOR-2)
    trn = (~valc) & valid  # any-row split tag for val rates
    base_val = auc((-slot_idx.astype(float))[row_val],
                   commit.astype(float)[row_val])
    report = {"protocol": dict(epochs=a.epochs, lr=a.lr, wd=a.wd,
                               seed=a.seed, split="hash(conv)%10==0 val",
                               bias=a.bias,
                               model=("linear4096" + ("+bias" if a.bias
                                                      else "")
                                      + ", markov-half zeros"),
                               slab=os.path.basename(a.slab)),
              "counts": {"train_rows": int(row_tr.sum()),
                         "val_rows": int(row_val.sum()),
                         "train_cycles": int((~is_val_cycle).sum()),
                         "val_cycles": int(is_val_cycle.sum())},
              "val_slot_base_rates": [
                  round(float(commit[row_val & (slot_idx == d)].mean()), 4)
                  if (row_val & (slot_idx == d)).sum() else None
                  for d in range(4)],
              "baseline_auc_val_pop": round(float(base_val), 4)}
    # val metrics per slot + pooled (shared eval3c machinery)
    yv = commit.astype(np.float64)
    val_eval = {"pooled": gate_stats(allsc, yv, row_val)}
    for d in range(4):
        m = row_val & (slot_idx == d)
        if m.sum() and len(np.unique(yv[m])) == 2:
            val_eval[f"slot{d}"] = gate_stats(allsc, yv, m)
    val_eval["baseline_note"] = ("beat pooled ~0.546 / per-slot 0.5 from "
                                 "eval3c baseline; milestone .7418/.7933 "
                                 "ceiling-note only (different metric)")
    s = allsc[row_val]
    val_eval["ece_val"] = ece(s, yv[row_val])
    report["val"] = val_eval
    # gate-region calibration: rows with score in [.35,.55]
    g = row_val & (allsc >= 0.35) & (allsc <= 0.55)
    report["val"]["gate_region_n"] = int(g.sum())
    report["val"]["gate_region_pred_mean"] = (
        round(float(allsc[g].mean()), 4) if g.sum() else None)
    report["val"]["gate_region_obs_rate"] = (
        round(float(yv[g].mean()), 4) if g.sum() else None)
    report["PASS_auc"] = bool(val_eval["pooled"]["auc"] is not None
                              and val_eval["pooled"]["auc"] > base_val)
    print(json.dumps(report, indent=1))
    with open(os.path.join(a.out_dir, "confcal1_report.json"), "w") as f:
        json.dump(report, f, indent=1)
    np.save(os.path.join(a.out_dir, "confcal1_val_scores.npy"), allsc)
    np.savez(os.path.join(a.out_dir, "confcal1_weights.npz"),
             w=w.detach().cpu().numpy().reshape(-1),
             b=(b.detach().cpu().numpy() if b is not None
                else np.zeros(1, np.float32)),
             w_full_1x4352=np.concatenate(
                 [w.detach().cpu().numpy().reshape(-1),
                  np.zeros(256, np.float32)]).reshape(1, 4352))
    print("wrote", a.out_dir)
    raise SystemExit(0 if report["PASS_auc"] else 1)


if __name__ == "__main__":
    main()
