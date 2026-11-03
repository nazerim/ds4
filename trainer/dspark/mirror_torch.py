"""S2 torch port of mirror_trunk.Trunk.cycle (3C-NIGHT-PLAN S2).

1:1 port rules: every op mirrors its numpy counterpart in the SAME order
with the SAME literals; shape constants are IMPORTED from mirror_trunk
(single source of truth). float32 everywhere EXCEPT where numpy itself
goes float64 (router logits, rope theta) - mirrored exactly. Dense weight
tensors are torch.from_numpy VIEWS of the numpy Trunk (zero-copy); routed
expert dequant reuses the numpy trunk's cache (engine-faithful gguf_dq).
Differentiable op set keeps input dtype (no hardcoded f32 casts) so
gradcheck can run in float64; where numpy deliberately computes f64 and
casts down, we compute f64 and cast to x.dtype.

Verify legs (plan S2):
  (a) conf rows cos ~1.000 vs numpy trunk on identical cycles
      (--selfcheck: empty-ring cycles + warmed-ring cycles)
  (b) Gate A through the torch trunk reproduces the frozen numpy baseline
      (slot0 cos_mean .9898, MIRROR_EMPTY_RING=1, exact-era pid53508)
      - --gate-a runs mirror_trunk.run_gate_a on the torch trunk
  (c) torch.autograd.gradcheck over the differentiable ops (--selfcheck)

Usage:
  uv run python -m dspark.mirror_torch --selfcheck
  MIRROR_EMPTY_RING=1 uv run python -m dspark.mirror_torch --gate-a \
      --recs REC --parity PAR --compare 24 --stride 90 --report OUT.json
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import torch

from . import mirror_trunk as mt
from .mirror_trunk import (BLOCK, N_EMBD, N_HC, N_HEAD, N_HEAD_DIM, N_ROT,
                           N_LORA_Q, N_LORA_O, N_OUT_GROUP, N_EXPERT_USED,
                           NOISE_TOKEN, EXPERT_WEIGHT_SCALE,
                           ROUTER_DENOM_FLOOR, FP8KV_AMAX_FLOOR, FP8KV_MAX,
                           SINKHORN_ITERS, WINDOW_ROWS, ATTN_SCALE,
                           ROPE_FREQ_BASE)


# ---------------------------- ported ops ----------------------------
# Each function below is the literal torch twin of the numpy function of
# the same name in mirror_trunk.py (line refs in comments).

def rms_norm(x, w, eps):
    # mirror rms_norm: x / sqrt(mean(x*x)+eps) * w
    ms = torch.mean(x * x, dim=-1, keepdim=True)
    return x / torch.sqrt(ms + eps) * w


def _sigmoid(z, stable):
    # mirror _sigmoid: stable = 0.5*tanh(0.5z)+0.5 else 1/(1+exp(-z))
    if stable:
        return 0.5 * torch.tanh(0.5 * z) + 0.5
    return 1.0 / (1.0 + torch.exp(-z))


def hc_split_sinkhorn(mix, scale, base, eps):
    # mirror hc_split_sinkhorn (pre/post/comb + sinkhorn loop)
    pre = _sigmoid(mix[..., 0:4] * scale[0] + base[0:4],
                   mt.HC_SIGMOID_STABLE) + eps
    post = 2.0 * _sigmoid(mix[..., 4:8] * scale[1] + base[4:8],
                          mt.HC_SIGMOID_STABLE)
    r = mix[..., 8:24].clone()
    r = r * scale[2] + base[8:24]
    r = r.reshape(*r.shape[:-1], 4, 4)
    m = r.max(dim=-1, keepdim=True).values
    r = torch.exp(r - m)
    r = r / r.sum(dim=-1, keepdim=True) + eps
    col = 1.0 / (r.sum(dim=-2, keepdim=True) + eps)
    r = r * col
    for _ in range(SINKHORN_ITERS - 1):
        r = r / (r.sum(dim=-1, keepdim=True) + eps)
        col = 1.0 / (r.sum(dim=-2, keepdim=True) + eps)
        r = r * col
    return pre, post, r


def hc_collapse(streams, pre):
    # mirror einsum("...i,...id->...d")
    return torch.einsum("...i,...id->...d", pre, streams)


def hc_expand(block_v, residual, post, comb):
    # mirror: v[...,None,:]*post[...,:,None] + einsum("...ji,...id->...jd")
    return (block_v[..., None, :] * post[..., :, None]
            + torch.einsum("...ji,...id->...jd", comb, residual))


_E4M3_T = None
_E4M3_POS = None


def _e4m3_tensors(dtype):
    global _E4M3_T, _E4M3_POS
    if _E4M3_T is None:
        _E4M3_T = torch.from_numpy(mt._e4m3_lut())          # (256,) f32
        lut = _E4M3_T.numpy()
        finite = lut[lut != 0]
        _E4M3_POS = torch.from_numpy(finite[finite > 0])    # (126,) f32
    return _E4M3_T.to(dtype), _E4M3_POS.to(dtype)


def fp8_kv_quantize(kv):
    """mirror fp8_kv_quantize: fake-quant E4M3 on first 448 dims in 64-
    blocks (amax floor 1e-4, scale=2^ceil(log2(amax/448)), clamp +-448,
    nearest-grid * scale); last 64 rope dims pass through.
    NON-DIFFERENTIABLE by design on the quantized half: the nope half is
    DETACHED (engine quantizer has no meaningful gradient; S2 review ruled
    detach explicit rather than accidental-zero via sign). The rope half
    (last 64 dims) passes through the engine quantizer untouched, so it
    deliberately KEEPS gradient - that asymmetry is engine-faithful.
    Functional assembly (torch.cat) instead of in-place slice writes:
    the port's earlier `kv[..., :448] = ...` clobbered an AsStrided view
    autograd had saved and killed backward (S2 review MAJOR 2)."""
    _, pos = _e4m3_tensors(kv.dtype)
    nope = kv[..., :448].reshape(*kv.shape[:-1], 7, 64)
    amax = torch.clamp(nope.abs().max(dim=-1, keepdim=True).values,
                       min=FP8KV_AMAX_FLOOR)
    scale = torch.exp2(torch.ceil(torch.log2(amax / FP8KV_MAX)))
    v = torch.clamp(nope / scale, -FP8KV_MAX, FP8KV_MAX)
    av, asig = v.abs(), torch.sign(v)
    idx = (av[..., None] - pos).abs().argmin(dim=-1)
    q = pos[idx] * asig
    quant = (q * scale).reshape(*kv.shape[:-1], 448).detach()
    return torch.cat([quant, kv[..., 448:]], dim=-1)


def router(logits, bias):
    # mirror router: f64 internal; probs=sqrt(softplus); +bias selection
    # only; top-6 stable; weights renorm * 1.5 (returned f64 like numpy).
    x = logits.double()
    probs = torch.sqrt(torch.log1p(torch.exp(-x.abs()))
                       + torch.clamp(x, min=0))
    scores = probs if bias is None else probs + bias.double()
    sel = torch.argsort(scores, dim=-1, descending=True, stable=True)[
        ..., :N_EXPERT_USED]
    p_sel = torch.gather(probs, -1, sel)
    denom = torch.clamp(p_sel.sum(dim=-1, keepdim=True),
                        min=ROUTER_DENOM_FLOOR)
    return sel, p_sel / denom * EXPERT_WEIGHT_SCALE


def rope_tail(x, pos0, n_rot=N_ROT, inverse=False, freq_base=ROPE_FREQ_BASE):
    """mirror rope_tail: theta f64 = (arange(rows)+pos0)*base^(-2i/n_rot);
    cos/sin cast to x.dtype (numpy casts to f32; port keeps dtype so
    gradcheck can run f64)."""
    squeeze = x.ndim == 2
    x3 = x[:, None, :] if squeeze else x
    rows, heads, hd = x3.shape
    out = x3.clone()
    n_nope = hd - n_rot
    i = torch.arange(n_rot // 2, dtype=torch.float64)
    idx0 = list(range(n_nope, hd, 2))
    idx1 = list(range(n_nope + 1, hd, 2))
    theta = ((torch.arange(rows, dtype=torch.float64) + pos0)[:, None]
             * torch.pow(torch.tensor(float(freq_base),
                                      dtype=torch.float64),
                         (-2.0 * i / n_rot)[None, :]))
    c = torch.cos(theta).to(x.dtype)
    s = torch.sin(theta).to(x.dtype)
    if inverse:
        s = -s
    x0 = x3[:, :, idx0]
    x1 = x3[:, :, idx1]
    c3 = c[:, None, :]
    s3 = s[:, None, :]
    out[:, :, idx0] = x0 * c3 - x1 * s3
    out[:, :, idx1] = x0 * s3 + x1 * c3
    return out[:, 0, :] if squeeze else out


def attn_noncausal(q, kv, sinks):
    """mirror attn_noncausal: scores=einsum(q,kv)*scale, sinks add mass to
    the softmax denominator only; out = einsum(p/den, kv)."""
    scores = torch.einsum("rhd,vd->rhv", q, kv) \
        * torch.tensor(float(ATTN_SCALE), dtype=q.dtype)
    sink_logit = sinks[None, :, None]
    m = torch.maximum(scores.max(dim=-1, keepdim=True).values, sink_logit)
    p = torch.exp(scores - m)
    ps = torch.exp(sink_logit - m)
    den = p.sum(dim=-1, keepdim=True) + ps
    return torch.einsum("rhv,vd->rhd", p / den, kv)


def swiglu(gate, up, limit=10.0):
    # mirror swiglu: g=min(gate,limit); u=clamp(up,-limit,limit); silu*u
    g = torch.minimum(gate, torch.tensor(limit, dtype=gate.dtype))
    u = torch.clamp(up, -limit, limit)
    return (g / (1.0 + torch.exp(-g))) * u


# ---------------------------- torch trunk ----------------------------

class TorchTrunk:
    """Zero-copy twin of mirror_trunk.Trunk: dense weights are torch views
    of the numpy arrays; routed experts are pulled (and cached) through the
    numpy trunk's gguf_dq path."""

    def __init__(self, np_trunk: mt.Trunk):
        self.np = np_trunk
        self.embd = torch.from_numpy(np_trunk.embd)        # f16 view
        self.stages = []
        for st in np_trunk.stages:
            self.stages.append({k: torch.from_numpy(v) for k, v in st.items()})
        cvt = torch.from_numpy
        self.main_proj = cvt(np_trunk.main_proj)
        self.main_norm = cvt(np_trunk.main_norm)
        self.head_fn = cvt(np_trunk.head_fn)
        self.head_base = cvt(np_trunk.head_base)
        self.head_scale = cvt(np_trunk.head_scale)
        self.final_norm = cvt(np_trunk.final_norm)
        self._xpcache: dict = {}
        self.xp_cache_cap = max(16, int(np_trunk._ecache_cap) * 3)

    def expert(self, stage, which, eid):
        key = (stage, which, eid)
        v = self._xpcache.get(key)
        if v is None:
            # share the numpy trunk's cached buffer (zero-copy; eviction
            # from the numpy cache cannot free it while we hold a ref).
            # BOUND (S2 review MINOR 1): insert-ordered FIFO; entries are
            # views, so eviction only drops references. POLICY: expert
            # slices are share-don't-train - S4 must NOT set requires_grad
            # on them (aliasing would corrupt the numpy mirror's buffers).
            if len(self._xpcache) >= self.xp_cache_cap:
                self._xpcache.pop(next(iter(self._xpcache)))
            v = torch.from_numpy(self.np.expert(stage, which, eid))
            self._xpcache[key] = v
        return v

    def _mixer(self, x_rows, fn, base, scale):
        flat = rms_norm(x_rows.reshape(x_rows.shape[0], -1),
                        torch.ones(N_HC * N_EMBD, dtype=x_rows.dtype),
                        mt.RMS_EPS)
        mix = flat @ fn.T
        return hc_split_sinkhorn(mix, scale, base, mt.HC_EPS)

    def _ffn(self, st, x):
        logits = x @ st["gate_inp"].T + st["exp_b"][None, :]
        # router selection+weights are DISCRETE/scalar coefficients: the
        # engine treats them as constants, so we DETACH before the .numpy()
        # hop (S2 review MAJOR 1: grad-tracked router crashed on w.numpy()).
        # Gradient still reaches `logits` consumers? NO - by design: expert
        # routing is not trained through the trunk.
        sel, w = router(logits.detach(), None)
        sel_np = sel.numpy()
        w_np = w.numpy()
        rows = []
        for r in range(x.shape[0]):
            acc = torch.zeros(N_EMBD, dtype=x.dtype)
            for e_i in range(N_EXPERT_USED):
                eid = int(sel_np[r, e_i])
                gm = self.expert(self._cur_stage, "gate", eid)
                um = self.expert(self._cur_stage, "up", eid)
                dm = self.expert(self._cur_stage, "down", eid)
                gate_v = x[r] @ gm.T
                up_v = x[r] @ um.T
                # numpy: mid * np.float32(w[r, e_i]) - f64 -> f32 cast first
                mid = swiglu(gate_v, up_v) \
                    * torch.tensor(np.float32(w_np[r, e_i]), dtype=x.dtype)
                acc = acc + mid @ dm.T
            rows.append(acc)
        out = torch.stack(rows, dim=0)   # functional (was out[r] = acc)
        shared = swiglu(x @ st["sh_gate"].T,
                        x @ st["sh_up"].T) @ st["sh_down"].T
        return out + shared

    def cycle_gradable(self, pos, seed, taps_f32, ring):
        """cycle() with grad enabled (S4 will train through this).
        POST-S2-REVIEW CONTRACT: gradients reach every dense weight and
        the input taps, EXCEPT (engine-faithful constants): (a) expert
        routing selections/weights (detached at the .numpy() hop),
        (b) the quantized nope half of the KV cache (engine quantizer,
        detached). Trainables must be explicit leaf views with
        requires_grad_(True); do NOT train expert slices (aliased into
        the numpy mirror's buffers - share-don't-train)."""
        with torch.enable_grad():
            return self._cycle(pos, seed, taps_f32, ring)

    def cycle(self, pos, seed, taps_f32, ring):
        with torch.no_grad():
            return self._cycle(pos, seed, taps_f32, ring)

    def _cycle(self, pos, seed, taps_f32, ring):
        feature_pos = pos - 1
        taps = torch.from_numpy(np.ascontiguousarray(taps_f32, np.float32))
        main_x = rms_norm(taps @ self.main_proj.T, self.main_norm, mt.RMS_EPS)
        ids = [seed] + [NOISE_TOKEN] * (BLOCK - 1)
        # functional assembly (no in-place row writes - S2 review MINOR 3).
        # Semantics = numpy broadcast: each 4096 row REPLICATED across the
        # N_HC streams (stage_in[i] = row[None,:] broadcast to (N_HC,4096));
        # repeat materializes the same (6,N_HC,4096) as the old code.
        stage_rows = torch.stack(
            [main_x] + [self.embd[tok].to(torch.float32) for tok in ids],
            dim=0)                                        # (6,4096)
        stage_in = stage_rows[:, None, :].repeat(1, N_HC, 1)
        streams = None
        for s in range(3):
            st = self.stages[s]
            self._cur_stage = s
            rows = stage_in.reshape(BLOCK + 1, N_HC * N_EMBD)
            pre, post, comb = self._mixer(rows, st["hc_attn_fn"],
                                          st["hc_attn_base"],
                                          st["hc_attn_scale"])
            cur = hc_collapse(stage_in, pre)
            # TARGET row uses the shared main ladder (engine: dspark_main_x
            # computed once at stage0); rms_norm is row-wise so composing
            # [main_x | norm(cur[1:])] is forward-identical to norm-all-
            # then-overwrite, without the in-place write. cur/an are
            # 2-D (6,4096) here - collapse already weighted the streams.
            an = torch.cat([main_x[None, :],
                            rms_norm(cur[1:], st["attn_norm"], mt.RMS_EPS)],
                           dim=0)
            drafts = an[1:]
            qr = rms_norm(drafts @ st["q_a"].T, st["q_a_norm"], mt.RMS_EPS)
            q = (qr @ st["q_b"].T).reshape(BLOCK, N_HEAD, N_HEAD_DIM)
            q = rms_norm(q, torch.ones(N_HEAD_DIM, dtype=torch.float32),
                         mt.RMS_EPS)
            q = rope_tail(q, pos)
            kv = rms_norm(an @ st["attn_kv"].T, st["kv_a_norm"], mt.RMS_EPS)
            kv = torch.cat([rope_tail(kv[0:1], feature_pos),
                            rope_tail(kv[1:], pos)], dim=0)
            kv = fp8_kv_quantize(kv)
            ring_rows = ring[s]
            if ring_rows and not mt.MIRROR_EMPTY_RING:
                visible = torch.cat([torch.stack(ring_rows), kv])
            else:
                visible = kv
            heads = attn_noncausal(q, visible, st["sinks"])
            heads = rope_tail(heads, pos, inverse=True)
            xg = heads.reshape(BLOCK, N_OUT_GROUP, N_HEAD // N_OUT_GROUP,
                               N_HEAD_DIM)
            attn_out = torch.zeros((BLOCK, N_EMBD), dtype=torch.float32)
            for gidx in range(N_OUT_GROUP):
                x_g = xg[:, gidx].reshape(BLOCK, -1)
                a_g = st["out_a"][gidx * N_LORA_O:(gidx + 1) * N_LORA_O]
                b_g = st["out_b"][:, gidx * N_LORA_Q:(gidx + 1) * N_LORA_Q]
                attn_out = attn_out + (x_g @ a_g.T) @ b_g.T
            streams = hc_expand(attn_out, stage_in[1:], post[1:], comb[1:])
            fpre, fpost, fcomb = self._mixer(streams, st["hc_ffn_fn"],
                                             st["hc_ffn_base"],
                                             st["hc_ffn_scale"])
            fcur = hc_collapse(streams, fpre)
            fcur = rms_norm(fcur, st["ffn_norm"], mt.RMS_EPS)
            block2 = self._ffn(st, fcur)
            streams = hc_expand(block2, streams, fpost, fcomb)
            # ring = quantized KV cache: future-cycle history is CONSTANT
            # (engine semantics; also stops autograd graphs accumulating
            # across cycles). Numerically identical to clone().
            ring[s].append(kv[0].detach().clone())
            if len(ring[s]) > WINDOW_ROWS:
                del ring[s][:len(ring[s]) - WINDOW_ROWS]
            stage_in = torch.cat([stage_in[0:1], streams], dim=0)
        rows = streams.reshape(BLOCK, N_HC * N_EMBD)
        flat = rms_norm(rows, torch.ones(N_HC * N_EMBD, dtype=torch.float32),
                        mt.RMS_EPS)
        pre_h = flat @ self.head_fn.T
        weights = _sigmoid(pre_h * self.head_scale[0] + self.head_base,
                           mt.HC_SIGMOID_STABLE) + mt.HC_EPS
        return torch.einsum("rj,rjd->rd", weights, streams)

    @torch.no_grad()
    def advance_ring(self, pos, taps_f32, ring):
        main_x = rms_norm(
            torch.from_numpy(np.ascontiguousarray(taps_f32, np.float32))
            @ self.main_proj.T, self.main_norm, mt.RMS_EPS)
        fp = pos - 1
        for s, st in enumerate(self.stages):
            kv0 = rms_norm(main_x @ st["attn_kv"].T, st["kv_a_norm"],
                           mt.RMS_EPS)
            kv0 = rope_tail(kv0[None, :], fp)[0]
            kv0 = fp8_kv_quantize(kv0[None, :])[0]
            ring[s].append(kv0)
            if len(ring[s]) > WINDOW_ROWS:
                del ring[s][:len(ring[s]) - WINDOW_ROWS]


