"""Numpy dequant of the ds4 GGUF expert formats q2_k and iq2_xxs.

1:1 port of the engine reference formulas (READ from ds4.c, not guessed):
  struct block_q2_K  { u8 scales[16] @0; u8 qs[64] @16; u16 d @80;
                        u16 dmin @82; }                                   84B
      value(k) = d*(scales[g]&0xF)*q - dmin*(scales[g]>>4)
      g=k//16; l=k%16; q_base=32*(g//8)+16*(g&1); shift=((g//2)&3)*2
      q=(qs[q_base+l]>>shift)&3                                    (ds4.c:4002)
  struct block_iq2_xxs { u16 d @0; u16 qs[32] @2; }                         66B
      per ib32 0..7: aux32[0] = u16 pair 0-1 (grid bytes aux8[0..3]),
      aux32[1] = u16 pair 2-3 (sign idx = (aux32[1]>>7l)&127,
      scale = 0.125*d*(2*(aux32[1]>>28)+1)); vals[32*ib+8l:+8] =
      scale * signed_grid[aux8[l]][sign_idx]            (ds4.c:4344, tables:1169)

Codebooks parsed from the C initializers (hex AND decimal literals).
Reviewed 2026-11-01: corrected vectorization is BIT-EXACT vs scalar
transcriptions of the engine formulas on real tensor data
(tests/test_gguf_dq.py pins the first blocks of ffn_down_exps /
ffn_gate_exps).

Usage: python -m dspark.gguf_dq <support.gguf> <out.npz> [expert_idx]
"""
from __future__ import annotations

import re
import struct
import sys

import numpy as np

QK = 256


def parse_tables_from_ds4_c(src_path="ds4.c"):
    txt = open(src_path, encoding="utf8", errors="replace").read()

    def arr(name):
        m = re.search(rf"{name}\[[0-9]*\]\s*=\s*\{{(.*?)\n\}};", txt, re.S)
        assert m, name
        return [int(x, 0) for x in re.findall(r"0[xX][0-9a-fA-F]+|\d+",
                                              m.group(1))]

    kmask = np.array(arr("kmask_iq2xs"), dtype=np.uint16)
    ksigns = np.array(arr("ksigns_iq2xs"), dtype=np.uint16)
    grid = np.array(arr("iq2xxs_grid"), dtype=np.uint64)
    assert len(kmask) == 8 and len(ksigns) == 128 and len(grid) == 256
    assert kmask.max() <= 0xFF and ksigns.max() <= 0xFF
    gbytes = np.stack([(grid >> (8 * j)) & 0xFF for j in range(8)], axis=1)
    signed = np.empty((256, 128, 8), dtype=np.int8)
    for s in range(128):
        flip = (gbytes != 0) & ((kmask & ksigns[s]) != 0).ravel()
        signed[:, s, :] = np.where(flip, -gbytes, gbytes).astype(np.int8)
    return signed


def _f16_at(buf2d: np.ndarray, byte_off: int) -> np.ndarray:
    return buf2d[:, byte_off:byte_off + 2].copy().view(np.float16) \
             .astype(np.float32).ravel()


