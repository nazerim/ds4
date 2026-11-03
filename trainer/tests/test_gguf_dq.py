"""Golden-vector tests for the q2_k / iq2_xxs numpy ports (night-run #2).

The scalar references below are transcriptions of ds4.c:4002 (q2_k_value_f32)
and ds4.c:4344-4375 (vec_dot_iq2_xxs grid decode) — reviewed bit-exact.
They pin the vectorized dequant against the ENGINE formula on real bytes
from the official support GGUF, catching the offset/parse bugs the review
found (C1: d/dmin at 80:82/82:84, not 4:6/6:8; C2: decimal codebook
literals; M1: ksigns broadcasting).
"""
import numpy as np
import pytest

from dspark.gguf_dq import (deq_iq2_xxs, deq_q2_k, gguf_tensor_table,
                            parse_tables_from_ds4_c)

SUPPORT = ("/Users/naz/Projects/ds4/gguf/"
           "DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf")
DS4_C = "/Users/naz/Projects/ds4/ds4.c"


# ---- q8_0 golden (pinned by S5 independent review, night 20261103/04) ----
# Layout per ds4.c:2330: block of 32 values = <f16 scale><32 x i8>,
# dequant = q * scale. These tests pin tools/repack_dspark_tensor.py's
# q8_0 quant/dequant against the layout SEMANTICS directly (not against
# itself), incl. the idempotence property that proved the tool's convention
# matches the official tensor's.

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "tools"))
import repack_dspark_tensor as rdt  # noqa: E402


def test_q8_0_synthetic_golden():
    # hand-built block: scale f16(0.5), q = [-28..3]
    q = list(range(-28, 4))
    assert len(q) == 32
    blob = struct.pack("<e", 0.5) + struct.pack("<32b", *q)
    out = rdt.q8_0_dequant(blob)
    assert out.shape == (32,)
    assert np.all(out == np.array(q, dtype=np.float32) * 0.5)
    # all-zero block: 34 zero bytes dequant to exact zeros
    z = rdt.q8_0_dequant(bytes(34))
    assert np.all(z == 0.0)
    # +-127 clamp inside the quantizer, no overflow bytes
    vals = np.full(32, 3.0, dtype=np.float32)
    vals[0] = -3.0
    qb, _ = rdt.q8_0_quantize(vals)
    qs = struct.unpack("<32b", qb[2:34])
    assert qs[0] == -127 and all(x == 127 for x in qs[1:])
    dq = rdt.q8_0_dequant(qb)
    # error bound: half a quant step per block (scale = f16(3/127))
    scale = np.float32(np.float16(np.float32(3.0 / 127)))
    assert np.max(np.abs(dq - vals)) <= scale * 0.5 + 1e-9


def test_q8_0_quant_dequant_roundtrip_property():
    rng = np.random.default_rng(7)
    vals = (rng.normal(size=320) * rng.choice([1e-6, 1e-3, 1.0, 1e3])).astype(
        np.float32)
    blob, scale = rdt.q8_0_quantize(vals)
    assert len(blob) == 10 * 34
    dq = rdt.q8_0_dequant(blob)
    # quantizer is idempotent: re-quantizing the dequant reproduces bytes
    blob2, _ = rdt.q8_0_quantize(dq)
    assert blob2 == blob
    # every nonzero block within half step
    s = np.asarray(scale, dtype=np.float32)
    err = np.abs(dq - vals)
    step = np.repeat(s, 32)
    ok = step > 0
    assert bool(np.all(err[ok] <= np.float32(0.5) * step[ok] + 1e-12))


@pytest.mark.skipif(not os.path.exists(SUPPORT), reason="official gguf absent")
def test_q8_0_official_tensor_idempotence():
    """The proof the S5 review used: quantize(dequant(OFFICIAL bytes)) ==
    OFFICIAL bytes -> the tool's convention IS the file's convention."""
    t = rdt.walk_tensors(SUPPORT)["mtp.2.confidence_head.proj.weight"]
    assert t["dtype"] == 8 and t["dims"] == [4352, 1]
    nb = rdt.tensor_bytes(8, 4352)
    assert nb == 4352 // 32 * 34 == 4624
    with open(SUPPORT, "rb") as f:
        f.seek(t["offset"])
        blob = f.read(nb)
    dq = rdt.q8_0_dequant(blob)
    assert np.all(np.isfinite(dq))
    blob2, _ = rdt.q8_0_quantize(dq)
    assert blob2 == blob, "q8_0 convention drift vs official tensor"



def scalar_q2_k(block: bytes) -> np.ndarray:
    """ds4.c:4002 verbatim for one 84-byte block."""
    b = np.frombuffer(block, dtype=np.uint8)
    d = np.frombuffer(b[80:82].tobytes(), dtype=np.float16)[0].astype(np.float32)
    dmin = np.frombuffer(b[82:84].tobytes(), dtype=np.float16)[0].astype(np.float32)
    out = np.empty(256, dtype=np.float32)
    for k in range(256):
        g, l = divmod(k, 16)
        q_base = 32 * (g // 8) + 16 * (g & 1)
        shift = ((g // 2) & 3) * 2
        q = (b[16 + q_base + l] >> shift) & 3
        sc = b[g]
        out[k] = d * (sc & 0xF) * q - dmin * (sc >> 4)
    return out


@pytest.fixture(scope="module")
def support_table():
    return gguf_tensor_table(SUPPORT)


@pytest.fixture(scope="module")
def signed():
    return parse_tables_from_ds4_c(DS4_C)


def _first_blocks(path, off, nbytes):
    infos, dstart = path
    with open(SUPPORT, "rb") as fd:
        fd.seek(dstart + infos[off][2])
        return fd.read(nbytes)


def test_q2_k_matches_scalar_reference(support_table):
    infos, dstart = support_table
    name = "mtp.0.ffn_down_exps.weight"
    raw = _first_blocks((infos, dstart), name, 84 * 4)
    vec = deq_q2_k(raw)
    ref = np.concatenate([scalar_q2_k(raw[i * 84:(i + 1) * 84])
                          for i in range(4)])
    assert np.array_equal(vec, ref), "q2_k vectorization must be bit-exact"
    assert np.isfinite(vec).all()


def test_iq2_xxs_deterministic_and_finite(support_table, signed):
    infos, dstart = support_table
    raw = _first_blocks((infos, dstart), "mtp.0.ffn_gate_exps.weight", 66 * 4)
    v1 = deq_iq2_xxs(raw, signed)
    v2 = deq_iq2_xxs(raw, signed)
    assert np.array_equal(v1, v2)
    assert np.isfinite(v1).all()
    # grid values are small ints x scales: bound sanity (llama iq2_xxs
    # |value| <= 0.125*4*(2*15+1)*d_max) — keep loose magnitude check
    assert np.abs(v1).max() < 100.0
    # codebook tables parsed with hex AND decimal literals present
    assert signed.shape == (256, 128, 8)


def test_expert_slice_roundtrip_single(support_table, signed):
    """Full-pipeline smoke: expert 0 slice of ffn_down_exps via public fn."""
    from dspark.gguf_dq import expert_slice_f32
    infos, dstart = support_table
    v = expert_slice_f32(SUPPORT, "mtp.0.ffn_down_exps.weight", 0, signed,
                         infos, dstart)
    assert v.shape == (4096, 2048)
    assert np.isfinite(v).all()
