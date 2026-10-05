#!/usr/bin/env python3
"""Phase 1a reference: the UT-transform (fla-style) chunked gated-delta-rule
scan -- the formulation a real kernel would implement.

Derivation from the harness recurrence (gdn_reference, fp semantics 1:1):
  per token: S *= g; u_t = beta_t (v_t - (g_t S_{t-1}) @ k_t); S += outer(u_t, k_t)
  With gc_t = cumsum(log g) inside the chunk (gc_{c0-1} := 0):
    S_t = exp(gc_t) S_prev + sum_{s<=t} exp(gc_t - gc_s) u_s k_s^T
  hence u_t = beta_t v_t - sum_{s<t} T[t,s] u_s - beta_t exp(gc_t) (S_prev k_t)
    T[t,s] = exp(gc_t - gc_s) beta_t (k_t . k_s)   (strictly lower)
  so (I + T) U = P with P_t = beta_t v_t - beta_t exp(gc_t) S_prev k_t,
  solved by forward substitution; then per-token output
    o_t = exp(gc_t) (S_prev q_t) + sum_{s<=t} exp(gc_t - gc_s) (k_s . q_t) u_s
  and the chunk-end state S_c = exp(gc_C) S_prev + sum_s exp(gc_C - gc_s) u_s k_s^T.

All pairwise decay ratios are exp of a NEGATIVE log difference -- stable even
when per-step g ~ 1e-14 (where k/gamma would overflow). Drift is measured
against the serial fp64 anchor (probe's gen_head/serial kept here) for the
f64/f32 variants; f32 is the projected GPU kernel class.

usage: uv run --no-project --with numpy python gdn_chunk_ut_reference.py [heads]
"""
import sys

import numpy as np

from gdn_chunk_scan_probe import D, T, C, gen_head, serial, maxabs


def chunk_ut(g, beta, q, k, v, dtype):
    S = np.zeros((D, D), dtype=dtype)
    o = np.empty((T, D), dtype=dtype)
    for c0 in range(0, T, C):
        c1 = min(c0 + C, T)
        n = c1 - c0
        gt = g[c0:c1].astype(np.float64)
        bt = beta[c0:c1].astype(np.float64)
        kc = k[c0:c1].astype(np.float64)
        vc = v[c0:c1].astype(np.float64)
        qc = q[c0:c1].astype(np.float64)
        gc = np.cumsum(np.log(gt))                      # <= 0, monotone down
        # pairwise decay ratio matrix R[t,s] = exp(gc_t - gc_s), t>=s
        idx = np.arange(n)
        lower = idx[:, None] >= idx[None, :]
        R = np.where(lower, np.exp(gc[:, None] - gc[None, :]), 0.0)
        Gk = kc * (bt[:, None] * np.exp(gc))[:, None]   # beta_t exp(gc_t) k_t
        # strictly-lower coupling T[t,s] = R[t,s] beta_t (k_t.k_s), s < t
        KK = kc @ kc.T
        Tm = R * KK * bt[:, None]
        np.fill_diagonal(Tm, 0.0)
        # P_t = beta_t v_t - beta_t exp(gc_t) (S_prev k_t)
        Sk = np.asarray(S, np.float64) @ kc.T            # [dv][n]: S_prev k_t per col
        Sk = Sk.T                                         # [t][dv]
        P = (vc * bt[:, None] - Sk * (bt * np.exp(gc))[:, None])
        # forward substitution (I + T) U = P
        U = np.empty_like(P)
        for t in range(n):
            U[t] = P[t] - Tm[t, :t] @ U[:t]
        # outputs: o_t = exp(gc_t) (S_prev q_t) + sum_{s<=t} R[t,s](k_s.q_t) U_s
        KQ = np.where(lower, R * (qc @ kc.T), 0.0)
        o[c0:c1] = (np.asarray(S, np.float64) @ qc.T).T * np.exp(gc)[:, None] + KQ @ U
        # chunk-end state: S_c = exp(gc_last) S_prev + sum_s exp(gc_last-gc_s) u_s k_s^T
        last = np.exp(gc[-1] - gc)
        S = np.asarray(np.exp(gc[-1]) * np.asarray(S, np.float64) + (U * last[:, None]).T @ kc,
                       dtype=dtype)
        # store the f32-rounded U/k back into o loop? the kernel would compute
        # the dtype-rounded pieces; emulate: round inputs per chunk
        o = np.asarray(o, dtype)
    return o, S


def chunk_ut_f32(g, beta, q, k, v):
    """dtype-faithful version: solve in fp32 with fp32 pairwise ratios (the
    tensor-unit kernel class), triangular solve accumulates fp32."""
    dtype = np.float32
    S = np.zeros((D, D), dtype=dtype)
    o = np.empty((T, D), dtype=dtype)
    for c0 in range(0, T, C):
        c1 = min(c0 + C, T)
        n = c1 - c0
        gt = g[c0:c1].astype(dtype)
        bt = beta[c0:c1].astype(dtype)
        kc = k[c0:c1].astype(dtype)
        vc = v[c0:c1].astype(dtype)
        qc = q[c0:c1].astype(dtype)
        gc = np.cumsum(np.log(gt.astype(np.float64))).astype(dtype)
        idx = np.arange(n)
        lower = idx[:, None] >= idx[None, :]
        R = np.where(lower, np.exp(gc[:, None] - gc[None, :]).astype(dtype), dtype(0))
        expg = np.exp(gc).astype(dtype)
        KK = (kc @ kc.T).astype(dtype)
        Tm = (R * KK * bt[:, None]).astype(dtype)
        Sk = (S @ kc.T).T.astype(dtype)
        P = (vc * bt[:, None] - Sk * (bt * expg)[:, None]).astype(dtype)
        U = np.empty_like(P)
        for t in range(n):
            U[t] = P[t] - (Tm[t, :t] @ U[:t]) if t else P[t]
        KQ = np.where(lower, (R * (qc @ kc.T)).astype(dtype), dtype(0))
        o[c0:c1] = ((S @ qc.T).T * expg[:, None]).astype(dtype) + (KQ @ U).astype(dtype)
        last = np.exp(gc[-1] - gc).astype(dtype)
        S = (expg[-1] * S + (U * last[:, None]).T.astype(dtype) @ kc).astype(dtype)
    return o, S


def serial_fast(g, beta, q, k, v, dtype):
    return serial(g, beta, q, k, v, dtype)


if __name__ == "__main__":
    nh = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    print(f"UT-form chunked scan vs serial anchor; D={D} T={T} chunk={C}")
    print(f"{'head':5s}{'serial_f32':>14s}{'ut_f64':>14s}{'ut_f32':>14s}  (max|do| / max|dS|)")
    for idx in range(nh):
        g, beta, q, k, v = gen_head(idx)
        o64, S64 = serial_fast(g, beta, q, k, v, np.float64)
        cells = []
        for fn in [lambda: serial_fast(g, beta, q, k, v, np.float32),
                   lambda: chunk_ut(g, beta, q, k, v, np.float64),
                   lambda: chunk_ut_f32(g, beta, q, k, v)]:
            o, S = fn()
            cells.append(f"{maxabs(o, o64):6.2e}/{maxabs(S, S64):5.2e}")
        print(f"h{idx:<4d}" + "".join(f"{c:>15s}" for c in cells))
