"""Milestone-2 DSpark trunk mirror: offline replay of the engine's per-cycle
draft forward (numpy). Semantics source: MILESTONE2-HANDOFF-20261103.md §7/§8b
(all formulas read from ds4.c + metal/*.metal on 2026-11-03, line-anchored).

Gate plan (handoff §4):
  A: conf rows (5 x 4096 f32, FINAL-stage hc_head mix PRE norm) vs parity
     dump rows  -> cos / max-err against the fp8-input noise floor; exact
     subset = cycles >= ~128 of a conv (seed-evicted window). Early cycles
     run seedless -> the delta quantifies the prefill-seed contribution.
  B: spec_logits rows -> (rms final->norm) @ output_head_f16 -> argmax after
     markov bias vs parity props[:dlen] (~97-99% expected, fp8 taps cap it).

Implemented here (full formulas known): rms_norm, hc mixer (24-coef split:
pre sigmoid+eps / post 2*sigmoid / comb row-softmax + 20 alternating sinkhorn
normalizations), collapse (sum pre_i*stream_i), expand (out_j = v*post_j +
sum comb[j][i]*r_i), fp8 KV fake-quant (E4M3 per 64-block over the 448
nope dims; rope 64 pass-through), router (sqrt(softplus) probs, bias on
selection only, top-6, renorm w/ 1/16384 floor * 1.5), rope tail mode 0
(adjacent pairs, theta=pos*base^(-2i/64), yarn inert ext=0 attn=1, inverse
negates sin), noncausal MLA attention (scale 1/sqrt(512), kv rows shared as
k and v, per-head sinks absorb denominator mass), swiglu with +-10 gate
clamp (shared form; routed form = impl checklist), 6-row batch assembly.
STUBS with kernel refs remaining: grouped-lora out proj (attn_output_a/b),
MoE experts apply (gguf_dq.expert_slice_f32 dt10/16 verified to cover the
support-gguf mtp stacks + shared shexp q8_0), final rms+base-head matmul.

Production drift flags observed at boot: hc_stable=ON norm_unify=ON. The
metal HC kernels document that the FUSED decode sites keep the historical
1/(1+exp(-z)) form regardless; mirror defaults to naive and exposes
HC_SIGMOID_STABLE to flip (A/B at gate level decides).
"""
from __future__ import annotations

import json
import os
import struct

import numpy as np

# ---- shape constants (ds4.c:601-640 DS4_SHAPE_FLASH; vision-exp keeps it) ----
N_EMBD = 4096
N_HC = 4
HC_MIX = 2 * N_HC + N_HC * N_HC          # 24
SINKHORN_ITERS = 20
N_HEAD = 64
N_HEAD_DIM = 512                         # MLA kv: 448 nope + 64 rope
N_ROT = 64
N_LORA_Q = 1024
N_LORA_O = 1024
N_OUT_GROUP = 8
N_EXPERT = 256
N_EXPERT_USED = 6
N_FFN_EXPERT = 2048
N_SWA = 128                              # cache window rows (SWA-1 usable)
BLOCK = 5
N_VOCAB = 129280
NOISE_TOKEN = 128799
EXPERT_WEIGHT_SCALE = 1.5
ROUTER_DENOM_FLOOR = 6.103515625e-5      # 1/16384, exact literal in metal
FP8KV_AMAX_FLOOR = 1e-4
FP8KV_MAX = 448.0                        # E4M3FN max finite


def rms_norm(x: np.ndarray, w: np.ndarray, eps: float) -> np.ndarray:
    """Rows last-axis: x * rsqrt(mean(x^2)+eps) * (1+w)?  ENGINE FORM TO PIN:
    ds4_gpu_rms_norm_* multiplies by tensor weight DIRECTLY (attn_norm w is
    ~0.2 mean, NOT 1+w centered) -- mirror reads model.py normalization
    helpers; placeholder = direct scale (matches milestone-1 conf side)."""
    ms = np.mean(x * x, axis=-1, keepdims=True)
    return x / np.sqrt(ms + eps) * w


def _sigmoid(z: np.ndarray, stable: bool) -> np.ndarray:
    if stable:
        return 0.5 * np.tanh(0.5 * z) + 0.5
    return 1.0 / (1.0 + np.exp(-z))


HC_SIGMOID_STABLE = os.environ.get("MIRROR_HC_STABLE", "0") != "0"
# gate-A diagnostics: MIRROR_EMPTY_RING=1 replays with no history window;
# if cos IMPROVES, the bug is in window content, not the per-cycle path.
MIRROR_EMPTY_RING = os.environ.get("MIRROR_EMPTY_RING", "0") != "0"
MIRROR_FULLWARM = os.environ.get("MIRROR_FULLWARM", "0") != "0"


