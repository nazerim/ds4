"""Official-head replay on the S1a conf slab (S4 baseline; 3C night).

Replays the OFFICIAL confidence head exactly as the engine computes it
(ds4.c:37033-37064 batched loop): per slot d, logit =
    h_d @ w_h + markov_w1[prev_tok_d] @ w_m
with w = official confidence_proj (1,4352) = [w_h 4096 | w_m 256], and
    prev_tok_0 = seed token (labels[0]; engine `first_prev_token` = the
                 committed token at pos, ds4.c:80320-80324),
    prev_tok_d = props[d-1] (engine prev_token = proposal[draft-1]).
TRAP (S4b review): the PARITY file's hdr.seed is NOT this seed —
ds4.c:~76204 writes hdr.seed = props[0], the first DRAFT token. Joining
prev-token reconstruction on the parity seed field gives a silent
off-by-one; the seed must come from the RECS header (kept_cycles does).
Official weights: mtp_nonexpert_f32.npz (dequant bit-identity to the
support GGUF proven in S5). Labels/features: conf_s1a_20261103.npz.

The keep-pass reconstruction (recs+parity -> kept record indices) is
CROSS-ASSERTED against slab meta (pos,dlen,w,nl,k_lab) cycle-by-cycle;
any mismatch aborts.

Outputs: report json + scores npy (all rows, slab order) ->
/Volumes/FireCuda520/ds4-train/labels3c/run_officialreplay1/.

Usage:
  uv run python -m dspark.official3c --slab ... --recs ... --parity ...
      --tensors ... --out-dir ...
"""
from __future__ import annotations

import argparse
import json
import mmap
import os

import numpy as np

from .confidence_probe import auc
from .eval3c import gate_stats, masked_views
from .labels3c import (PAR_PROPS, PARITY_HDR, PARITY_STRIDE, REC_HDR,
                       RECORD_BYTES, N_EMBD)


