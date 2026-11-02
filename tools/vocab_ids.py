#!/usr/bin/env python3
"""Resolve special-token IDs from a GGUF tokenizer by regex substrings.

Usage: tools/vocab_ids.py <model.gguf> [regex ...]
Prints "<id> <token>" for every vocab entry matching any regex
(defaults: DSML / think / im_ / tool).
"""
import re
import struct
import sys

path = sys.argv[1]
pats = [re.compile(p) for p in sys.argv[2:]] or \
    [re.compile("DSML|think|im_|tool")]

f = open(path, "rb")


def rd_str():
    n = struct.unpack("<Q", f.read(8))[0]
    return f.read(n).decode("utf8", "replace")


def read_val(t):
    # DS4 GGUF dialect (ds4.c:2300): 0 u8 1 i8 2 u16 3 i16 4 u32 5 i32
    # 6 f32 7 bool 8 STRING 9 ARRAY 10 u64 11 i64 12 f64
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
        if n > 10_000_000:
            sys.exit(f"implausible array of {n} type {at} — desynced")
        return [read_val(at) for _ in range(n)]
    sys.exit(f"unknown gguf type {t}")


assert f.read(4) == b"GGUF"
f.read(4)
f.read(8)
nkv = struct.unpack("<Q", f.read(8))[0]

tokens = None
for _ in range(nkv):
    k = rd_str()
    t = struct.unpack("<I", f.read(4))[0]
    v = read_val(t)
    if k == "tokenizer.ggml.tokens":
        tokens = v
        break
f.close()
assert tokens, "no tokenizer.ggml.tokens found"
for i, tok in enumerate(tokens):
    if any(p.search(tok) for p in pats):
        print(i, repr(tok))