def hc_split_sinkhorn(mix: np.ndarray, scale: np.ndarray, base: np.ndarray,
                      eps: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """mix (rows,24) -> (pre, post, comb) per dsv4_hc.metal kernels.
    pre  = sigmoid(mix[0:4]*scale[0] + base[0:4]) + eps
    post = 2*sigmoid(mix[4:8]*scale[1] + base[4:8])
    comb rows: r = mix[8+i*4:]*scale[2]+base[8:]; exp(r-rowmax); r/(sum)+eps;
    col: r *= 1/(sum_rows + eps); then 19 iters row-norm, col-norm."""
    pre = _sigmoid(mix[..., 0:4] * scale[0] + base[0:4],
                   HC_SIGMOID_STABLE) + eps
    post = 2.0 * _sigmoid(mix[..., 4:8] * scale[1] + base[4:8],
                          HC_SIGMOID_STABLE)
    r = mix[..., 8:24].copy()
    r = r * scale[2] + base[8:24]
    r = r.reshape(*r.shape[:-1], 4, 4)
    m = r.max(axis=-1, keepdims=True)
    r = np.exp(r - m)
    r = r / r.sum(axis=-1, keepdims=True) + eps
    col = 1.0 / (r.sum(axis=-2, keepdims=True) + eps)
    r = r * col
    for _ in range(SINKHORN_ITERS - 1):
        r = r / (r.sum(axis=-1, keepdims=True) + eps)
        col = 1.0 / (r.sum(axis=-2, keepdims=True) + eps)
        r = r * col
    return pre, post, r


def hc_collapse(streams: np.ndarray, pre: np.ndarray) -> np.ndarray:
    """streams (rows,4,4096), pre (rows,4) -> (rows,4096) = sum_i pre_i*s_i."""
    return np.einsum("...i,...id->...d", pre, streams)


def hc_expand(block_v: np.ndarray, residual: np.ndarray,
              post: np.ndarray, comb: np.ndarray) -> np.ndarray:
    """kernel_dsv4_hc_expand4: out_j = v*post_j + sum_i comb[j][i]*r_i.
    block_v (rows,4096), residual (rows,4,4096), post (rows,4),
    comb (rows,4,4) -> (rows,4,4096)."""
    return (block_v[..., None, :] * post[..., :, None]
            + np.einsum("...ji,...id->...jd", comb, residual))


# ---- FP8 E4M3FN fake-quant (dsv4_kv.metal kernel_dsv4_fp8_kv_quantize_f32) ----

_E4M3_TABLE = None


def _e4m3_lut() -> np.ndarray:
    """All 256 e4m3fn byte values -> float (NaN slot maps to 0 usage-free;
    we only need finite magnitudes: s|eeee|mmm, bias 7, max 448, subnormal
    step 2^-9.)"""
    global _E4M3_TABLE
    if _E4M3_TABLE is None:
        t = np.zeros(256, dtype=np.float32)
        for b in range(256):
            s = -1.0 if b & 0x80 else 1.0
            e, m = (b >> 3) & 0xF, b & 0x7
            if e == 0:
                t[b] = s * m * 2 ** -9
            elif e == 0xF and m == 0x7:
                t[b] = 0.0            # NaN encoding, never produced here
            else:
                t[b] = s * (1 + m / 8) * 2 ** (e - 7)
        _E4M3_TABLE = t
    return _E4M3_TABLE


def fp8_kv_quantize(kv: np.ndarray) -> np.ndarray:
    """kv (...,512) f32: fake-quant first 448 dims in 64-blocks (amax floor
    1e-4, scale=2^ceil(log2(amax/448)), clamp +-448, nearest e4m3 * scale);
    last 64 rope dims pass through. Matches kernel_dsv4_fp8_kv_quantize_f32
    EXCEPT rounding: kernel uses ds4_e4m3fn round-to-nearest-even by value
    search; we requantize via float->nearest table entry (equal for finite
    values; recheck bit-exactness at gate A)."""
    kv = kv.copy()
    lut = _e4m3_lut()
    finite = lut[lut != 0]
    nope = kv[..., :448].reshape(*kv.shape[:-1], 7, 64)
    amax = np.maximum(np.abs(nope).max(axis=-1, keepdims=True), FP8KV_AMAX_FLOOR)
    scale = np.exp2(np.ceil(np.log2(amax / FP8KV_MAX)))
    v = np.clip(nope / scale, -FP8KV_MAX, FP8KV_MAX)
    # round-to-nearest over the 15-bit magnitude grid (sign preserved)
    av, asig = np.abs(v), np.sign(v)
    idx = np.abs(av[..., None] - finite[finite > 0]).argmin(axis=-1)
    q = finite[finite > 0][idx] * asig
    kv[..., :448] = (q * scale).reshape(kv.shape[:-1] + (448,))
    return kv


def router(logits: np.ndarray, bias: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    """(rows,256) -> (selected (rows,6) int, weights (rows,6) f32).
    probs = sqrt(softplus(x)); scores = probs + bias (SELECTION ONLY);
    top-6 by score; weights = probs_sel / max(sum, 1/16384) * 1.5.
    TIE RULE: ds4_gpu_indexer_topk semantics unverified (handoff §8b) -
    argpartition/argsort ties may differ; affects only equal-score experts."""
    x = logits.astype(np.float64)
    probs = np.sqrt(np.log1p(np.exp(-np.abs(x))) + np.maximum(x, 0))
    scores = probs if bias is None else probs + bias
    sel = np.argsort(-scores, axis=-1, kind="stable")[..., :N_EXPERT_USED]
    p_sel = np.take_along_axis(probs, sel, axis=-1)
    denom = np.maximum(p_sel.sum(axis=-1, keepdims=True), ROUTER_DENOM_FLOOR)
    return sel, p_sel / denom * EXPERT_WEIGHT_SCALE


# ---- batch input assembly (handoff §7 STAGE FLOW, fill kernel 35000-35095) ----

def assemble_stage_input(taps_f32: np.ndarray, ids: np.ndarray,
                        embd: np.ndarray, main_w: np.ndarray,
                        main_norm_w: np.ndarray, eps: float) -> np.ndarray:
    """taps (12288,) fp8-dequantized f32 h[40,41,42]@pos-1; ids (5,) =
    [seed=t[pos], noise x4] (fill kernel rows1..5). row0 = rms_norm(
    main_proj(taps)) replicated x4 streams; rows1..5 = embd[ids] x4."""
    proj = taps_f32 @ main_w.T                    # (4096,) main_proj 4096x12288
    row0 = rms_norm(proj, main_norm_w, eps)
    out = np.zeros((BLOCK + 1, N_HC, N_EMBD), dtype=np.float32)
    out[0] = row0[None, :]
    for i in range(1, BLOCK + 1):
        out[i] = embd[ids[i - 1]].astype(np.float32)[None, :]
    return out.reshape(BLOCK + 1, N_HC * N_EMBD)


# ---- RoPE tail (dsv4_rope.metal kernel_dsv4_rope_tail_f32, mode 0 =
# adjacent pairs; production build has ROPE_EXP2_LOG2 OFF -> pow(); yarn is
# inert for dspark calls: ext_factor=0, freq_scale=1, attn_factor=1,
# n_ctx_orig=0; theta = pos * base^(-r/64), r = even index in rot region;
# inverse negates sin. Rows carry pos0+row*1. host pos_step=1 @ds4_metal.m
# 6242). ----

ROPE_FREQ_BASE = 10000.0

def rope_tail(x: np.ndarray, pos0: int, n_rot: int = N_ROT,
              inverse: bool = False, freq_base: float = ROPE_FREQ_BASE
              ) -> np.ndarray:
    """x (rows, head_dim) or (rows, heads, head_dim) f32: leading dims pass
    through, tail n_rot dims rotate as adjacent pairs; row t uses position
    pos0 + t; theta_i = pos * freq_base^(-2i/n_rot), i=0..n_rot/2-1."""
    x = np.asarray(x, dtype=np.float32)
    squeeze = x.ndim == 2
    x3 = x[:, None, :] if squeeze else x
    rows, heads, hd = x3.shape
    out = x3.copy()
    n_nope = hd - n_rot
    i = np.arange(n_rot // 2)
    idx0 = n_nope + 2 * i
    idx1 = idx0 + 1
    theta = (np.arange(rows, dtype=np.float64) + pos0)[:, None] \
        * np.power(float(freq_base), (-2.0 * i / n_rot)[None, :])
    c = np.cos(theta).astype(np.float32)
    s = np.sin(theta).astype(np.float32)
    if inverse:
        s = -s
    x0 = x3[:, :, idx0]
    x1 = x3[:, :, idx1]
    out[:, :, idx0] = x0 * c[:, None, :] - x1 * s[:, None, :]
    out[:, :, idx1] = x0 * s[:, None, :] + x1 * c[:, None, :]
    return out[:, 0, :] if squeeze else out


# ---- Noncausal MLA attention over raw kv rows + per-head sinks
# (ds4_gpu_attention_noncausal_raw_batch_heads_tensor; scale=1/sqrt(head_dim)
# from the shared encode; raw kv rows serve as BOTH k and v; sinks are extra
# logits that absorb probability mass (no value). softmax denominator
# includes exp(sink) [flash_attn.metal ~818]. ----

ATTN_SCALE = 1.0 / np.sqrt(N_HEAD_DIM, dtype=np.float64)

def attn_noncausal(q: np.ndarray, kv: np.ndarray, sinks: np.ndarray
                   ) -> np.ndarray:
    """q (rows,64,512), kv (visible,512), sinks (64,) -> out (rows,64,512)."""
    q = q.astype(np.float32)
    scores = np.einsum("rhd,vd->rhv", q, kv) * np.float32(ATTN_SCALE)
    sink_logit = sinks.astype(np.float32)[None, :, None]      # (1,H,1)
    m = np.maximum(scores.max(axis=-1, keepdims=True), sink_logit)
    p = np.exp(scores - m)
    ps = np.exp(sink_logit - m)
    den = p.sum(axis=-1, keepdims=True) + ps
    return np.einsum("rhv,vd->rhd", p / den, kv)


def swiglu(gate: np.ndarray, up: np.ndarray, limit: float = 10.0
           ) -> np.ndarray:
    """ds4_glm_swiglu (moe.metal:440): gate = min(gate, limit)  [UPPER ONLY];
    up = clamp(up, -limit, limit); out = silu(gate) * up. limit=10 for
    vision-exp shape (swiglu_clamp_exp ds4.c:668); applied to SHARED via
    glu.metal host DS4_SWIGLU_CLAMP_EXP and to ROUTED via moe swiglu_weight
    args.clamp_value."""
    g = np.minimum(gate, limit)
    u = np.clip(up, -limit, limit)
    return (g / (1.0 + np.exp(-g))) * u


# ============================ TRUNK FORWARD ============================
# Remaining semantics note: grouped-lora group slicing assumed = 8 heads per
# group contiguous (group_dim = 512*8) -- the parity gate catches layout
# errors here (would show as uniform low cos, not gradient).

RMS_EPS = 1.0e-20        # vision-exp shape overrides (ds4.c:665), NOT 1e-6!
HC_EPS = 1.0e-6          # ds4.c:666
WINDOW_ROWS = N_SWA - 1  # logical target window cap (127)


def _w(z: np.ndarray) -> np.ndarray:
    return z.reshape(-1).astype(np.float32)


class Trunk:
    """All-foreseeable weights for the 3-stage draft forward."""

    def __init__(self, npz_path: str, embd_path: str,
                 support_gguf: str | None = None,
                 expert_cache_slices: int = 200,
                 head_npz: str | None = None):
        z = np.load(npz_path)
        self.npz_path = npz_path
        self.head_npz = head_npz
        self.embd = np.load(embd_path)["weight"]              # f16 (129280,4096)
        self.stages = []
        for s in range(3):
            p = f"mtp.{s}."
            st = {
                "attn_norm": _w(z[p + "attn_norm.weight"]),
                "attn_kv": z[p + "attn_kv.weight"].astype(np.float32),
                "kv_a_norm": _w(z[p + "attn_kv_a_norm.weight"]),
                "q_a": z[p + "attn_q_a.weight"].astype(np.float32),
                "q_a_norm": _w(z[p + "attn_q_a_norm.weight"]),
                "q_b": z[p + "attn_q_b.weight"].astype(np.float32),
                "sinks": _w(z[p + "attn_sinks.weight"]),
                "out_a": z[p + "attn_output_a.weight"].astype(np.float32),
                "out_b": z[p + "attn_output_b.weight"].astype(np.float32),
                "hc_attn_fn": z[p + "hc_attn_fn.weight"].astype(np.float32),
                "hc_attn_base": _w(z[p + "hc_attn_base.weight"]),
                "hc_attn_scale": _w(z[p + "hc_attn_scale.weight"]),
                "ffn_norm": _w(z[p + "ffn_norm.weight"]),
                "gate_inp": z[p + "ffn_gate_inp.weight"].astype(np.float32),
                "exp_b": _w(z[p + "exp_probs_b.bias"]),
                "sh_gate": z[p + "ffn_gate_shexp.weight"].astype(np.float32),
                "sh_up": z[p + "ffn_up_shexp.weight"].astype(np.float32),
                "sh_down": z[p + "ffn_down_shexp.weight"].astype(np.float32),
                "hc_ffn_fn": z[p + "hc_ffn_fn.weight"].astype(np.float32),
                "hc_ffn_base": _w(z[p + "hc_ffn_base.weight"]),
                "hc_ffn_scale": _w(z[p + "hc_ffn_scale.weight"]),
            }
            self.stages.append(st)
        self.main_proj = z["mtp.0.main_proj.weight"].astype(np.float32)
        self.main_norm = _w(z["mtp.0.main_norm.weight"])
        f = 2
        self.head_fn = z[f"mtp.{f}.hc_head_fn.weight"].astype(np.float32)
        self.head_base = _w(z[f"mtp.{f}.hc_head_base.weight"])
        self.head_scale = _w(z[f"mtp.{f}.hc_head_scale.weight"])
        self.final_norm = _w(z[f"mtp.{f}.norm.weight"])
        self._support_gguf = support_gguf
        self._signed = None
        self._infos = None
        self._data_start = None
        self._ecache: dict = {}
        self._eq = []
        self._ecache_cap = expert_cache_slices

    # ---- routed experts: on-demand dequant of selected 6/stage/row ----
    def expert(self, stage: int, which: str, eid: int) -> np.ndarray:
        key = (stage, which, eid)
        v = self._ecache.get(key)
        if v is not None:
            return v
        if self._support_gguf is None:
            raise RuntimeError("expert tensors requested without support gguf")
        from . import gguf_dq
        if self._infos is None:
            self._infos, self._data_start = gguf_dq.gguf_tensor_table(
                self._support_gguf)
            self._signed = gguf_dq.parse_tables_from_ds4_c(
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "..", "ds4.c"))
        name = f"mtp.{stage}.ffn_{which}_exps.weight"
        v = gguf_dq.expert_slice_f32(self._support_gguf, name, eid,
                                    self._signed, self._infos,
                                    self._data_start)
        self._ecache[key] = v
        self._eq.append(key)
        if len(self._eq) > self._ecache_cap:
            for k in self._eq[:self._ecache_cap // 4]:
                if k in self._ecache:
                    del self._ecache[k]
            self._eq = self._eq[self._ecache_cap // 4:]
        return v

    # ---- per-stage machinery ----
    def _mixer(self, x_rows: np.ndarray, fn, base, scale
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        flat = rms_norm(x_rows.reshape(x_rows.shape[0], -1),
                        np.ones(N_HC * N_EMBD, dtype=np.float32), RMS_EPS)
        mix = flat @ fn.T
        return hc_split_sinkhorn(mix, scale, base, HC_EPS)

    def _ffn(self, st: dict, x: np.ndarray) -> np.ndarray:
        """x (rows,4096) post-ffn_norm -> routed+shared sum (rows,4096)."""
        logits = x @ st["gate_inp"].T + st["exp_b"][None, :]
        sel, w = router(logits, None)
        out = np.zeros_like(x)
        for r in range(x.shape[0]):
            acc = np.zeros(N_EMBD, dtype=np.float32)   # down: 2048 -> 4096
            for e_i in range(N_EXPERT_USED):
                eid = int(sel[r, e_i])
                gm = self.expert(self._cur_stage, "gate", eid)
                um = self.expert(self._cur_stage, "up", eid)
                dm = self.expert(self._cur_stage, "down", eid)
                gate_v = x[r] @ gm.T
                up_v = x[r] @ um.T
                mid = swiglu(gate_v, up_v) * np.float32(w[r, e_i])
                acc += mid @ dm.T
            out[r] = acc
        shared = swiglu(x @ st["sh_gate"].T, x @ st["sh_up"].T) @ st["sh_down"].T
        return out + shared

    def cycle(self, pos: int, seed: int, taps_f32: np.ndarray,
              ring: list[list[np.ndarray]]):
        """One draft cycle. ring = per-stage list of cached target kv rows
        (mutated in place: append current target row, trim to WINDOW_ROWS).
        Returns conf rows (5,4096) = final-stage hc_head mix PRE norm
        (gate A target)."""
        feature_pos = pos - 1
        main_x = rms_norm(taps_f32 @ self.main_proj.T, self.main_norm, RMS_EPS)
        stage_in = np.zeros((BLOCK + 1, N_HC, N_EMBD), dtype=np.float32)
        stage_in[0] = main_x[None, :]
        ids = [seed] + [NOISE_TOKEN] * (BLOCK - 1)
        for i, tok in enumerate(ids, start=1):
            stage_in[i] = self.embd[tok].astype(np.float32)[None, :]
        streams = None
        for s in range(3):
            st = self.stages[s]
            self._cur_stage = s
            rows = stage_in.reshape(BLOCK + 1, N_HC * N_EMBD)
            pre, post, comb = self._mixer(rows, st["hc_attn_fn"],
                                          st["hc_attn_base"],
                                          st["hc_attn_scale"])
            cur = hc_collapse(stage_in, pre)
            an = rms_norm(cur, st["attn_norm"], RMS_EPS)
            an[0] = main_x                      # engine alias (35686)
            drafts = an[1:]                     # (5,4096)
            qr = rms_norm(drafts @ st["q_a"].T, st["q_a_norm"], RMS_EPS)
            q = (qr @ st["q_b"].T).reshape(BLOCK, N_HEAD, N_HEAD_DIM)
            q = rms_norm(q, np.ones(N_HEAD_DIM, dtype=np.float32), RMS_EPS)
            q = rope_tail(q, pos)
            kv = rms_norm(an @ st["attn_kv"].T, st["kv_a_norm"], RMS_EPS)
            kv[0] = rope_tail(kv[0:1], feature_pos)[0]
            kv[1:] = rope_tail(kv[1:], pos)
            kv = fp8_kv_quantize(kv)
            ring_rows = ring[s]
            if ring_rows and not MIRROR_EMPTY_RING:
                visible = np.concatenate([np.stack(ring_rows), kv])
            else:
                visible = kv                   # window-first (L+6, 512)
            heads = attn_noncausal(q, visible, st["sinks"])
            heads = rope_tail(heads, pos, inverse=True)
            xg = heads.reshape(BLOCK, N_OUT_GROUP, N_HEAD // N_OUT_GROUP,
                               N_HEAD_DIM)      # 8 groups x 8 heads
            attn_out = np.zeros((BLOCK, N_EMBD), dtype=np.float32)
            for gidx in range(N_OUT_GROUP):
                x_g = xg[:, gidx].reshape(BLOCK, -1)            # (5,4096)
                a_g = st["out_a"][gidx * N_LORA_O:(gidx + 1) * N_LORA_O]
                b_g = st["out_b"][:, gidx * N_LORA_Q:(gidx + 1) * N_LORA_Q]
                attn_out += (x_g @ a_g.T) @ b_g.T
            streams = hc_expand(attn_out, stage_in[1:],
                                post[1:], comb[1:])     # draft-row mixers
            fpre, fpost, fcomb = self._mixer(streams, st["hc_ffn_fn"],
                                             st["hc_ffn_base"],
                                             st["hc_ffn_scale"])
            fcur = hc_collapse(streams, fpre)
            fcur = rms_norm(fcur, st["ffn_norm"], RMS_EPS)
            block2 = self._ffn(st, fcur)
            streams = hc_expand(block2, streams, fpost, fcomb)
            ring[s].append(kv[0].copy())
            if len(ring[s]) > WINDOW_ROWS:
                del ring[s][:len(ring[s]) - WINDOW_ROWS]
            stage_in = np.concatenate([stage_in[0:1], streams], axis=0)
        # final hidden -> conf rows (no normalization: PRE-norm binding)
        rows = streams.reshape(BLOCK, N_HC * N_EMBD)
        flat = rms_norm(rows, np.ones(N_HC * N_EMBD, dtype=np.float32),
                        RMS_EPS)
        pre_h = flat @ self.head_fn.T
        weights = _sigmoid(pre_h * self.head_scale[0] + self.head_base,
                           HC_SIGMOID_STABLE) + HC_EPS
        return np.einsum("rj,rjd->rd", weights, streams)

    # ring-only advance (cheap path for warmup/non-compared cycles):
    # the window stores ONLY the target kv rows (draft rows are transient)
    def advance_ring(self, pos: int, taps_f32: np.ndarray, ring):
        main_x = rms_norm(taps_f32 @ self.main_proj.T, self.main_norm,
                          RMS_EPS)
        fp = pos - 1
        for s, st in enumerate(self.stages):
            kv0 = rms_norm(main_x @ st["attn_kv"].T, st["kv_a_norm"], RMS_EPS)
            kv0 = rope_tail(kv0[None, :], fp)[0]
            kv0 = fp8_kv_quantize(kv0[None, :])[0]
            ring[s].append(kv0)
            if len(ring[s]) > WINDOW_ROWS:
                del ring[s][:len(ring[s]) - WINDOW_ROWS]


# ============================ GATE A HARNESS ============================

RECORD_BYTES = 12320
REC_HDR = struct.Struct("<IiBBBB5i")
PARITY_STRIDE = 81952
PARITY_HDR = struct.Struct("<IiBBH")


def conv_record_lists(recs_path: str) -> dict[int, list[int]]:
    """conv id -> record indices in file order, rebuilt from idx.jsonl
    (one flush batch = one session ring, v1.3 semantics, td_validate)."""
    idx_path = recs_path.replace(".recs", ".idx.jsonl")
    n = os.path.getsize(recs_path) // RECORD_BYTES
    out: dict[int, list[int]] = {}
    cursor = 0
    with open(idx_path) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            cnt = int(r["flushed"])
            out.setdefault(int(r["conv"]), []).extend(range(cursor,
                                                             cursor + cnt))
            cursor += cnt
    if cursor != n:
        raise RuntimeError(f"idx cursor {cursor} != file records {n}")
    return out


def read_rec(mm, i: int):
    r = REC_HDR.unpack_from(mm, i * RECORD_BYTES)
    pos, seed = r[0], r[1]
    n_label = r[3]
    l1 = r[7] if n_label >= 2 else None      # labels[1] = actual next token
    from .data_contract import decode_e4m3
    taps = decode_e4m3(mm[i * RECORD_BYTES + 32:
                         i * RECORD_BYTES + 32 + 3 * N_EMBD])
    return pos, seed, taps, l1


def parity_lookup(parity_path: str, wanted: dict):
    """single linear scan; wanted maps (pos, expected_seed) -> meta.
    Join rule (empirically verified on pid43176): parity hdr.seed/props[0]
    is the FIRST PROPOSAL; equals recs labels[1] (actual next token) iff
    the draft was accepted. (pos)-only keys collide across convs sharing
    position ranges, so accepted-first-proposal cycles are the safe join
    (a mild selection bias for gate A, revisit if rejection matters)."""
    res = {}
    with open(parity_path, "rb") as f:
        import mmap as _m
        mm = _m.mmap(f.fileno(), 0, access=_m.ACCESS_READ)
        n = mm.size() // PARITY_STRIDE
        for i in range(n):
            base = i * PARITY_STRIDE
            pos, seed, dlen, stages, _r = PARITY_HDR.unpack_from(mm, base)
            if (pos, seed) in wanted and (pos, seed) not in res:
                rows = np.frombuffer(
                    mm, dtype=np.float32, count=5 * N_EMBD,
                    offset=base + 32).reshape(5, N_EMBD).copy()
                res[(pos, seed)] = (int(dlen), rows)
        mm.close()
    return res


def run_gate_a(recs_path, parity_path, trunk: Trunk, conv: int,
               n_compare: int = 40, stride: int = 3, log=print):
    """compares ONLY cycles that are >= WINDOW_ROWS+8 past the last turn
    boundary (pos jump > 32 == new prompt injected == engine RE-SEEDED the
    window from that prefill's taps, which .recs does not contain; those
    seeded rows fully evict after 128 appends)."""
    lists = conv_record_lists(recs_path)
    if conv not in lists:
        raise RuntimeError(f"conv {conv} not in idx; have {sorted(lists)}")
    rec_ids = lists[conv]
    f = open(recs_path, "rb")
    import mmap as _m
    mm = _m.mmap(f.fileno(), 0, access=_m.ACCESS_READ)
    metas_all = [read_rec(mm, ri) for ri in rec_ids]   # (pos,seed,taps,l1)
    bounds = [0]
    for i in range(1, len(metas_all)):
        if metas_all[i][0] - metas_all[i - 1][0] > 32:
            bounds.append(i)
    eligible = set()
    MARGIN = int(os.environ.get("MIRROR_MARGIN", WINDOW_ROWS + 8))
    for bi, _b in enumerate(bounds):
        nxt = bounds[bi + 1] if bi + 1 < len(bounds) else len(metas_all)
        for i in range(_b + MARGIN, nxt):
            eligible.add(i)
    targets = sorted(i for i in range(len(metas_all))
                     if i in eligible and metas_all[i][3] is not None
                     )[:n_compare * stride:stride]
    tset = set(targets)
    wanted = {(metas_all[i][0], metas_all[i][3]): i for i in targets}
    par = parity_lookup(parity_path, wanted)
    log(f"conv {conv}: {len(rec_ids)} records, {len(bounds)} turn-bounds, "
        f"{len(targets)} target cycles, {len(par)} parity hits")
    ring = [[], [], []]
    last_bound = 0
    stats = {}
    for idx in range(len(metas_all)):
        if idx > (max(targets) if targets else -1):
            break
        pos, seed, taps, l1 = metas_all[idx]
        if idx in bounds and idx != 0:
            ring = [[], [], []]          # engine re-seeded: our reset mirrors
        if idx in tset:
            conf = trunk.cycle(pos, seed, taps, ring)
            hit = par.get((pos, l1))
            if hit is not None:
                dlen, rows = hit
                for d in range(min(dlen, BLOCK)):
                    a, b = conf[d], rows[d]
                    cos = float(a @ b / (np.linalg.norm(a)
                                         * np.linalg.norm(b) + 1e-12))
                    err = float(np.abs(a - b).max())
                    if idx == max(targets):
                        log(f"  [probe] slot{d} |mir|={np.linalg.norm(a):.3f} "
                            f"|par|={np.linalg.norm(b):.3f} "
                            f"mean {a.mean():+.5f}/{b.mean():+.5f} "
                            f"std {a.std():.4f}/{b.std():.4f}")
                    st = stats.setdefault(d, {"n": 0, "cos_sum": 0.0,
                                              "cos_min": 1.0,
                                              "err_sum": 0.0,
                                              "err_max": 0.0})
                    st["n"] += 1
                    st["cos_sum"] += cos
                    st["cos_min"] = min(st["cos_min"], cos)
                    st["err_sum"] += err
                    st["err_max"] = max(st["err_max"], err)
                    log(f"  idx={idx} pos={pos} slot{d}: "
                        f"cos={cos:.5f} maxerr={err:.4f}")
        else:
            if MIRROR_FULLWARM:
                trunk.cycle(pos, seed, taps, ring)   # exact same path
            else:
                trunk.advance_ring(pos, taps, ring)
    mm.close()
    report = {"conv": conv, "targets": len(targets),
              "parity_hits": len(par), "slots": {}}
    for d, st in sorted(stats.items()):
        report["slots"][f"slot{d}"] = {
            "n": st["n"],
            "cos_mean": round(st["cos_sum"] / max(st["n"], 1), 6),
            "cos_min": round(st["cos_min"], 6),
            "maxerr_mean": round(st["err_sum"] / max(st["n"], 1), 5),
            "maxerr_max": round(st["err_max"], 5)}
        log(f"SLOT {d}: {report['slots'][f'slot{d}']}")
    return report


def noise_sensitivity(trunk: Trunk, recs_path, parity_path, conv, idxs,
                      rel=0.005, log=print):
    """Feed the mirror its OWN fp8 taps twice: exact replay vs tiny gaussian
    perturbation (rel) injected at main_x. If cos(mirror_a, mirror_b) is the
    same order as cos(mirror, parity) (~0.95), the trunk's nonlinearity
    (sinkhorn/router boundaries) amplifies capture noise to that floor and
    no gross port bug is needed to explain it. If >>0.99, a bug remains."""
    lists = conv_record_lists(recs_path)
    rec_ids = lists[conv]
    f = open(recs_path, "rb")
    import mmap as _m
    mm = _m.mmap(f.fileno(), 0, access=_m.ACCESS_READ)
    metas = {i: read_rec(mm, rec_ids[i]) for i in range(len(rec_ids))}
    bounds = sorted(i for i in range(1, len(rec_ids))
                    if metas[i][0] - metas[i - 1][0] > 32) + [len(rec_ids)]
    for target in idxs:
        def warm(to):
            ring = [[], [], []]
            b = max([x for x in bounds if x <= to] or [0])
            for i in range(b, to):
                p, s, t, _l = metas[i]
                trunk.advance_ring(p, t, ring)
            return ring, metas[target]
        ring_a, (pos, seed, taps, _l1) = warm(target)
        conf_a = trunk.cycle(pos, seed, taps, ring_a)
        ring_b, _ = warm(target)
        rng = np.random.default_rng(target)
        noise = (rng.normal(0.0, 1.0, taps.shape).astype(np.float32)
                 * (rel * float(np.sqrt((taps * taps).mean()))))
        conf_b = trunk.cycle(pos, seed, taps + noise, ring_b)
        cs = [float(conf_a[d] @ conf_b[d]
                    / (np.linalg.norm(conf_a[d]) * np.linalg.norm(conf_b[d])
                       + 1e-12)) for d in range(BLOCK)]
        log(f"noise sens idx={target} rel={rel}: self-cos per slot "
            f"{[round(c, 4) for c in cs]}")
    mm.close()


def markov_propose_chain(logits: np.ndarray, w1: np.ndarray, w2: np.ndarray,
                         seed: int, count: int) -> np.ndarray:
    """dspark_apply_markov_greedy_probe port: state = w1[prev]; bias =
    w2 @ state; argmax(logits[d] + bias); prev = proposal. (q8_0 variant
    ds4.c:36634 = same math quantized; CPU fallthrough per 37070.)"""
    prev = seed
    out = np.zeros(count, dtype=np.int64)
    for d in range(count):
        state = w1[prev].astype(np.float64)
        bias = w2 @ state                       # (V,) bias per candidate
        score = logits[d].astype(np.float64) + bias
        prev = int(np.argmax(score))
        out[d] = prev
    return out


def run_gate_b(recs_path, parity_path, trunk: Trunk, conv: int,
               n_compare: int = 20, stride: int = 60, log=print):
    """Two legs per target cycle:
       CONTROL: parity's OWN conf rows -> final norm -> head -> chain vs
                props  (isolates head+chain fidelity; expect ~100%)
       FULL:    mirror conf rows -> same tail -> chain vs props
                (end-to-end proposal match; expect 97-99% per fp8 floor)
       Join: pos-unique cycles only (exactly one parity record at that pos)."""
    from .mirror import load_head_weights
    w = load_head_weights(trunk.npz_path)
    lists = conv_record_lists(recs_path)
    rec_ids = lists[conv]
    f = open(recs_path, "rb")
    import mmap as _m
    mm = _m.mmap(f.fileno(), 0, access=_m.ACCESS_READ)
    metas = [read_rec(mm, ri) for ri in rec_ids]
    g = open(parity_path, "rb")
    gm = _m.mmap(g.fileno(), 0, access=_m.ACCESS_READ)
    np_rec = gm.size() // PARITY_STRIDE
    pcount, prec = {}, {}
    want_pos = {m[0] for m in metas}
    for i in range(np_rec):
        base = i * PARITY_STRIDE
        pos, seed, dlen, _st, _r = PARITY_HDR.unpack_from(gm, base)
        if pos not in want_pos:
            continue
        pcount[pos] = pcount.get(pos, 0) + 1
        if pcount[pos] == 1:
            props = np.frombuffer(gm, dtype=np.int32, count=BLOCK,
                                  offset=base + 12).copy()
            hid = np.frombuffer(gm, dtype=np.float32, count=5 * N_EMBD,
                                offset=base + 32).reshape(5, N_EMBD).copy()
            prec[pos] = (int(dlen), props, hid)
    gm.close()
    head = np.load(trunk.head_npz)["weight"].astype(np.float32)
    log(f"gate B: conv {conv}, {len(want_pos)} cycle-positions, "
        f"{sum(1 for p in want_pos if pcount.get(p) == 1)} pos-unique")
    targets = sorted(i for i, m in enumerate(metas)
                     if pcount.get(m[0]) == 1 and m[3] is not None
                     and prec[m[0]][0] > 0)
    targets = targets[:n_compare * stride:stride]
    stats = {"control": {}, "full": {}}
    depth_stats = {"control": {}, "full": {}}
    for idx in targets:
        pos, seed, taps, _l1 = metas[idx]
        dlen, props, hid = prec[pos]
        conf_m = trunk.cycle(pos, seed, taps, [[], [], []])
        for leg, conf in (("control", hid), ("full", conf_m)):
            normed = rms_norm(conf[:dlen], trunk.final_norm, RMS_EPS)
            logits = normed @ head.T
            pred = markov_propose_chain(logits, w["markov_w1"],
                                        w["markov_w2"], seed, dlen)
            m = int((pred[:dlen] == props[:dlen]).sum())
            st = stats[leg]
            st["hits"] = st.get("hits", 0) + m
            st["total"] = st.get("total", 0) + int(dlen)
            for d in range(dlen):
                ds = depth_stats[leg].setdefault(d, [0, 0])
                ds[0] += int(pred[d] == props[d])
                ds[1] += 1
            log(f"  idx={idx} pos={pos} dlen={dlen} {leg}: "
                f"{m}/{dlen} pred0={pred[0]} prop0={props[0]}")
    mm.close()
    report = {}
    for leg in ("control", "full"):
        st = stats[leg]
        tot = st.get("total", 0)
        report[leg] = {"slots": tot,
                       "match_rate": round(st.get("hits", 0) / tot, 4)
                       if tot else None,
                       "per_slot": {d: round(v[0] / v[1], 4)
                                    for d, v in sorted(
                                        depth_stats[leg].items())}}
        log(f"GATE B {leg}: {report[leg]}")
    return report


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate-a", action="store_true")
    ap.add_argument("--gate-b", action="store_true")
    ap.add_argument("--head-npz", default="/Volumes/FireCuda520/ds4-train/"
                    "tensors-20261101/output_head_f16.npz")
    ap.add_argument("--recs", required=True)
    ap.add_argument("--parity", required=True)
    ap.add_argument("--npz", default="/Volumes/FireCuda520/ds4-train/"
                    "tensors-20261101/mtp_nonexpert_f32.npz")
    ap.add_argument("--embd", default="/Volumes/FireCuda520/ds4-train/"
                    "tensors-20261101/token_embd_f16.npz")
    ap.add_argument("--support-gguf", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "gguf",
        "DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf"))
    ap.add_argument("--conv", type=int, default=None)
    ap.add_argument("--compare", type=int, default=40)
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--report", default=None)
    ap.add_argument("--noise-rel", type=float, default=0.0)
    ap.add_argument("--noise-idxs", default="230,680,830")
    a = ap.parse_args()
    trunk = Trunk(a.npz, a.embd, a.support_gguf, head_npz=a.head_npz)
    if a.conv is None:
        lists = conv_record_lists(a.recs)
        a.conv = max(lists, key=lambda c: len(lists[c]))
        print("auto conv", a.conv, len(lists[a.conv]), "records")
    if a.noise_rel > 0:
        noise_sensitivity(trunk, a.recs, a.parity, a.conv,
                          [int(x) for x in a.noise_idxs.split(",")],
                          rel=a.noise_rel)
        return
    if a.gate_b:
        rep = run_gate_b(a.recs, a.parity, trunk, a.conv, a.compare, a.stride)
    else:
        rep = run_gate_a(a.recs, a.parity, trunk, a.conv, a.compare, a.stride)
    if a.report:
        with open(a.report, "w") as f:
            json.dump(rep, f, indent=1)


if __name__ == "__main__":
    main()





