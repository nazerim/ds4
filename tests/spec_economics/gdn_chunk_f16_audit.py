#!/usr/bin/env python3
"""P1b.5 f16-staging drift audit for the tensorized chunked GDN scan.

The v1 Metal kernels are correct but ILP-starved (71.7 ms vs serial 9.7 ms).
The only hardware fast enough to beat serial is the f16 tensor path, but the
UT solve has heavy cancellation (O0 = KQ U0 with |O0| << sum|KQ||U0|), so
f16 operand staging may amplify well past the fp32 floor.  This script
measures the drift class of the projected v2 kernel in numpy before any Metal
is written.

Variants (all accumulate fp32, gc cumsum fp64 like the kernel's Kahan pair):
  f32bti    no staging, Minv@P solve (BTI form) — sanity baseline (~1e-7)
  f16in     stage raw inputs only: K, Q, V rounded to f16 at every GEMM
  f16ck1    + stage intermediates: Minv, P0/PK, KQ, U0, L (full CK1 f16)
  f16all    + stage the state/output passes: S,B in CK2's S@B, W,Sprev in
            CK3's output GEMM — the full projected v2 kernel class
Also reports cancellation factors for O0 = KQ U0 and A = (last U0)^T K.

usage: uv run --no-project --with numpy python gdn_chunk_f16_audit.py [heads]
"""
import sys

import numpy as np

from gdn_chunk_scan_probe import D, T, C, gen_head, serial, maxabs


def st16(x):
    return x.astype(np.float16).astype(np.float32)


def ut_f16(g, beta, q, k, v, stage_inputs, stage_inter, stage_state):
    S = np.zeros((D, D), dtype=np.float32)
    o = np.empty((T, D), dtype=np.float32)
    ks = st16(k) if stage_inputs else k.astype(np.float32)
    qs = st16(q) if stage_inputs else q.astype(np.float32)
    vs = st16(v) if stage_inputs else v.astype(np.float32)
    cf = {}
    for c0 in range(0, T, C):
        c1 = min(c0 + C, T)
        n = c1 - c0
        kc, qc, vc = ks[c0:c1], qs[c0:c1], vs[c0:c1]
        bt = beta[c0:c1].astype(np.float32)
        gc = np.cumsum(np.log(g[c0:c1].astype(np.float64)))
        idx = np.arange(n)
        lower = idx[:, None] >= idx[None, :]
        Rd = (gc[:, None] - gc[None, :]).astype(np.float32)
        R = np.where(lower, np.exp(np.where(lower, Rd, np.float32(0))), np.float32(0))
        KK = (kc @ kc.T).astype(np.float32)
        QK = (qc @ kc.T).astype(np.float32)
        Tm = (R * KK * bt[:, None]).astype(np.float32)
        np.fill_diagonal(Tm, 0.0)
        KQ = (R * QK).astype(np.float32)          # R is lower incl diagonal
        Minv = np.eye(n)
        for t in range(1, n):
            Minv[t, :t] = -(Tm[t, :t] @ Minv[:t, :t])
        Minv = Minv.astype(np.float32)
        if stage_inter:
            Minv = st16(Minv)
        eg = np.exp(gc).astype(np.float32)
        P0 = (vc * bt[:, None]).astype(np.float32)
        PK = (kc * (bt * eg)[:, None]).astype(np.float32)
        if stage_inter:
            P0 = st16(P0)
            PK = st16(PK)
        U0 = (Minv @ P0).astype(np.float32)
        L = (Minv @ PK).astype(np.float32)
        last = np.exp(gc[-1] - gc).astype(np.float32)
        A = ((U0 * last[:, None]).T @ kc).astype(np.float32)
        B = ((L * last[:, None]).T @ kc).astype(np.float32)
        U0s = st16(U0) if stage_inter else U0
        Ls = st16(L) if stage_inter else L
        KQs = st16(KQ) if stage_inter else KQ
        O0 = (KQs @ U0s).astype(np.float32)
        G = (KQs @ Ls).astype(np.float32)
        cf["O0"] = max(cf.get("O0", 0.0),
                       float((np.abs(KQs) @ np.abs(U0s)).max() / max(np.abs(O0).max(), 1e-30)))
        cf["A"] = max(cf.get("A", 0.0),
                      float((np.abs(U0s * last[:, None]).T @ np.abs(kc)).max() / max(np.abs(A).max(), 1e-30)))
        Sprev = S
        if stage_state:
            S = (eg[-1] * S + A - (st16(S) @ st16(B)).astype(np.float32)).astype(np.float32)
        else:
            S = (eg[-1] * S + A - (S @ B).astype(np.float32)).astype(np.float32)
        W = (qc * eg[:, None] - G).astype(np.float32)
        if stage_state:
            o[c0:c1] = (O0 + (st16(W) @ st16(Sprev).T).astype(np.float32)).astype(np.float32)
        else:
            o[c0:c1] = (O0 + (W @ Sprev.T).astype(np.float32)).astype(np.float32)
    return o, S, cf


if __name__ == "__main__":
    nh = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    variants = [
        ("f32bti", dict(stage_inputs=False, stage_inter=False, stage_state=False)),
        ("f16in", dict(stage_inputs=True, stage_inter=False, stage_state=False)),
        ("f16ck1", dict(stage_inputs=True, stage_inter=True, stage_state=False)),
        ("f16all", dict(stage_inputs=True, stage_inter=True, stage_state=True)),
    ]
    print(f"f16 staging audit, D={D} T={T} chunk={C}  (anchor = serial fp64, |o| ~ 0.061)")
    print(f"{'head':5s}" + "".join(f"{n:>22s}" for n, _ in variants) + "   (max|do| / max|dS|)")
    for idx in range(nh):
        g, beta, q, k, v = gen_head(idx)
        o64, S64 = serial(g, beta, q, k, v, np.float64)
        cells, cfs = [], {}
        for name, kw in variants:
            o, S, cf = ut_f16(g, beta, q, k, v, **kw)
            cells.append(f"{maxabs(o, o64):8.2e}/{maxabs(S, S64):8.2e}")
            for kk, vv in cf.items():
                cfs[kk] = max(cfs.get(kk, 0.0), vv)
        print(f"h{idx:<4d}" + "".join(f"{c:>22s}" for c in cells))
    print(f"\ncancellation factors (no-cancel magnitude / actual): O0 = KQ U0: {cfs.get('O0', 0):.1f}x, "
          f"A = (last U0)^T K: {cfs.get('A', 0):.1f}x")
