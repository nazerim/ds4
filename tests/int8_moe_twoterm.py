#!/usr/bin/env python3
"""Does a two-term int8 activation representation recover the accuracy?

The single-term result (1.4% mean relative error, 16.4x worse than ds4's fp16
path) is fundamental 8-bit quantization noise amplified by dot-product
cancellation, not an outlier problem - finer groups did not help. ds4's half path
already solves the same problem the same way: the COMP variant stages
x = xh + xr so the pair reaches ~2^-22 relative. This measures the int8 analogue,
which costs TWO int32 MMAs per K step instead of one, so the speed case has to be
recomputed against 2x the tensor work.
"""
import numpy as np

rng = np.random.default_rng(20260929)
T, K, G = 256, 2560, 32
NG = K // G


def make_weights(seed):
    r = np.random.default_rng(seed)
    q = r.integers(0, 16, size=(K,)).astype(np.float64)
    ds = np.repeat(r.uniform(0.002, 0.01, size=NG), G)
    dm = np.repeat(r.uniform(0.0, 0.05, size=NG), G)
    return q, ds, dm, ds * q - dm


def quant_term(x, q, ds, dm):
    """One symmetric int8 term: returns its corrected contribution and the residual."""
    xg = x.reshape(NG, G).astype(np.float64)
    absmax = np.max(np.abs(xg), axis=1)
    sa = np.where(absmax > 0, absmax / 127.0, 1.0)
    qa = np.clip(np.rint(xg / sa[:, None]), -127, 127).astype(np.int64)
    qg = q.reshape(NG, G).astype(np.int64)
    mma = np.sum(qa * qg, axis=1).astype(np.float64)
    rowsum = np.sum(qa, axis=1).astype(np.float64)
    dsg = ds.reshape(NG, G)[:, 0]
    dmg = dm.reshape(NG, G)[:, 0]
    contrib = float(np.sum(sa * (dsg * mma - dmg * rowsum)))
    residual = x - (qa.astype(np.float64) * sa[:, None]).reshape(-1)
    return contrib, residual


def int8_two_term(x, q, ds, dm):
    c1, r1 = quant_term(x, q, ds, dm)
    c2, _ = quant_term(r1, q, ds, dm)
    return c1 + c2


def int8_three_term(x, q, ds, dm):
    c1, r1 = quant_term(x, q, ds, dm)
    c2, r2 = quant_term(r1, q, ds, dm)
    c3, _ = quant_term(r2, q, ds, dm)
    return c1 + c2 + c3


def ref(x, w):
    return float(np.dot(x.astype(np.float64), w))


def fp16(x, w, comp=False):
    xh = x.astype(np.float16).astype(np.float64)
    wh = w.astype(np.float16).astype(np.float64)
    if not comp:
        return float(np.dot(xh, wh))
    xr = (x - xh).astype(np.float16).astype(np.float64)
    return float(np.dot(xh, wh) + np.dot(xr, wh))


x = rng.normal(0.0, 1.0, size=(T, K))
q, ds, dm, w = make_weights(7)

e1, e2, e3, ef, ec = [], [], [], [], []
for t in range(T):
    r = ref(x[t], w)
    if abs(r) < 1e-9:
        continue
    c1, res1 = quant_term(x[t], q, ds, dm)
    e1.append(abs(c1 - r) / abs(r))
    e2.append(abs(c1 + quant_term(res1, q, ds, dm)[0] - r) / abs(r))
    e3.append(abs(int8_three_term(x[t], q, ds, dm) - r) / abs(r))
    ef.append(abs(fp16(x[t], w) - r) / abs(r))
    ec.append(abs(fp16(x[t], w, comp=True) - r) / abs(r))


def stats(name, a, mmas):
    a = np.array(a)
    print("%-30s mean %.3e  p95 %.3e  max %.3e   MMAs/Kstep=%d" %
          (name, a.mean(), np.percentile(a, 95), a.max(), mmas))
    return a.mean()


print("T=%d K=%d group=%d\n" % (T, K, G))
m1 = stats("int8 single term", e1, 1)
m2 = stats("int8 two terms", e2, 2)
m3 = stats("int8 three terms", e3, 3)
mf = stats("fp16 (ds4 today)", ef, 1)
mc = stats("fp16 + COMP (ds4 today)", ec, 2)

print()
print("two-term int8 vs fp16        : %.2fx %s" %
      (m2 / mf, "worse" if m2 > mf else "BETTER"))
print("two-term int8 vs fp16+COMP   : %.2fx %s" %
      (m2 / mc, "worse" if m2 > mc else "BETTER"))

# Speed model on the measured attribution: moe 2041.5 ms of 5563.0 total at 65k.
MOE, TOT, RATIO = 2041.5, 5563.0, 3.0   # RATIO = 42 TOP/s int8 vs ~14 TF/s fp16
print()
for name, mmas in (("single term", 1), ("two terms", 2), ("three terms", 3)):
    new_moe = MOE * mmas / RATIO
    new_tot = TOT - MOE + new_moe
    print("%-12s moe %7.1f -> %7.1f ms   total %7.1f -> %7.1f ms   prefill %+6.1f%%"
          % (name, MOE, new_moe, TOT, new_tot, 100.0 * (TOT / new_tot - 1.0)))
