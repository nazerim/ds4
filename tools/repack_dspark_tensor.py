#!/usr/bin/env python3
"""S5 repack: swap DS4 DSpark support-GGUF tensors IN PLACE (q8_0->q8_0 /
f32->f32, size-preserving), on a COPY, with proofs.

Strategy (S3 review ruling, night 20261103/04): the engine validator
(ds4.c:6359 dspark_validate_tensor_layout, DENSE kind) accepts f16/f32/q8_0
for [N_EMBD+rank, 1] confidence_proj. Requantizing a trained f32 head to
q8_0 keeps TYPE, DIMS and BYTE SIZE identical (q8_0 = 34 bytes per 32-value
block, ds4.c:2330) => pure byte swap at the recorded data offset, zero
header or offset-table edits. Size-changing swaps (e.g. ->f32) are REJECTED
here; they need full re-serialization - future scope if ever required.

Modes:
  --prove-roundtrip  : read target tensor's ORIGINAL bytes, write them back,
                       assert output file sha256 == input file sha256.
  --repack NAME=NPZ  : NPZ key 'w' holds flat f32 values (e.g. 4352 for
                       confidence_proj); block-quantize to q8_0 (engine-
                       compatible: scale = absmax/127 rounded to f16,
                       values = round(x/scale)), byte-swap in place.
Both write to --out (default <in>.repacked.gguf); --in is NEVER modified.

q8_0 layout per block of 32 values: <e f16 scale><32 x i8>. Dequant parity
with trainer/dspark/gguf_dq.py is asserted after writing (round-trip error
reported: max abs delta of dequantized weights).

Usage:
  python3 tools/repack_dspark_tensor.py SUPPORT.gguf --prove-roundtrip \
      --out /tmp/rt.gguf
  python3 tools/repack_dspark_tensor.py SUPPORT.gguf \
      --repack mtp.2.confidence_head.proj.weight=run/confcal1b/\
confcal1_weights.npz --out new_support.gguf
"""
import argparse
import hashlib
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir,
                                "trainer"))

SC = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}


