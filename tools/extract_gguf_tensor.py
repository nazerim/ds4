#!/usr/bin/env python3
"""Extract one GGUF tensor's raw bytes / dequantized rows from a .gguf.

Standard GGUF v3 header walking with the CORRECT scalar size table
(0=u8 1=i8 2=u16 3=i16 4=u32 5=i32 6=f32 7=bool; 8=string len+bytes;
9=array etype+count; 10=u64 11=i64 12=f64). A wrong table desyncs the
walker into the multi-GB data section (learned Nov-03: MemoryError).

Usage:
  python3 tools/extract_gguf_tensor.py MODEL.gguf --list [filter]
  python3 tools/extract_gguf_tensor.py MODEL.gguf --name token_embd.weight \
      --out /path/out.npz [--rows 0-1000|comma] [--slab name]
f32/f16 tensors: --rows extracts selected rows (per-row contiguous,
row-major as GGUF stores [d0,d1] with d0 fastest... for embedding tables
GGUF dims are [n_embd, vocab] and data is vocab rows of n_embd).
Quantized types (q8_0/q2_k/iq2_xxs...) print layout info only; dequant
goes through trainer/dspark/gguf_dq.py (bit-exact ports, golden-tested).
"""
import argparse, json, os, struct, sys

# ENGINE enum per ds4.c:2323 gguf_types[] (NOT ggml-standard order!)
DT_NAMES = {0:"f32",1:"f16",2:"q4_0",3:"q4_1",6:"q5_0",7:"q5_1",8:"q8_0",
            9:"q8_1",10:"q2_k",11:"q3_k",12:"q4_k",13:"q5_k",14:"q6_k",
            15:"q8_k",16:"iq2_xxs",17:"iq2_xs",18:"iq3_xxs",19:"iq1_s",
            20:"iq4_nl",21:"iq3_s",22:"iq2_s",23:"iq4_xs"}
SCALAR = {0:4,1:2,2:1,3:1,4:1,5:1,6:1,7:1,10:8,11:8,12:8}  # code->bytes? NO:
# canonical gguf metadata types: uint8=0,int8=1,uint16=2,int16=3,uint32=4,
# int32=5,float32=6,bool=7,string=8,array=9,uint64=10,int64=11,float64=12
SS = {0:1,1:1,2:2,3:2,4:4,5:4,6:4,7:1,10:8,11:8,12:8}
DT_BYTES = {0:4,1:2,13:2,17:2,9:1,10:2,11:4,12:8}  # tensor element bytes

class R:
    def __init__(self, f): self.f = f
    def raw(self, n):
        d = self.f.read(n)
        if len(d) != n: raise EOFError(f"short read {n}")
        return d
    def u32(self): return struct.unpack("<I", self.raw(4))[0]
    def u64(self): return struct.unpack("<Q", self.raw(8))[0]
    def s(self):
        n = self.u64(); return self.raw(n).decode("utf8", "replace")

def skip_kv(r, t):
    if t == 8:
        n = r.u64(); r.raw(n)
    elif t == 9:
        et = r.u32(); n = r.u64()
        if n > 50_000_000: raise RuntimeError(f"huge array {n}")
        if et == 8:
            for _ in range(n):
                m = r.u64(); r.raw(m)
        else:
            for _ in range(n): skip_kv(r, et)
    else:
        r.raw(SS[t])

def walk(path):
    f = open(path, "rb"); r = R(f)
    if r.raw(4) != b"GGUF": sys.exit("not GGUF")
    ver = r.u32(); n_t = r.u64(); n_kv = r.u64()
    for _ in range(n_kv):
        r.s(); t = r.u32(); skip_kv(r, t)
    infos = []
    for _ in range(n_t):
        name = r.s(); nd = r.u32()
        dims = [r.u64() for _ in range(nd)]
        dt = r.u32(); off = r.u64()
        infos.append((name, dims, dt, off))
    header_end = f.tell()          # after kvs + tensor infos
    align = 32                     # re-read kv once for general.alignment
    f.seek(0); R2 = R(f); R2.raw(4); R2.u32(); R2.u64(); nk = R2.u64()
    for _ in range(nk):
        k = R2.s(); t = R2.u32()
        if k == "general.alignment" and t == 4:
            align = R2.u32()
        else:
            skip_kv(R2, t)
    f.seek(header_end)
    data_start = header_end + (-header_end % align)
    return f, infos, data_start, DT_BYTES

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gguf"); ap.add_argument("--list", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--rows", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--slab", default="weight")
    a = ap.parse_args()
    f, infos, data_start, DT_BYTES = walk(a.gguf)
    if a.list is not None:
        for name, dims, dt, off in infos:
            if a.list.lower() in name.lower():
                print(f"{name}  {DT_NAMES.get(dt,dt)}  {dims}  off={off}")
        return
    if not a.name:
        sys.exit("--list or --name required")
    ti = [t for t in infos if t[0] == a.name]
    if not ti: sys.exit(f"tensor {a.name} not found")
    name, dims, dt, off = ti[0]
    dname = DT_NAMES.get(dt, "?")
    print(f"{name}: {dname} dims={dims} data_start={data_start}+{off}")
    if dname not in ("f32", "f16", "bf16"):
        print("quantized type -> use trainer gguf_dq dequant path for rows")
        return
    eb = DT_BYTES[dt]
    n0, n1 = dims[0], dims[1]        # e.g. [4096, 129280] for token_embd
    row_len, n_rows = n0, n1         # rows are contiguous n0 elements
    import numpy as np
    dtmap = {"f32": np.float32, "f16": np.float16, "bf16": None}
    if dname not in dtmap:
        sys.exit("unexpected dtype")
    if dtmap[dname] is None:
        sys.exit("bf16: convert manually")
    if a.rows:
        rows = []
        for part in a.rows.split(","):
            if "-" in part:
                lo, hi = part.split("-"); rows += list(range(int(lo), int(hi)+1))
            else:
                rows.append(int(part))
    else:
        rows = list(range(n_rows))
    out = np.empty((len(rows), row_len), dtype=dtmap[dname])
    for i, rv in enumerate(rows):
        f.seek(data_start + off + rv * row_len * eb)
        out[i] = np.frombuffer(f.read(row_len * eb), dtype=dtmap[dname])
    np.savez(a.out or f"/tmp/{name.replace('.','_')}.npz",
             **{a.slab: out})
    print(f"wrote {len(rows)} rows x {row_len} {dname}")

if __name__ == "__main__":
    main()
