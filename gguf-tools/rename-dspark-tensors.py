#!/usr/bin/env python3
"""Rewrite DSpark drafter tensor names from the HF 'dspark.' convention to
ds4's expected 'mtp.<stage>.' convention, so ds4 can detect/bind the model.

Usage: rename-dspark-tensors.py INPUT.gguf [OUTPUT.gguf]
Writes OUTPUT (default <input>-ds4.gguf); leaves INPUT unchanged.
"""
import struct
import sys
import os
import tempfile

GGUF_MAGIC = b'GGUF'
SCALAR_BYTES = {0:1, 1:1, 2:2, 3:2, 4:4, 5:4, 6:4, 7:1, 10:8, 11:8, 12:8}

def read_str(f):
    ln = struct.unpack("<Q", f.read(8))[0]
    return f.read(ln)

def write_str(f, b):
    f.write(struct.pack("<Q", len(b)))
    f.write(b)

def read_kv_value(f, kt):
    if kt in SCALAR_BYTES:
        return ("scalar", f.read(SCALAR_BYTES[kt]))
    if kt == 8:   # string
        ln = struct.unpack("<Q", f.read(8))[0]
        return ("string", f.read(ln))
    if kt == 9:   # array
        it = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        if it in SCALAR_BYTES:
            return ("array", it, [f.read(SCALAR_BYTES[it]) for _ in range(n)])
        if it == 8:
            return ("array", it, [read_str(f) for _ in range(n)])
        raise SystemExit(f"unsupported array item type {it}")
    raise SystemExit(f"unsupported kv type {kt}")

def parse_header(f):
    magic = f.read(4)
    if magic != GGUF_MAGIC:
        raise SystemExit("not a GGUF file")
    ver = struct.unpack("<I", f.read(4))[0]
    n_tensors = struct.unpack("<Q", f.read(8))[0]
    n_kv = struct.unpack("<Q", f.read(8))[0]
    kvs = []
    for _ in range(n_kv):
        key = read_str(f)
        kt = struct.unpack("<I", f.read(4))[0]
        val = read_kv_value(f, kt)
        kvs.append((key, kt, val))
    tensors = []
    for _ in range(n_tensors):
        name = read_str(f)
        ndim = struct.unpack("<I", f.read(4))[0]
        dims = struct.unpack("<" + "Q"*ndim, f.read(8*ndim))
        ttype = struct.unpack("<I", f.read(4))[0]
        rel = struct.unpack("<Q", f.read(8))[0]
        tensors.append((name, ndim, dims, ttype, rel))
    tensor_data_pos = f.tell()
    return ver, kvs, tensors, tensor_data_pos

def write_header(f, kvs, tensors):
    f.write(GGUF_MAGIC)
    f.write(struct.pack("<I", 3))
    f.write(struct.pack("<Q", len(tensors)))
    f.write(struct.pack("<Q", len(kvs)))
    for key, kt, val in kvs:
        write_str(f, key)
        f.write(struct.pack("<I", kt))
        if val[0] == "scalar":
            f.write(val[1])
        elif val[0] == "string":
            write_str(f, val[1])
        elif val[0] == "array":
            _, it, items = val
            f.write(struct.pack("<I", it))
            f.write(struct.pack("<Q", len(items)))
            if it in SCALAR_BYTES:
                for item in items:
                    f.write(item)
            else:
                for item in items:
                    write_str(f, item)
    for (name, ndim, dims, ttype, rel) in tensors:
        write_str(f, name)
        f.write(struct.pack("<I", ndim))
        f.write(struct.pack("<" + "Q"*ndim, *dims))
        f.write(struct.pack("<I", ttype))
        f.write(struct.pack("<Q", rel))

