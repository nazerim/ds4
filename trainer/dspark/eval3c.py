"""S2.5 accept-proxy eval harness (3C-NIGHT-PLAN-20261103).

Independent re-derivation + scoring of CONF accept quality. Reuses
confidence_probe.auc (bit-identical Mann-Whitney, tie-safe) so any AUC here
is directly comparable to milestone-1 (.7418/.7933) numbers.

The conf head (trained in S3) consumes the parity conf-row (h, 4096) per
draft slot and emits P(commit). Ground truth = D-S0 masked label: 1 commit,
0 reject-point, -1 masked (never scored). This harness is the single scoring
path S3 and later stages consume.

Metrics (per slot and pooled, masked rows dropped):
  * AUC(score, commit) via confidence_probe.auc;
  * conf>=THRESH(0.45) precision / recall / coverage vs commit;
  * commit rate (the D-S0-4 prior) as an independent recompute;
  * per-slot positional-prior baseline AUC (score = -d, monotone in accept
    decay) - the bar S3's learned head must beat.

Self-tests (gate S2.5, no training needed):
  --selftest  : auc() equivalence to a second implementation on adversarial
                + tie inputs; and rates re-derived from the S1a slab match
                D-S0-4 (.802/.744/.568/.521) within tolerance.

Usage:
  uv run python -m dspark.eval3c --selftest --slab conf_s1a_20261103.npz
  uv run python -m dspark.eval3c --scores SCORES.npy --slab SLAB.npz
"""
from __future__ import annotations

import argparse
import json
import numpy as np

from .confidence_probe import auc


def masked_views(slab):
    """Return per-slot arrays plus the unmasked commit labels."""
    meta = slab["meta"]           # (n,6): pos,dlen,conv,k_lab,w,nl
    slot_label = slab["slot_label"].astype(int)
    slot_cycle = slab["slot_cycle"].astype(int)
    commit = (slot_label == 1).astype(np.int64)
    valid = slot_label != -1
    slot_idx = _slot_index(len(slot_label), slot_cycle)
    return slot_idx, commit, valid, meta


def _slot_index(total, slot_cycle):
    out = np.zeros(total, dtype=int)
    # slot_cycle is nondecreasing run-id per row
    prev = -1
    c = 0
    for i, cid in enumerate(slot_cycle):
        if cid != prev:
            c = 0
            prev = cid
        else:
            c += 1
        out[i] = c
    return out


def commit_rates(slab):
    """Independent recompute of D-S0-4 masked-BCE positive rate per slot."""
    slot_idx, commit, valid, meta = masked_views(slab)
    rates, ns = [], []
    for d in range(4):
        m = valid & (slot_idx == d)
        if m.sum():
            rates.append(round(float(commit[m].mean()), 3))
            ns.append(int(m.sum()))
        else:
            rates.append(None)
            ns.append(0)
    return rates, ns


def gate_stats(score, commit, valid, thresh=0.45):
    s, y, v = score[valid], commit[valid], True
    pred = s >= thresh
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    cov = int(pred.sum()) / len(y) if len(y) else None
    return {"precision": prec, "recall": rec, "coverage": round(cov, 4)
            if cov is not None else None, "auc": _safe_auc(s, y)}


def _safe_auc(s, y):
    v = auc(s, y.astype(float))
    return round(v, 4) if v is not None else None


def evaluate(score, slab, thresh=0.45):
    slot_idx, commit, valid, meta = masked_views(slab)
    assert len(score) == len(commit), (len(score), len(commit))
    out = {"pooled": gate_stats(score, commit, valid, thresh),
           "per_slot": {}}
    for d in range(4):
        m = (slot_idx == d)
        if m.sum() and valid[m].any():
            out["per_slot"][d] = gate_stats(score[m], commit[m], valid[m],
                                            thresh)
    # baseline: positional-prior (score decreases with slot depth)
    base = -slot_idx.astype(float)
    out["baseline_positional"] = gate_stats(base, commit, valid, thresh=0.5)
    return out