def kept_cycles(recs_path, parity_path):
    """Rebuild build_pair(conf) keep decisions; return per-cycle arrays."""
    n = os.path.getsize(recs_path) // RECORD_BYTES
    npar = os.path.getsize(parity_path) // PARITY_STRIDE
    with open(recs_path, "rb") as f:
        mr = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        with open(parity_path, "rb") as g:
            mp = mmap.mmap(g.fileno(), 0, access=mmap.ACCESS_READ)
            idx, pos_a, seed_a, dlen_a, w_a, klab_a, nl_a, props_a = \
                [], [], [], [], [], [], [], []
            for i in range(min(n, npar)):
                r = REC_HDR.unpack_from(mr, i * RECORD_BYTES)
                nl = r[3]
                pos, seed = int(r[0]), int(r[1])
                labels = list(r[6:6 + nl])
                base = i * PARITY_STRIDE
                ppos, _ps, dlen, _st, _rr = PARITY_HDR.unpack_from(mp, base)
                if ppos != pos:
                    raise RuntimeError(f"index join broken at {i}")
                if nl < 2 or dlen == 1 or dlen < 1 or dlen > 5:
                    continue
                props = list(PAR_PROPS.unpack_from(mp, base + 12))
                w = min(dlen, nl - 1)
                klab = next((d for d in range(w)
                             if props[d] != labels[d + 1]), None)
                idx.append(i)
                pos_a.append(pos)
                seed_a.append(seed if seed >= 0 else labels[0])
                dlen_a.append(int(dlen))
                w_a.append(w)
                klab_a.append(-1 if klab is None else klab)
                nl_a.append(nl)
                props_a.append(props)
            mp.close()
        mr.close()
    return (np.array(idx, np.int64), np.array(pos_a, np.int64),
            np.array(seed_a, np.int64), np.array(dlen_a, np.int64),
            np.array(w_a, np.int64), np.array(klab_a, np.int64),
            np.array(nl_a, np.int64), np.array(props_a, np.int64))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slab", required=True)
    ap.add_argument("--recs", required=True)
    ap.add_argument("--parity", required=True)
    ap.add_argument("--tensors",
                    default="/Volumes/FireCuda520/ds4-train/tensors-20261101/"
                    "mtp_nonexpert_f32.npz")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    slab = np.load(a.slab, allow_pickle=True)
    meta = slab["meta"]                     # pos,dlen,conv,klab,w,nl
    (idx, pos, seed, dlen, w, klab, nl, props) = kept_cycles(a.recs,
                                                             a.parity)
    # cross-assert reconstruction vs slab meta (cycle-by-cycle)
    if len(idx) != meta.shape[0]:
        raise SystemExit(f"kept cycles {len(idx)} != slab cycles "
                         f"{meta.shape[0]}")
    for col, ours, name in ((0, pos, "pos"), (1, dlen, "dlen"),
                            (3, klab, "klab"), (4, w, "w"), (5, nl, "nl")):
        if not np.array_equal(meta[:, col].astype(np.int64), ours):
            raise SystemExit(f"slab meta {name} mismatch vs reconstruction")

    z = np.load(a.tensors)
    w_off = z["mtp.2.confidence_head.proj.weight"].astype(
        np.float32).reshape(-1)
    w1 = z["mtp.2.markov_head.markov_w1.weight"].astype(np.float32)
    assert w_off.size == N_EMBD + 256, w_off.shape
    w_h, w_m = w_off[:N_EMBD], w_off[N_EMBD:]

    # prev-token per stored row: cycle rows d=0..dlen-1 -> seed, props[:d-1]
    total = int(dlen.sum())
    prev = np.zeros(total, np.int64)
    slot = np.zeros(total, np.int64)
    off = 0
    for c in range(len(idx)):
        k = int(dlen[c])
        prev[off] = seed[c]
        if k > 1:
            prev[off + 1:off + k] = props[c, :k - 1]
        slot[off:off + k] = np.arange(k)
        off += k
    assert off == total == slab["slot_label"].shape[0]

    h = slab["conf_rows"].astype(np.float32)
    base_h = h @ w_h                        # h-only component (H2 below)
    mk = w1[prev] @ w_m                     # markov component (H3 below)
    score_h1 = 1.0 / (1.0 + np.exp(-(base_h + mk)))       # engine pairing
    score_h2 = 1.0 / (1.0 + np.exp(-(base_h + w1[seed.repeat(dlen.tolist())]
                                    @ w_m)))              # all-prev=seed
    # per-hypothesis metrics through the shared eval machinery
    slot_idx, commit, valid, _ = masked_views(slab)
    y = commit.astype(np.float64)

    def metrics(sc):
        out = {"pooled_auc": round(float(auc(sc[valid], y[valid])), 4),
               "per_slot_auc": {}, "pooled_gate": None}
        for d in range(4):
            m = valid & (slot_idx == d)
            v = auc(sc[m], y[m])
            out["per_slot_auc"][d] = round(float(v), 4) if v is not None \
                else None
        out["pooled_gate"] = gate_stats(sc, y, valid)
        return out

    report = {"protocol": {
        "weights": "official confidence_proj + markov_w1 (f32 npz, S5-"
                   "verified bit-identical dequant)",
        "pairing_H1_engine": "prev_0=seed, prev_d=props[d-1] "
                             "(ds4.c:37033-37064, 80320)",
        "pairing_H2": "prev_d=seed for all d",
        "cycles_asserted_vs_slab_meta": int(len(idx)),
        "rows": total},
        "H1_engine_pairing": metrics(score_h1),
        "H2_seed_only_prev": metrics(score_h2)}
    with open(os.path.join(a.out_dir, "officialreplay_report.json"),
              "w") as f:
        json.dump(report, f, indent=1)
    np.save(os.path.join(a.out_dir, "official_h1_scores.npy"),
            score_h1.astype(np.float32))
    np.save(os.path.join(a.out_dir, "official_h1_prev_tokens.npy"), prev)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