class NumpyGateAdapter:
    """Plugs TorchTrunk into mirror_trunk.run_gate_a/_b, which keep a
    python-list ring and expect numpy conf rows. run_gate_a REBINDS the
    ring list on turn boundaries - detected by identity."""

    def __init__(self, tt: TorchTrunk):
        self.tt = tt
        self._bound = None
        self._ring = None

    def _ring_for(self, ring):
        if ring is not self._bound:
            self._bound = ring
            self._ring = [[], [], []]
        return self._ring

    def cycle(self, pos, seed, taps, ring):
        return self.tt.cycle(pos, seed, taps, self._ring_for(ring)).numpy()

    def advance_ring(self, pos, taps, ring):
        self.tt.advance_ring(pos, taps, self._ring_for(ring))


# ---------------------------- verify legs ----------------------------

def leg_gradcheck():
    """(c) autograd validity on tiny inputs, float64. Uses an enable_grad
    CONTEXT (S2 review MINOR 2: the old set_grad_enabled(False) at exit was
    a silent no-grad trap for later code in the same process)."""
    from torch.autograd import gradcheck
    f64 = torch.float64
    res = {}
    with torch.enable_grad():
        x = torch.randn(3, 8, dtype=f64, requires_grad=True)
        w = torch.randn(8, dtype=f64, requires_grad=True)
        res["rms_norm"] = gradcheck(rms_norm, (x, w, 1e-6))
        mix = torch.randn(2, 24, dtype=f64, requires_grad=True)
        sc = torch.rand(3, dtype=f64, requires_grad=True) + 0.5
        bs = torch.randn(24, dtype=f64, requires_grad=True)
        res["hc_split_sinkhorn"] = gradcheck(
            lambda m, s, b: hc_split_sinkhorn(m, s, b, 1e-6), (mix, sc, bs))
        st_ = torch.randn(2, 4, 8, dtype=f64, requires_grad=True)
        pre = torch.rand(2, 4, dtype=f64, requires_grad=True)
        res["hc_collapse"] = gradcheck(hc_collapse, (st_, pre))
        v = torch.randn(2, 8, dtype=f64, requires_grad=True)
        r_ = torch.randn(2, 4, 8, dtype=f64, requires_grad=True)
        post = torch.rand(2, 4, dtype=f64, requires_grad=True)
        comb = torch.rand(2, 4, 4, dtype=f64, requires_grad=True)
        res["hc_expand"] = gradcheck(hc_expand, (v, r_, post, comb))
        q = torch.randn(3, N_HEAD, 64, dtype=f64, requires_grad=True)
        kv = torch.randn(7, 64, dtype=f64, requires_grad=True)
        sinks = torch.randn(N_HEAD, dtype=f64, requires_grad=True)
        # small head_dim ok: attn op is dtype/shape generic
        res["attn_noncausal"] = gradcheck(
            attn_noncausal, (q, kv, sinks))
        g = torch.randn(4, 16, dtype=f64, requires_grad=True)
        u = torch.randn(4, 16, dtype=f64, requires_grad=True)
        res["swiglu"] = gradcheck(swiglu, (g, u))
        xq = torch.randn(2, N_HEAD, N_HEAD_DIM, dtype=f64, requires_grad=True)
        res["rope_tail"] = gradcheck(rope_tail, (xq, 17))
        res["rope_tail_inv"] = gradcheck(
            lambda t: rope_tail(t, 17, inverse=True), (xq,))
    return res