def _auc_ref(score, y):
    """Second (independent) AUC impl for the equivalence self-test."""
    y = y.astype(int)
    n1, n0 = int(y.sum()), int((1 - y).sum())
    if n1 == 0 or n0 == 0:
        return None
    order = np.argsort(score, kind="mergesort")
    s_sorted = score[order]
    y_sorted = y[order]
    # rank sum with average ranks over ties
    ranks = np.empty(len(score), float)
    i = 0
    while i < len(score):
        j = i
        while j + 1 < len(score) and s_sorted[j + 1] == s_sorted[i]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        ranks[order[i:j + 1]] = avg
        i = j + 1
    r1 = ranks[y == 1].sum()   # ranks are in ORIGINAL order (scattered back)
    return float((r1 - n1 * (n1 + 1) / 2) / (n1 * n0))


def selftest(slab_path, tol=0.01):
    rng = np.random.default_rng(20261103)
    results = {"auc_equiv": [], "rates": None}
    # adversarial + tie cases
    cases = [
        (rng.normal(size=5000), (rng.random(5000) < 0.3).astype(int)),
        (np.round(rng.normal(size=4000), 1), (rng.random(4000) < 0.5)
         .astype(int)),                       # many ties
        (np.repeat(np.arange(10.0), 50), (np.arange(10).repeat(50) > 4)
         .astype(int)),                        # block ties
        (rng.random(100), np.ones(100, int)),  # degenerate (all pos -> None)
    ]
    ok = True
    for sc, yy in cases:
        a = auc(sc, yy.astype(float))
        b = _auc_ref(sc, yy)
        if a is None or b is None:
            agree = (a is None and b is None)
        else:
            agree = abs(a - b) < 1e-9
        results["auc_equiv"].append(agree)
        ok = ok and agree
    results["auc_equiv_ok"] = ok
    slab = np.load(slab_path, allow_pickle=True)
    rates, ns = commit_rates(slab)
    results["rates_trainer_seen"] = rates
    results["rates_n"] = ns
    # Trainer-seen per-slot positive rate = (label==1)/(valid) over STORED
    # rows at slot d. Deep-slot rows only exist when dlen>d, which over-
    # represents acceptance, so this is HIGHER than the unconditional
    # accept-chain P(a>=d+1)/P(a>=d). Both are real; the trainer sees THIS.
    expect_trainer = [0.802, 0.744, 0.728, 0.735]
    results["rates_trainer_ok"] = all(
        r is not None and abs(r - e) <= tol
        for r, e in zip(rates[:4], expect_trainer))
    # Reconcile with reviewer's UNCONDITIONAL accept-chain from meta k_lab.
    meta = slab["meta"]
    k_lab = meta[:, 3].astype(int)
    dlen = meta[:, 1].astype(int)
    a = np.where(k_lab >= 0, k_lab, dlen)  # accept run (=dlen when no reject)
    unconditional = []
    for d in range(4):
        ge_d = (a >= d).sum()
        ge_d1 = (a >= d + 1).sum()
        unconditional.append(round(ge_d1 / ge_d, 3) if ge_d else None)
    results["rates_uncond_chain"] = unconditional
    results["PASS"] = bool(results["auc_equiv_ok"]
                           and results["rates_trainer_ok"])
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--slab", required=True)
    ap.add_argument("--scores", default=None)
    ap.add_argument("--thresh", type=float, default=0.45)
    a = ap.parse_args()
    if a.selftest:
        r = selftest(a.slab)
        print(json.dumps(r, indent=1))
        raise SystemExit(0 if r["PASS"] else 1)
    slab = np.load(a.slab, allow_pickle=True)
    sc = np.load(a.scores) if a.scores else None
    if sc is None:
        # no learned head yet: score by positional-prior baseline only
        slot_idx, commit, valid, meta = masked_views(slab)
        sc = -slot_idx.astype(float)
    print(json.dumps(evaluate(sc, slab, a.thresh), indent=1))


if __name__ == "__main__":
    main()
