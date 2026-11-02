"""Shared constants for the DS4_TRAIN_DUMP capture contract.

Single source of truth mirrored from ds4.c ds4_td module (2026-10-29 v1.3).
If ds4.c's struct changes, change this + CAPTURE-SCHEMA.md + all td_* tools.
"""
from __future__ import annotations

import struct

RECORD_FMT = "<IiBBBB5i"          # pos seed streak n_label n_feat flags 5*label
HEADER_BYTES = struct.calcsize(RECORD_FMT)   # 32
FEAT_MAX = 12288                  # 3 taps x 4096 (V4 family runtime width)
RECORD_BYTES = HEADER_BYTES + FEAT_MAX       # 12320 — on-disk size
N_LABELS = 5
STREAK_OPEN = 255
STREAK_CLAMP = 254                # gap>=255 clamps here; exclude from means
FLAG_SEGMENT_START = 1

VOCAB = 129280
HIDDEN = 4096
TAPS = 3
BLOCK = 5
MARKOV_RANK = 256
CONF_INPUT = HIDDEN + MARKOV_RANK  # 4352

# control tokens (ds4flash.gguf tokenizer; see tools/vocab_ids.py)
T_DSML = 128825
T_THINK_OPEN, T_THINK_CLOSE = 128821, 128822

# banked corpus roots (blade)
CORPUS_ROOT = "/Volumes/FireCuda520/ds4-train"


def decode_e4m3(buf: memoryview | bytes) -> "object":
    """fp8 e4m3 (no NaN handling needed: encoder used satfinite).
    Returns numpy float32 of len(buf). Kept dependency-light; numpy imported
    lazily by callers.

    CODEBOOK CONVENTION (verified bit-exact vs ds4_td_f32_to_fp8_e4m3,
    review 2026-11-01): codes 0x7F / 0xFF are the encoder's NaN-sentinel
    and decode here to +/-480, and 0x80 (-0) decodes to -0.0. Measured
    corpora contain zero 0x7F bytes; if a future consumer swaps in
    torch.float8_e4m3fn decoding those codes become NaN - filter if so.
    """
    import numpy as np

    b = np.frombuffer(buf, dtype=np.uint8).astype(np.uint32)
    sign = np.where(b & 0x80, -1.0, 1.0).astype(np.float32)
    exp = (b >> 3) & 0xF
    mant = b & 0x7
    sub = mant.astype(np.float32) * (2.0 ** -9)
    nrm = (1.0 + mant.astype(np.float32) / 8.0) * np.ldexp(
        np.ones_like(mant, dtype=np.float32), exp.astype(np.int32) - 7)
    val = np.where(exp == 0, sub, nrm)
    return sign * val