def _cos(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)
                                 + 1e-12))


def leg_vs_numpy(recs_path, n_empty=4, warm=40, n_warmed=2):
    """(a) torch vs numpy conf rows on IDENTICAL cycles (lockstep rings).
    Runs under MIRROR_EMPTY_RING semantics? NO - uses the real ring path
    for the warmed leg and an empty ring for the empty leg (fresh rings,
    both trunks identical by construction)."""
    from .mirror_trunk import read_rec, conv_record_lists
    lists = conv_record_lists(recs_path)
    conv = max(lists, key=lambda c: len(lists[c]))
    rec_ids = lists[conv][:warm + n_empty + n_warmed + 8]
    with open(recs_path, "rb") as f:
        import mmap as _m
        mm = _m.mmap(f.fileno(), 0, access=_m.ACCESS_READ)
        metas = [read_rec(mm, ri) for ri in rec_ids]
        mm.close()
    np_trunk = _TRUNK
    t_trunk = _TTRUNK
    results = []
    # empty-ring leg: fresh rings, direct cycles (engine-realistic per
    # MILESTONE2-GATEA discovery 1: decode windows are empty/1-row)
    saved_flag = mt.MIRROR_EMPTY_RING
    mt.MIRROR_EMPTY_RING = True
    ring_n, ring_t = [[], [], []], [[], [], []]
    for pos, seed, taps, l1 in metas[:n_empty]:
        cn = np_trunk.cycle(pos, seed, taps, ring_n)
        ct = t_trunk.cycle(pos, seed, taps, ring_t)
        cn = np.asarray(cn)
        results.append({"kind": "empty-ring", "pos": pos,
                        "cos": [round(_cos(cn[d], ct[d].numpy()), 8)
                                for d in range(BLOCK)]})
    # warmed leg: ring PATH exercised (flag off) - lockstep advance_ring
    mt.MIRROR_EMPTY_RING = False
    ring_n, ring_t = [[], [], []], [[], [], []]
    for pos, seed, taps, l1 in metas[n_empty:n_empty + warm]:
        np_trunk.advance_ring(pos, taps, ring_n)
        t_trunk.advance_ring(pos, taps, ring_t)
    for pos, seed, taps, l1 in metas[n_empty + warm:n_empty + warm
                                     + n_warmed]:
        cn = np.asarray(np_trunk.cycle(pos, seed, taps, ring_n))
        ct = t_trunk.cycle(pos, seed, taps, ring_t)
        results.append({"kind": "warmed-ring", "pos": pos,
                        "ring": [len(r) for r in ring_n],
                        "cos": [round(_cos(cn[d], ct[d].numpy()), 8)
                                for d in range(BLOCK)]})
    mt.MIRROR_EMPTY_RING = saved_flag
    return results


