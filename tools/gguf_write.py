#!/usr/bin/env python3
"""GGUF writer in the ds4 dialect (types: bool=7, string=8, array=9,
u64=10, i64=11, f64=12) — the Stage-4 repack substrate: trainer tensors ->
support-model file the engine can ingest.

Round-trip proof: parse the official support GGUF preserving every original
type code, re-encode header+kv+tensor-table through this writer, splice the
original tensor DATA bytes, require byte-identical sha256. Proves the
writer's metadata encoding is exact for this file class.

Usage: tools/gguf_write.py --roundtrip <src.gguf> <out.gguf>
"""
import hashlib
import json
import struct
import sys

SZ = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
FMT = {2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 10: "<Q", 11: "<q", 12: "<d"}


def encode_typed(t, v):
    """(type code, python value) -> bytes, ds4 gguf dialect."""
    if t == 8:
        b = v.encode("utf8")
        return struct.pack("<Q", len(b)) + b
    if t == 7:
        return struct.pack("<B", 1 if v else 0)
    if t in (0, 1):
        return struct.pack("<B" if t == 0 else "<b", v)
    return struct.pack(FMT[t], v)


def encode_kv(key, tv):
    t, v = tv
    out = struct.pack("<Q", len(key.encode())) + key.encode() + struct.pack("<I", t)
    if t == 9:
        at, elems = v
        out += struct.pack("<I", at) + struct.pack("<Q", len(elems))
        for e in elems:
            out += encode_typed(at, e)
    else:
        out += encode_typed(t, v)
    return out


def parse(path):
    f = open(path, "rb")

    def rd_str():
        n = struct.unpack("<Q", f.read(8))[0]
        return f.read(n).decode("utf8")

    def read_typed(t):
        if t == 8:
            return rd_str()
        if t in (0, 1, 7):
            b = f.read(1)[0]
            return b if t != 7 else bool(b)
        if t == 9:
            at = struct.unpack("<I", f.read(4))[0]
            n = struct.unpack("<Q", f.read(8))[0]
            return (at, [read_typed(at) for _ in range(n)])
        return struct.unpack(FMT[t], f.read(SZ[t]))[0]

    assert f.read(4) == b"GGUF"
    ver = struct.unpack("<I", f.read(4))[0]
    nt = struct.unpack("<Q", f.read(8))[0]
    nkv = struct.unpack("<Q", f.read(8))[0]
    meta = []
    for _ in range(nkv):
        k = rd_str()
        t = struct.unpack("<I", f.read(4))[0]
        meta.append((k, (t, read_typed(t))))
    infos = []
    for _ in range(nt):
        name = rd_str()
        nd = struct.unpack("<I", f.read(4))[0]
        dims = [struct.unpack("<Q", f.read(8))[0] for _ in range(nd)]
        dt = struct.unpack("<I", f.read(4))[0]
        off = struct.unpack("<Q", f.read(8))[0]
        infos.append((name, dims, dt, off))
    hdr_end = f.tell()
    align = next((v[1][1] for v in meta if v[0] == "general.alignment"), 32)
    data_start = (hdr_end + align - 1) // align * align
    f.seek(data_start)
    data = f.read()
    f.close()
    return ver, meta, infos, align, hdr_end, data_start, data


def write(dst, ver, meta, infos, align, tensor_data):
    out = bytearray(b"GGUF" + struct.pack("<I", ver))
    out += struct.pack("<Q", len(infos)) + struct.pack("<Q", len(meta))
    for k, tv in meta:
        out += encode_kv(k, tv)
    for name, dims, dt, off in infos:
        nb = name.encode()
        out += struct.pack("<Q", len(nb)) + nb + struct.pack("<I", len(dims))
        for d in dims:
            out += struct.pack("<Q", d)
        out += struct.pack("<I", dt) + struct.pack("<Q", off)
    out += b"\x00" * ((-len(out)) % align)
    with open(dst, "wb") as fd:
        fd.write(out)
        fd.write(tensor_data)


def main():
    args = sys.argv[1:]
    if args[:1] != ["--roundtrip"] or len(args) != 3:
        sys.exit(__doc__)
    src, dst = args[1], args[2]
    ver, meta, infos, align, hdr_end, data_start, data = parse(src)
    write(dst, ver, meta, infos, align, data)
    h1 = hashlib.sha256(open(src, "rb").read()).hexdigest()
    h2 = hashlib.sha256(open(dst, "rb").read()).hexdigest()
    ok = h1 == h2
    print(json.dumps(dict(src=src, dst=dst, kvs=len(meta), tensors=len(infos),
                          hdr_end=hdr_end, data_start=data_start,
                          data_bytes=len(data), identical=ok,
                          sha256_16=h1[:16])))
    sys.exit(0 if ok else 1)


main()