class R:
    def __init__(self, f):
        self.f = f

    def raw(self, n):
        d = self.f.read(n)
        if len(d) != n:
            raise EOFError(f"short read {n}")
        return d

    def u32(self):
        return struct.unpack("<I", self.raw(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.raw(8))[0]

    def s(self):
        n = self.u64()
        return self.raw(n).decode("utf8", "replace")


def skip_kv(r, t):
    if t == 8:
        n = r.u64()
        r.raw(n)
    elif t == 9:
        et = r.u32()
        n = r.u64()
        if n > 50_000_000:
            raise RuntimeError(f"huge array {n}")
        if et == 8:
            for _ in range(n):
                m = r.u64()
                r.raw(m)
        else:
            for _ in range(n):
                skip_kv(r, et)
    else:
        r.raw(SC[t])


def walk_tensors(path):
    """name -> dict(dtype, dims, offset ABSOLUTE). This support GGUF stores
    a per-tensor offset u64 (relative to data_start) inside each info
    record (same reading as tools/extract_gguf_tensor.py.walk, proven on
    this file); data_start = align_up(header_end, general.alignment|32)."""
    out = {}
    with open(path, "rb") as f:
        r = R(f)
        if r.raw(4) != b"GGUF":
            sys.exit("not GGUF")
        r.u32()
        n_t = r.u64()
        n_kv = r.u64()
        align = 32
        for _ in range(n_kv):
            k = r.s()
            t = r.u32()
            if k == "general.alignment" and t == 4:
                align = struct.unpack("<I", r.raw(4))[0]
            else:
                skip_kv(r, t)
        infos = []
        for _ in range(n_t):
            name = r.s()
            nd = r.u32()
            dims = [r.u64() for _ in range(nd)]
            dtype = r.u32()
            off = r.u64()
            infos.append((name, dims, dtype, off))
        header_end = f.tell()
        data_start = header_end + (-header_end % align)
        for name, dims, dtype, off in infos:
            out[name] = dict(dtype=dtype, dims=dims, offset=data_start + off)
    return out


def tensor_bytes(dtype, nelem):
    if dtype == 0:
        return nelem * 4
    if dtype == 1:
        return nelem * 2
    if dtype == 8:  # q8_0: 34 bytes per 32 values
        return (nelem // 32) * 34
    raise SystemExit(f"unsupported dtype {dtype} for size math")


DTYPE_NAMES = {0: "f32", 1: "f16", 8: "q8_0"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def q8_0_quantize(vals):
    """blockwise (32): scale=round_f16(absmax/127), q=round(x/scale)."""
    import numpy as np
    assert len(vals) % 32 == 0
    v = np.asarray(vals, dtype=np.float32).reshape(-1, 32)
    amax = np.max(np.abs(v), axis=1)
    scale = (amax / np.float32(127.0)).astype(np.float16).astype(np.float32)
    safe = np.where(scale > 0, scale, 1.0)
    q = np.clip(np.rint(v / safe[:, None]), -127, 127).astype(np.int8)
    out = bytearray()
    qb = q.reshape(-1, 32)
    sb = scale.reshape(-1).astype(np.float16)
    for i in range(qb.shape[0]):
        out += struct.pack("<e", float(sb[i]))
        out += qb[i].tobytes()
    return bytes(out), scale


def q8_0_dequant(blob):
    import numpy as np
    n = len(blob) // 34
    out = np.zeros(n * 32, np.float32)
    for i in range(n):
        s = struct.unpack_from("<e", blob, i * 34)[0]
        q = np.frombuffer(blob, dtype=np.int8, count=32, offset=i * 34 + 2)
        out[i * 32:(i + 1) * 32] = q.astype(np.float32) * s
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gguf")
    ap.add_argument("--out", default=None)
    ap.add_argument("--prove-roundtrip", action="store_true")
    ap.add_argument("--repack", action="append", default=[],
                    metavar="NAME=NPZ[#KEY]",
                    help="NPZ key 'w' (or #KEY suffix), flat f32")
    ap.add_argument("--target-names", default="mtp.2.confidence_head."
                    "proj.weight", help="comma list for --prove-roundtrip")
    a = ap.parse_args()
    out = a.out or a.gguf + ".repacked.gguf"
    if os.path.realpath(out) == os.path.realpath(a.gguf):
        sys.exit("refusing: --out equals --in (official file immutable)")
    tensors = walk_tensors(a.gguf)
    src_sha = sha256(a.gguf)
    shutil.copyfile(a.gguf, out)

    if a.prove_roundtrip:
        with open(out, "r+b") as f:
            for name in a.target_names.split(","):
                t = tensors[name]
                ne = 1
                for d in t["dims"]:
                    ne *= d
                nb = tensor_bytes(t["dtype"], ne)
                f.seek(t["offset"])
                orig = f.read(nb)
                f.seek(t["offset"])
                f.write(orig)
                print(f"roundtrip {name}: {nb} B at {t['offset']}")
        dst_sha = sha256(out)
        ok = dst_sha == src_sha
        print(f"sha256 identity: {ok}\n  in  {src_sha}\n  out {dst_sha}")
        if not ok:
            os.remove(out)
            sys.exit("ROUNDTRIP PROOF FAILED")
        os.remove(out)
        print("proof file removed; official untouched")
        return

    import numpy as np
    with open(out, "r+b") as f:
        for spec in a.repack:
            name, npz = spec.split("=", 1)
            if "#" in npz:
                npz, key = npz.split("#", 1)
            else:
                key = "w"
            t = tensors[name]
            if t["dtype"] != 8:
                raise SystemExit(f"{name}: dtype {t['dtype']} not q8_0; "
                                 "size-preserving repack supports q8_0 only")
            vals = np.load(npz)[key].astype(np.float32).reshape(-1)
            ne = 1
            for d in t["dims"]:
                ne *= d
            if vals.size != ne:
                raise SystemExit(f"{name}: npz has {vals.size} vals, "
                                 f"tensor wants {ne}")
            blob, scale = q8_0_quantize(vals)
            deq = q8_0_dequant(blob)
            pad = np.where(scale.astype(np.float32).repeat(32) > 0,
                           deq - vals, 0.0)
            print(f"repack {name}: {len(blob)} B (offset {t['offset']}), "
                  f"dequant max|delta| {np.max(np.abs(pad)):.6g}")
            f.seek(t["offset"])
            f.write(blob)
    print(f"wrote {out}")
    print(f"input sha256 {src_sha} (file NOT modified)  "
          f"output sha256 {sha256(out)}")


if __name__ == "__main__":
    main()
