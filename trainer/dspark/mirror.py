"""Offline DSpark mirror: validate engine head math from parity dumps.

Milestone 1 (this file's first function): CONFIDENCE-GATE PARITY.
Engine (ds4.c dspark_apply_markov_confidence_lazy_runtime ~37044): for slot d,
features = hidden_row[d] (4096) ++ markov_w1[prev_token] (256), where
prev_token = proposal[d-1] (and first_prev_token at d=0); conf_logit =
confidence_proj @ features; proposal exists iff sigmoid_stable(conf_logit) >=
threshold (0.45 in production). Every dumped slot passed the gate BY
CONSTRUCTION, so a faithful reconstruction must show ZERO violations - any
sizable violation rate means the mirror math/layout is wrong.

Parity record = 12 B header + 20 B props + 5x4096 f32 conf-hidden rows,
stride 81,952. Rows beyond dlen are stale static-buffer residue: NEVER read
(CAPTURE-SCHEMA / DSPARK-STATE-20261102 loader rule).
"""
from __future__ import annotations

import mmap
import struct
from dataclasses import dataclass

import numpy as np

PARITY_STRIDE = 81952
PARITY_HDR = struct.Struct("<IiBBH")  # pos, seed, dlen, stages, rsvd
PROPS_OFF = 12
HID_OFF = 32
HID_ROW_BYTES = 5 * 4096 * 4
N_EMBD = 4096
RANK = 256
VOCAB = 129280
CONF_TH = 0.45  # production default (ds4-server.sh)

_npz_key_cache: dict[str, dict[str, np.ndarray]] = {}


def load_head_weights(npz_path: str) -> dict[str, np.ndarray]:
    """The four trainable tensors of the final-stage head (all f32 already)."""
    if npz_path not in _npz_key_cache:
        src = np.load(npz_path)
        _npz_key_cache[npz_path] = {
            "markov_w1": src["mtp.2.markov_head.markov_w1.weight"],
            "markov_w2": src["mtp.2.markov_head.markov_w2.weight"],
            "confidence_proj": src["mtp.2.confidence_head.proj.weight"],
        }
    return _npz_key_cache[npz_path]


def sigmoid_stable(x: np.ndarray | float) -> np.ndarray | float:
    """Engine sigmoid_stable equivalent, vectorized, no overflow."""
    x = np.asarray(x, dtype=np.float64)
    out = np.empty_like(x)
    pos = x >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    ex = np.exp(x[~pos])
    out[~pos] = ex / (1.0 + ex)
    return out


@dataclass
class ParityCycle:
    pos: int
    seed: int
    dlen: int
    stages: int
    props: np.ndarray          # int32[5]
    hidden: np.ndarray         # float32[5,4096]  (rows >= dlen are stale!)


def iter_parity(path: str, limit: int | None = None):
    """Memory-map record walk (5.4 GB files); yields ParityCycle."""
    f = open(path, "rb")
    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    n = len(mm) // PARITY_STRIDE
    if limit is not None:
        n = min(n, int(limit))
    try:
        for i in range(n):
            base = i * PARITY_STRIDE
            pos, seed, dlen, stages, rsvd = PARITY_HDR.unpack_from(mm, base)
            props = np.frombuffer(bytes(mm[base + PROPS_OFF:
                                           base + PROPS_OFF + 20]),
                                  dtype=np.int32)
            hid = np.frombuffer(bytes(mm[base + HID_OFF:
                                         base + HID_OFF + HID_ROW_BYTES]),
                                dtype=np.float32).reshape(5, N_EMBD)
            yield pos, seed, dlen, stages, rsvd, props, hid
    finally:
        try:
            mm.close()
        except BufferError:      # lingering view refs: let GC handle it
            pass
        f.close()


def conf_replay(w: dict[str, np.ndarray], hidden: np.ndarray,
                props: np.ndarray, dlen: int,
                first_prev: int | None = None):
    """Reconstruct sigmoid(conf) per dumped slot. Slot d>=1 uses
    w1[props[d-1]]; slot 0 uses first_prev when provided (else skipped)."""
    proj = w["confidence_proj"][0]              # [4352]
    w1 = w["markov_w1"]                         # [VOCAB,256]
    out = []
    for d in range(dlen):
        prev = props[d - 1]
        if d == 0:
            if first_prev is None:
                out.append(None)
                continue
            prev = first_prev
        if not (0 <= int(prev) < VOCAB):        # stale/sentinel guard (N6)
            out.append(None)
            continue
        feats = np.concatenate([hidden[d], w1[int(prev)].astype(np.float64)])
        out.append(float(sigmoid_stable(float(proj @ feats))))
    return out


def run_conf_parity(path: str, npz_path: str, max_cycles: int = 20000,
                    thr: float = CONF_TH, verbose: bool = True):
    """Milestone-1 experiment: violation rate over slots d>=1 across cycles."""
    w = load_head_weights(npz_path)
    viol = seen = cycles = 0
    confs: list[float] = []
    for k, (pos, seed, dlen, stages, rsvd, props, hid) in enumerate(
            iter_parity(path, max_cycles)):
        cycles = k + 1
        if dlen < 2:
            continue
        vals = conf_replay(w, hid, props, dlen)
        for d in range(1, dlen):
            v = vals[d]
            if v is None:
                continue
            seen += 1
            confs.append(v)
            if v < thr:
                viol += 1
    rate = viol / seen if seen else float("nan")
    if not confs:
        print(f"cycles={cycles} no replayable slots (dlen<2 everywhere)")
        return {"slots": 0, "violations": 0, "rate": rate,
                "median_conf": None}
    c = np.array(confs)
    if verbose:
        print(f"cycles={cycles}  slots(d>=1)={seen}  violations(<{thr})={viol}"
              f"  rate={rate:.4%}")
        print(f"conf dist: p05={np.percentile(c,5):.3f} p25={np.percentile(c,25):.3f}"
              f" med={np.percentile(c,50):.3f} p90={np.percentile(c,90):.3f} mean={c.mean():.3f}")
        near = float((c < thr * 1.05).mean())
        print(f"fraction of dumped slots with conf < {thr*1.05:.2f} (near-threshold): {near:.3%}")
    return {"slots": seen, "violations": viol, "rate": rate,
            "median_conf": float(np.median(c)) if len(c) else None}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="DSpark mirror: confidence-gate parity")
    ap.add_argument("parity", help="parity-*.bin file")
    ap.add_argument("--npz", default="/Volumes/FireCuda520/ds4-train/"
                    "tensors-20261101/mtp_nonexpert_f32.npz")
    ap.add_argument("--cycles", type=int, default=20000)
    a = ap.parse_args()
    run_conf_parity(a.parity, a.npz, a.cycles)
