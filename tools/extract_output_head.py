#!/usr/bin/env python3
"""Dequantize base-GGUF output.weight (q8_0) -> f16 npz for the mirror.

spec_logits = rms(final->norm) @ base_weights->output (ds4.c ~36325 path);
the support GGUF has no head, so milestone-2 proposal replay needs this
materialization (gate B; gate A conf-rows do not). q8_0 block = f16 scale +
32 int8, row = 128 blocks for n_embd=4096 (GGUF dims [4096, vocab], vocab
rows contiguous of 4096, same layout as token_embd_f16.npz).

Usage: python3 tools/extract_output_head.py MODEL.gguf --out FILE.npz
                                          [--rows A-B] [--check]
"""
import argparse
import importlib.util
import os
import struct
import sys

import numpy as np


def load_walker():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "extract_gguf_tensor.py")
    spec = importlib.util.spec_from_file_location("xgt", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def q8_0_row_dequant(buf):
    # buf: n/32 blocks * 34 bytes -> float32[n]
    n = (len(buf) // 34) * 32
    view = np.frombuffer(buf, dtype=np.uint8).reshape(-1, 34)
    d = (view[:, 0:2].copy().view(np.float16).astype(np.float32))
    q = view[:, 2:34].astype(np.int8).astype(np.float32)
    return (q * d).reshape(-1)[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gguf")
    ap.add_argument("--out", required=True)
    ap.add_argument("--rows", default=None, help="e.g. 0-129279 (default all)")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    xgt = load_walker()
    f, infos, data_start, _ = xgt.walk(a.gguf)
    ti = [t for t in infos if t[0] == "output.weight"]
    if not ti:
        sys.exit("output.weight not found")
    name, dims, dt, off = ti[0]
    if dt != 8:
        sys.exit(f"expected q8_0(8), got {dt}")
    n0, n1 = dims[0], dims[1]
    row_bytes = (n0 // 32) * 34
    if n0 % 32:
        sys.exit("n_embd not q8_0-aligned")
    lo, hi = 0, n1 - 1
    if a.rows:
        s, e = a.rows.split("-")
        lo, hi = int(s), int(e)
    print(f"output.weight q8_0 dims={dims} row_bytes={row_bytes} "
          f"rows={lo}..{hi} -> {a.out}")
    n_rows = hi - lo + 1
    out = np.empty((n_rows, n0), dtype=np.float16)
    rb = np.frombuffer
    for i, r in enumerate(range(lo, hi + 1)):
        f.seek(data_start + off + r * row_bytes)
        out[i] = q8_0_row_dequant(f.read(row_bytes)).astype(np.float16)
        if i and i % 8192 == 0:
            print(f"  {i}/{n_rows} rows", flush=True)
    np.savez(a.out, weight=out)
    print(f"wrote {n_rows} rows: mean={out.mean():.6f} "
          f"std={out.std():.6f} absmax={np.abs(out).max():.4f}")
    if a.check:
        # round-trip: requant row lo and compare dequant stability (q8_0 is
        # exact on stored scales; this catches offset/layout drift)
        f.seek(data_start + off + lo * row_bytes)
        b1 = rb(f.read(row_bytes), np.uint8)
        f.seek(data_start + off + lo * row_bytes)
        b2 = rb(f.read(row_bytes), np.uint8)
        print("reread stable:", bool((b1 == b2).all()))
        w0 = q8_0_row_dequant(b1.tobytes())
        print("deterministic dequant:",
              bool(np.array_equal(w0.astype(np.float16), out[0])))


if __name__ == "__main__":
    main()
