#!/usr/bin/env python3
"""Numerical viability of the INT8 MoE design in PLAN-INT8-MOE.md, on CPU.

Models exactly what the kernel would compute:
  - weights are Q4_K: w = ds*q - dm with q an exact 4-bit integer in [0,15],
    one (ds, dm) pair per 32-value sub-block. The int8 path widens q to 8 bits
    with NO loss, so the weight side contributes zero extra error.
  - activations are quantized symmetric per 32-group: sa = absmax/127, qa = round(x/sa).
  - the tensor core gives MMA_g = sum(qa*q) exactly in int32, and the epilogue
    accumulates sa_g*(ds_g*MMA_g - dm_g*RowSum_g) in float.

The only error source is therefore activation quantization. This script measures
how big that error is for the dot products a routed-expert FFN actually computes,
and compares against the FP16 path ds4 runs today (with and without its COMP
residual), so the trade is explicit rather than assumed.
"""
import numpy as np

rng = np.random.default_rng(20260929)

T = 256            # tokens per tile
K = 2560           # DS4_N_EMBD for this checkpoint
G = 32             # Q4_K sub-block == the kernel's NK
NG = K // G


def make_weights(seed):
    """Q4_K-shaped weights: 4-bit codes with per-sub-block scale and min."""
    r = np.random.default_rng(seed)
    q = r.integers(0, 16, size=(K,)).astype(np.float64)          # exact 4-bit codes
    ds = np.repeat(r.uniform(0.002, 0.01, size=NG), G)           # per-sub-block scale
    dm = np.repeat(r.uniform(0.0, 0.05, size=NG), G)             # per-sub-block min
    return q, ds, dm, ds * q - dm


def ref_dot(x, w):
    return float(np.dot(x.astype(np.float64), w))


def int8_path(x, q, ds, dm):
    """Per-32-group symmetric int8 activations, exact 4-bit weights, int32 MMA."""
    xg = x.reshape(NG, G).astype(np.float64)
    absmax = np.max(np.abs(xg), axis=1)
    sa = np.where(absmax > 0, absmax / 127.0, 1.0)
    qa = np.rint(xg / sa[:, None]).astype(np.int64)
    qa = np.clip(qa, -127, 127)
    qg = q.reshape(NG, G).astype(np.int64)
    mma = np.sum(qa * qg, axis=1)                 # exact int32 in practice
    rowsum = np.sum(qa, axis=1)
    dsg = ds.reshape(NG, G)[:, 0]
    dmg = dm.reshape(NG, G)[:, 0]
    acc = np.sum(sa * (dsg * mma.astype(np.float64) - dmg * rowsum.astype(np.float64)))
    return float(acc), float(np.mean(sa))


def fp16_path(x, w, comp=False):
    xh = x.astype(np.float16).astype(np.float64)
    wh = w.astype(np.float16).astype(np.float64)
    if not comp:
        return float(np.dot(xh, wh))
    # ds4's COMP path stages x = xh + xr to ~2^-22 relative
    xr = (x - xh).astype(np.float16).astype(np.float64)
    return float(np.dot(xh, wh) + np.dot(xr, wh))


x = rng.normal(0.0, 1.0, size=(T, K))
q, ds, dm, w = make_weights(7)

rel_i8, rel_f16, rel_comp, rel_ref = [], [], [], []
scale_mag = []
for t in range(T):
    r = ref_dot(x[t], w)
    if abs(r) < 1e-9:
        continue
    i8, sa_mean = int8_path(x[t], q, ds, dm)
    f16 = fp16_path(x[t], w, comp=False)
    cp = fp16_path(x[t], w, comp=True)
    rel_i8.append(abs(i8 - r) / abs(r))
    rel_f16.append(abs(f16 - r) / abs(r))
    rel_comp.append(abs(cp - r) / abs(r))
    scale_mag.append(sa_mean)

a_i8 = np.array(rel_i8)
a_f16 = np.array(rel_f16)
a_cp = np.array(rel_comp)


def stats(name, a):
    print("%-26s mean %.3e  p50 %.3e  p95 %.3e  max %.3e" %
          (name, a.mean(), np.percentile(a, 50), np.percentile(a, 95), a.max()))


print("T=%d K=%d groups=%d (group size %d = Q4_K sub-block = kernel NK)" % (T, K, NG, G))
print("mean per-group activation scale: %.4f" % float(np.mean(scale_mag)))
print()
stats("int8 act x exact 4-bit w", a_i8)
stats("fp16 (ds4 today)", a_f16)
stats("fp16 + COMP residual", a_cp)
print()
print("ratio int8 / fp16      : %.1fx worse" % (a_i8.mean() / max(a_f16.mean(), 1e-30)))
print("ratio int8 / fp16+COMP : %.1fx worse" % (a_i8.mean() / max(a_cp.mean(), 1e-30)))

# Sensitivity: does a finer activation group help enough to matter?
print()
for gg in (16, 32, 64, 128, K):
    if K % gg:
        continue
    ng = K // gg
    errs = []
    for t in range(0, T, 4):
        r = ref_dot(x[t], w)
        if abs(r) < 1e-9:
            continue
        xg = x[t].reshape(ng, gg).astype(np.float64)
        am = np.max(np.abs(xg), axis=1)
        sa = np.where(am > 0, am / 127.0, 1.0)
        qa = np.clip(np.rint(xg / sa[:, None]), -127, 127).astype(np.int64)
        # keep the weight correction at its true 32-value granularity
        deq = (qa.astype(np.float64) * sa[:, None]).reshape(-1)
        errs.append(abs(float(np.dot(deq, w)) - r) / abs(r))
    e = np.array(errs)
    print("activation group %4d : mean rel err %.3e  p95 %.3e" % (gg, e.mean(), np.percentile(e, 95)))