_TRUNK = None
_TTRUNK = None


def main():
    global _TRUNK, _TTRUNK
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--gate-a", action="store_true")
    ap.add_argument("--recs", default=None)
    ap.add_argument("--parity", default=None)
    ap.add_argument("--npz", default="/Volumes/FireCuda520/ds4-train/"
                    "tensors-20261101/mtp_nonexpert_f32.npz")
    ap.add_argument("--embd", default="/Volumes/FireCuda520/ds4-train/"
                    "tensors-20261101/token_embd_f16.npz")
    ap.add_argument("--support-gguf", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "gguf",
        "DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf"))
    ap.add_argument("--conv", type=int, default=None)
    ap.add_argument("--compare", type=int, default=24)
    ap.add_argument("--stride", type=int, default=90)
    ap.add_argument("--expert-cache", type=int, default=64,
                    help="numpy expert slice cache cap (L0 memory)")
    ap.add_argument("--report", default=None)
    a = ap.parse_args()
    torch.set_num_threads(min(4, os.cpu_count() or 4))

    _TRUNK = mt.Trunk(a.npz, a.embd, a.support_gguf,
                      expert_cache_slices=a.expert_cache)
    _TTRUNK = TorchTrunk(_TRUNK)

    out = {"env": {"MIRROR_EMPTY_RING": mt.MIRROR_EMPTY_RING,
                   "HC_SIGMOID_STABLE": mt.HC_SIGMOID_STABLE,
                   "torch": torch.__version__}}
    if a.selfcheck:
        out["leg_c_gradcheck"] = leg_gradcheck()
        recs = a.recs or "/Volumes/FireCuda520/ds4-train/" \
            "20261103-day13/train_pid53508.recs"
        out["leg_a_vs_numpy"] = leg_vs_numpy(recs)
        ok = all(out["leg_c_gradcheck"].values()) and all(
            c >= 0.9999 for r in out["leg_a_vs_numpy"] for c in r["cos"])
        out["SELFCHECK_PASS"] = bool(ok)
    if a.gate_a:
        assert a.recs and a.parity, "--gate-a needs --recs and --parity"
        adapter = NumpyGateAdapter(_TTRUNK)
        lists = mt.conv_record_lists(a.recs)
        conv = a.conv or max(lists, key=lambda c: len(lists[c]))
        rep = mt.run_gate_a(a.recs, a.parity, adapter, conv,
                            a.compare, a.stride)
        out["gate_a_torch"] = rep
    print(json.dumps(out, indent=1))
    if a.report:
        with open(a.report, "w") as f:
            json.dump(out, f, indent=1)
    if a.selfcheck:
        raise SystemExit(0 if out["SELFCHECK_PASS"] else 1)


if __name__ == "__main__":
    main()
