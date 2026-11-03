"""mirror_trunk primitives: algebra invariants (kernel parity comes later
via gate A/B against parity dumps; these guard the numpy port itself)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dspark.mirror_trunk import (assemble_stage_input, fp8_kv_quantize,
                                 hc_collapse, hc_expand, hc_split_sinkhorn,
                                 router, rms_norm,
                                 BLOCK, N_EMBD, N_HC, N_VOCAB,
                                 ROUTER_DENOM_FLOOR, EXPERT_WEIGHT_SCALE,
                                 FP8KV_AMAX_FLOOR, N_EXPERT, N_EXPERT_USED)

EPS = 1e-6


def test_sinkhorn_doubly_stochastic():
    rng = np.random.default_rng(0)
    mix = rng.normal(size=(3, 24)).astype(np.float32)
    scale = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    base = np.zeros(24, dtype=np.float32)
    pre, post, comb = hc_split_sinkhorn(mix, scale, base, EPS)
    assert pre.shape == (3, 4) and post.shape == (3, 4)
    assert comb.shape == (3, 4, 4)
    assert (pre > 0).all() and (comb > 0).all()
    # rows and cols of comb both ~1 up to eps drift (20 alternating iters)
    rs = comb.sum(axis=-1)
    cs = comb.sum(axis=-2)
    assert np.allclose(rs, 1.0, atol=0.05)
    assert np.allclose(cs, 1.0, atol=0.05)


def test_collapse_expand_identities():
    rng = np.random.default_rng(1)
    s = rng.normal(size=(2, 4, N_EMBD)).astype(np.float32)
    pre = np.full((2, 4), 0.25, dtype=np.float32)
    out = hc_collapse(s, pre)
    assert np.allclose(out, s.mean(axis=1), rtol=1e-5)
    v = rng.normal(size=(2, N_EMBD)).astype(np.float32)
    eye = np.broadcast_to(np.eye(4, dtype=np.float32), (2, 4, 4)).copy()
    one = np.ones((2, 4), dtype=np.float32)
    exp = hc_expand(v, s, one, eye)
    assert np.allclose(exp, s + v[:, None, :], rtol=1e-5)


def test_fp8kv_quantize_semantics():
    rng = np.random.default_rng(2)
    kv = rng.normal(size=(1, 512)).astype(np.float32) * 0.3
    q = fp8_kv_quantize(kv)
    assert np.array_equal(q[..., 448:], kv[..., 448:])   # rope pass-through
    assert np.all(np.abs(q) <= np.abs(kv).max() * 2)
    q2 = fp8_kv_quantize(q)                              # idempotent
    assert np.allclose(q2, q, rtol=0, atol=1e-6)
    err = np.abs(q[..., :448] - kv[..., :448]).max()
    assert 0 < err < 0.05                                # e4m3 grid step
    z = np.zeros((1, 512), dtype=np.float32)
    assert np.allclose(fp8_kv_quantize(z), 0.0)          # amax floor keeps 0


def test_router_scale_and_bias_only_selection():
    rng = np.random.default_rng(3)
    logits = rng.normal(size=(4, N_EXPERT)).astype(np.float32) * 2
    sel, w = router(logits, None)
    assert sel.shape == (4, N_EXPERT_USED)
    assert w.shape == (4, N_EXPERT_USED)
    assert np.allclose(w.sum(axis=-1), EXPERT_WEIGHT_SCALE, rtol=1e-3)
    bias = rng.normal(size=N_EXPERT).astype(np.float32) * 5
    sel_b, w_b = router(logits, bias)
    # bias moves selection, weights still renorm from PROBS (not biased)
    assert np.allclose(w_b.sum(axis=-1), EXPERT_WEIGHT_SCALE, rtol=1e-3)
    p = np.sqrt(np.maximum(logits, 0) + np.log1p(np.exp(-np.abs(logits))))
    for i in range(4):
        expect = p[i, sel_b[i]] / max(p[i, sel_b[i]].sum(), ROUTER_DENOM_FLOOR)
        assert np.allclose(w_b[i], expect * EXPERT_WEIGHT_SCALE, rtol=1e-3)


def test_assemble_stage_input_layout():
    rng = np.random.default_rng(4)
    taps = rng.normal(size=3 * N_EMBD).astype(np.float32)
    ids = np.array([10, 128799, 128799, 128799, 128799], dtype=np.int32)
    embd = rng.normal(size=(N_VOCAB, N_EMBD)).astype(np.float16)
    main_w = rng.normal(size=(N_EMBD, 3 * N_EMBD)).astype(np.float32) * 0.02
    main_norm = np.ones(N_EMBD, dtype=np.float32)
    out = assemble_stage_input(taps, ids, embd, main_w, main_norm, 1e-5)
    assert out.shape == (BLOCK + 1, N_HC * N_EMBD)
    s = out.reshape(BLOCK + 1, N_HC, N_EMBD)
    row0 = s[0]
    assert np.allclose(row0, row0[0][None, :])          # replicated streams
    for i in range(1, BLOCK + 1):                       # emb rows replicated
        assert np.allclose(s[i], embd[ids[i - 1]][None, :].astype(np.float32),
                           rtol=1e-3)


def test_rms_norm_scale():
    x = np.full((1, 8), 3.0, dtype=np.float32)
    y = rms_norm(x, np.ones(8, dtype=np.float32), 0.0)
    assert np.allclose(y, 1.0)                          # unit-rms direction
