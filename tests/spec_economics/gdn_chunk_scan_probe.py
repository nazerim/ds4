#!/usr/bin/env python3
"""Phase 0 probe for a chunked GDN scan (GATED-SCAN project): quantify the
re-association drift of replacing the serial per-token rank-1 recurrence
with chunk summaries (P_c = prod M_t, b_c = accumulated feed, chunk
propagation S_c = S_{c-1} P_c + b_c, intra-chunk rank-1 tail).

Semantics from gdn_reference (tests/test_qwen4_kernels.c): per token,
  S *= g; u = S@k; delta = (v-u)*beta; S += outer(delta,k); o = S@q
as a linear recurrence over the key dimension:
  S_t = S_{t-1} M_t + p_t,   M_t = g_t (I - beta_t k k^T),  p_t = beta_t v k^T
Realistic per-head params: g = exp(a*softplus(alpha+dt)), a in (-8,-0.1),
dt in (0.2,1.5), unit-norm k, q ~ k/sqrt(D), v ~ N(0,1), beta = sigmoid.

Variants vs the fp64 serial anchor:
  serial_f32   ~ current GPU kernel (fp32 noise floor)
  chunked_f64  pure re-association, no added rounding
  chunked_f32  fp32 chunk matmuls (projected kernel class)
  chunked_f16  f16-staged operands + fp32 accumulate on the chunk
               propagation (tensor-unit staging class, moe_mm-style)
Reports max|do| and max|dS_final| per head (D=128, T=2048, chunk 64).

usage: uv run --no-project python gdn_chunk_scan_probe.py [heads]
"""
import sys

import numpy as np

D, T, C = 128, 2048, 64


def softplus(x):
    return np.logaddexp(0.0, x)


def gen_head(idx):
    r = np.random.default_rng(7000 + idx)
    a = r.uniform(-8.0, -0.1)
    dt = r.uniform(0.2, 1.5)
    g = np.exp(a * softplus(r.normal(0.0, 1.0, T) + dt))
    beta = 1.0 / (1.0 + np.exp(-r.normal(0.0, 1.0, T)))
    k = r.normal(0.0, 1.0, (T, D))
    k /= np.linalg.norm(k, axis=1, keepdims=True)
    v = r.normal(0.0, 1.0, (T, D))
    q = r.normal(0.0, 1.0, (T, D))
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    q /= np.sqrt(D)
    return g, beta, q, k, v


def serial(g, beta, q, k, v, dtype):
    S = np.zeros((D, D), dtype=dtype)
    o = np.empty((T, D), dtype=dtype)
    for t in range(T):
        S *= dtype(g[t])
        u = S @ k[t].astype(dtype)
        delta = (v[t].astype(dtype) - u) * dtype(beta[t])
        S += np.outer(delta, k[t].astype(dtype))
        o[t] = S @ q[t].astype(dtype)
    return o, S


def chunked(g, beta, q, k, v, dtype, f16_stage=False):
    S = np.zeros((D, D), dtype=dtype)
    o = np.empty((T, D), dtype=dtype)
    for c0 in range(0, T, C):
        c1 = min(c0 + C, T)
        # chunked form: running prefixes P_t = M_c0..M_t, b_t = accumulated
        # feed, and the state is S_t = S_prev @ P_t + b_t (one d*d matmul per
        # token against the entry state -- the kernel class under probe).
        P = np.eye(D, dtype=dtype)
        b = np.zeros((D, D), dtype=dtype)
        Sprev = S
        for t in range(c0, c1):
            gt, bt, kt = dtype(g[t]), dtype(beta[t]), k[t].astype(dtype)
            Pk = P @ kt
            P = gt * (P - bt * np.outer(Pk, kt))
            bu = b @ kt
            b = b * gt + np.outer((v[t].astype(dtype) - gt * bu) * bt, kt)
            if f16_stage:
                prop = np.asarray(np.asarray(Sprev, np.float16).astype(np.float32)
                                  @ np.asarray(P, np.float16).astype(np.float32), dtype=dtype)
                St = prop + b
            else:
                St = Sprev @ P + b
            o[t] = St @ q[t].astype(dtype)
        S = Sprev @ P + b
    return o, S


def maxabs(x, y):
    return float(np.max(np.abs(x.astype(np.float64) - y.astype(np.float64))))


nh = int(sys.argv[1]) if len(sys.argv) > 1 else 4
print(f"D={D} T={T} chunk={C} heads={nh}   (anchor = serial fp64)")
names = ["serial_f32", "chunked_f64", "chunked_f32", "chunked_f16"]
print(f"{'head':5s}" + "".join(f"{n:>14s}" for n in names) + "   (max|do| / max|dS|)")
for idx in range(nh):
    g, beta, q, k, v = gen_head(idx)
    o64, S64 = serial(g, beta, q, k, v, np.float64)
    cells = []
    for fn in [lambda: serial(g, beta, q, k, v, np.float32),
               lambda: chunked(g, beta, q, k, v, np.float64),
               lambda: chunked(g, beta, q, k, v, np.float32),
               lambda: chunked(g, beta, q, k, v, np.float32, True)]:
        o, S = fn()
        cells.append(f"{maxabs(o, o64):6.2e}/{maxabs(S, S64):5.2e}")
    print(f"h{idx:<4d}" + "".join(f"{c:>14s}" for c in cells))
print("\nscale check: |o| ~", maxabs(o64, np.zeros_like(o64)))