def rewrite_name(name, final):
    s = name.decode('utf8', 'replace')
    parts = s.split('.')
    if len(parts) >= 3 and parts[0] == 'dspark' and parts[1].isdigit():
        return f"mtp.{parts[1]}.{'.'.join(parts[2:])}".encode()
    mapping = {
        'dspark.main_proj.weight':      f'mtp.0.main_proj.weight',
        'dspark.main_norm.weight':      f'mtp.0.main_norm.weight',
        'dspark.norm.weight':           f'mtp.{final}.norm.weight',
        'dspark.hc_head_fn.weight':     f'mtp.{final}.hc_head_fn.weight',
        'dspark.hc_head_base.weight':   f'mtp.{final}.hc_head_base.weight',
        'dspark.hc_head_scale.weight':  f'mtp.{final}.hc_head_scale.weight',
        'dspark.markov_w1.weight':      f'mtp.{final}.markov_head.markov_w1.weight',
        'dspark.markov_w2.weight':      f'mtp.{final}.markov_head.markov_w2.weight',
        'dspark.confidence_head.weight': f'mtp.{final}.confidence_head.proj.weight',
    }
    new = mapping.get(s)
    if new is None:
        raise SystemExit(f"unhandled dspark tensor name: {s}")
    return new.encode()

def align_up(n, a):
    return ((n + a - 1) // a) * a

def default_output(inp):
    # Replace only a trailing '.gguf' suffix (str.replace would rewrite every
    # occurrence, e.g. 'a.gguf.gguf' -> 'a-ds4.gguf-ds4.gguf').
    if inp.endswith('.gguf'):
        return inp[:-len('.gguf')] + '-ds4.gguf'
    return inp + '-ds4.gguf'

def main():
    if len(sys.argv) < 2:
        print(__doc__); return 1
    inp = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else default_output(inp)
    if os.path.abspath(out) == os.path.abspath(inp):
        raise SystemExit("refusing to overwrite the input file; pass a distinct OUTPUT")

    with open(inp, 'rb') as f:
        ver, kvs, tensors, old_pos = parse_header(f)
    if ver != 3:
        raise SystemExit(f"unsupported GGUF version {ver} (only v3 is supported)")

    # ds4 aligns tensor data to general.alignment (KV) or 32.
    alignment = 32
    for (key, kt, val) in kvs:
        if key == b'general.alignment' and kt == 4:
            alignment = struct.unpack("<I", val[1])[0]
    old_tdp = align_up(old_pos, alignment)

    max_stage = 0
    for (name, *_) in tensors:
        s = name.decode('utf8', 'replace').split('.')
        if s[0] == 'dspark' and len(s) > 1 and s[1].isdigit():
            max_stage = max(max_stage, int(s[1]))
    final = max_stage

    new_tensors = []
    for (name, ndim, dims, ttype, rel) in tensors:
        s = name.decode('utf8', 'replace')
        new_name = rewrite_name(name, final) if s.startswith('dspark.') else name
        new_tensors.append((new_name, ndim, dims, ttype, rel))

    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile('wb', dir=os.path.dirname(out) or '.', delete=False) as tmp:
            tmp_name = tmp.name
            write_header(tmp, kvs, new_tensors)
            new_pos = tmp.tell()
            new_tdp = align_up(new_pos, alignment)
            # pad to the aligned tensor data start
            tmp.write(b'\0' * (new_tdp - new_pos))
            delta = new_tdp - old_tdp
            # rel offsets are relative to the (aligned) tensor-data start and we
            # copy the payload from old_tdp to the same relative layout, so they
            # are unchanged.
            # append tensor payload unchanged (skip the old padding bytes)
            with open(inp, 'rb') as src:
                src.seek(old_tdp)
                while True:
                    chunk = src.read(1 << 24)
                    if not chunk:
                        break
                    tmp.write(chunk)
            tmp.flush()
        os.replace(tmp_name, out)
        tmp_name = None  # moved into place; nothing left to clean up
    finally:
        if tmp_name is not None and os.path.exists(tmp_name):
            os.unlink(tmp_name)
    print(f"wrote {out}: {len(new_tensors)} tensors, {final+1} stages, header delta {delta} bytes")
    return 0

if __name__ == '__main__':
    sys.exit(main())