def deq_q2_k(buf: bytes) -> np.ndarray:
    """-> float32 array, QK values per 84-byte block, block-major."""
    a = np.frombuffer(buf, dtype=np.uint8).reshape(-1, 84)
    d = _f16_at(a, 80)[:, None]
    dmin = _f16_at(a, 82)[:, None]
    scales = a[:, 0:16].astype(np.int32)
    qs = a[:, 16:80]
    out = np.empty((len(a), QK), dtype=np.float32)
    for g in range(16):
        base = 32 * (g // 8) + 16 * (g & 1)
        shift = ((g // 2) & 3) * 2
        q = ((qs[:, base:base + 16] >> shift) & 3).astype(np.float32)
        mult = (scales[:, g] & 0xF).astype(np.float32)[:, None]
        mins = (scales[:, g] >> 4).astype(np.float32)[:, None]
        out[:, g * 16:(g + 1) * 16] = d * mult * q - dmin * mins
    return out.reshape(-1)


def deq_iq2_xxs(buf: bytes, signed: np.ndarray) -> np.ndarray:
    a = np.frombuffer(buf, dtype=np.uint8).reshape(-1, 66)
    d = _f16_at(a, 0)
    aux = a[:, 2:66].copy().view(np.uint16).reshape(-1, 8, 4)
    out = np.empty((len(a), QK), dtype=np.float32)
    for ib in range(8):
        u = aux[:, ib, :]
        a0 = u[:, 0].astype(np.uint32) | (u[:, 1].astype(np.uint32) << 16)
        a1 = u[:, 2].astype(np.uint32) | (u[:, 3].astype(np.uint32) << 16)
        grid_idx = np.stack([(a0 >> (8 * l)) & 0xFF for l in range(4)], axis=1)
        scale = 0.125 * d * (2.0 * (a1 >> 28) + 1.0)
        for l in range(4):
            sidx = ((a1 >> (7 * l)) & 127).astype(np.int64)
            g = signed[grid_idx[:, l], sidx]              # [nb, 8]
            out[:, ib * 32 + l * 8: ib * 32 + (l + 1) * 8] = \
                scale[:, None] * g.astype(np.float32)
    return out.reshape(-1)


def gguf_tensor_table(path):
    f = open(path, "rb")

    def rd_str():
        n = struct.unpack("<Q", f.read(8))[0]
        return f.read(n).decode("utf8")

    def read_val(t):
        if t in (0, 1, 7):
            return f.read(1)[0]
        if t in (2, 3):
            return struct.unpack("<H", f.read(2))[0]
        if t in (4, 5):
            return struct.unpack("<I", f.read(4))[0]
        if t == 6:
            return struct.unpack("<f", f.read(4))[0]
        if t == 8:
            return rd_str()
        if t in (10, 11):
            return struct.unpack("<Q", f.read(8))[0]
        if t == 12:
            return struct.unpack("<d", f.read(8))[0]
        if t == 9:
            at = struct.unpack("<I", f.read(4))[0]
            n = struct.unpack("<Q", f.read(8))[0]
            return [read_val(at) for _ in range(n)]
        raise ValueError(t)

    assert f.read(4) == b"GGUF"
    f.read(4)
    nt = struct.unpack("<Q", f.read(8))[0]
    nkv = struct.unpack("<Q", f.read(8))[0]
    meta = {}
    for _ in range(nkv):
        k = rd_str()
        t = struct.unpack("<I", f.read(4))[0]
        meta[k] = read_val(t)
    infos = {}
    for _ in range(nt):
        name = rd_str()
        nd = struct.unpack("<I", f.read(4))[0]
        dims = [struct.unpack("<Q", f.read(8))[0] for _ in range(nd)]
        dt = struct.unpack("<I", f.read(4))[0]
        off = struct.unpack("<Q", f.read(8))[0]
        infos[name] = (dims, dt, off)
    align = meta.get("general.alignment", 32)
    data_start = (f.tell() + align - 1) // align * align
    f.close()
    return infos, data_start


def expert_slice_f32(path, name, expert, signed, infos, data_start):
    """Materialize expert `expert` as [ne1, ne0] f32 (rows = ne1)."""
    dims, dt, off = infos[name]
    ne0, ne1, ne2 = dims[0], dims[1], dims[2]
    assert ne0 % QK == 0
    nblk = ne0 // QK
    if dt == 10:                       # q2_k
        bsz, dq = 84, deq_q2_k
    elif dt == 16:                     # iq2_xxs
        bsz, dq = 66, lambda b: deq_iq2_xxs(b, signed)
    else:
        raise ValueError(f"{name}: dt={dt} is not an expert format")
    out = np.empty((ne1, ne0), dtype=np.float32)
    with open(path, "rb") as fd:
        base = data_start + off
        for r in range(ne1):
            o = base + ((expert * ne1 + r) * nblk) * bsz
            fd.seek(o)
            out[r] = dq(fd.read(nblk * bsz))
    return out


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    path, out_npz = sys.argv[1], sys.argv[2]
    e = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    infos, data_start = gguf_tensor_table(path)
    signed = parse_tables_from_ds4_c(
        "/Users/naz/Projects/ds4/ds4.c")
    results = {}
    for name, (dims, dt, off) in infos.items():
        if len(dims) != 3 or dims[2] != 256:
            continue
        vals = expert_slice_f32(path, name, e, signed, infos, data_start)
        results[name] = vals
        print(f"{name}: shape={vals.shape} absmax={np.abs(vals).max():.4f} "
              f"std={vals.std():.5f} nonzero={100*(vals != 0).mean():.1f}%")
    np.savez(out_npz, expert=e, **results)
    print(f"wrote {out_npz}")


if __name__ == "__main__":
    main()
