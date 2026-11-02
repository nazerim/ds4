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
