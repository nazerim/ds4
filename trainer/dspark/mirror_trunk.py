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
selection only, top-6, renorm w/ 1/16384 floor * 1.5).
STUBS with kernel refs to port next: rope_tail(+inverse), attention
(noncausal+sinks), grouped-lora out proj (attn_output_a/b), MoE experts
(q2_k/iq2_xxs dequant via gguf_dq + shared q8_0), main_proj/stage0 inputs,
spec_logits assembly.

Production drift flags observed at boot: hc_stable=ON norm_unify=ON. The
metal HC kernels document that the FUSED decode sites keep the historical
1/(1+exp(-z)) form regardless; mirror defaults to naive and exposes
HC_SIGMOID_STABLE to flip (A/B at gate level decides).
"""
from __future__ import annotations

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


HC_SIGMOID_STABLE = False   # flip per gate A/B; see module docstring


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


# STUBS (port next, kernel refs):
#   rope_tail(x, rows, heads, dim, rot, pos_start, inverse) -- dsv4_rope.metal
#   attn_noncausal(q, kv_rows, sinks, scale)               -- flash_attn.metal
#   lora_out(attn_heads_out, attn_output_a, attn_output_b) -- dense q8_0 pair
#   moe_ffn(x, layer)                                      -- gguf_dq experts
#   final_head(conf_mix) -> spec_logits                    -- output_head_f16
