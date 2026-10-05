#!/usr/bin/env python3
"""Census of Qwen4 projection weight types straight from the GGUF header.

Read-only, no GPU, coexists with a running engine (HANDOVER-20261004 step 1:
which arm every prefill projection takes at chunk=8192 — the dense-mm gates
are (type, n_tok) dependent, so the types are the input to that audit).

usage: gguf_arm_census.py <model.gguf>
"""
import struct
import sys
from collections import defaultdict

DTYPES = {0: "f32", 1: "f16", 2: "q4_0", 3: "q4_1", 6: "q5_0", 7: "q5_1",
          8: "q8_0", 9: "q8_1", 10: "q2_k", 11: "q3_k", 12: "q4_k",
          13: "q5_k", 14: "q6_k", 15: "q8_k", 16: "iq2_xxs", 24: "i8",
          26: "i32", 30: "bf16", 39: "mxfp4"}


def rd_str(f):
    n = struct.unpack("<Q", f.read(8))[0]
    return f.read(n).decode("utf-8", "replace")


ARR_SZ = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}


def skip_val(f, t):
    if t == 8:  # string
        rd_str(f)
    elif t == 9:  # array: elem type u32, count u64, elements
        et = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        if et == 8:
            for _ in range(n):
                rd_str(f)
        elif et == 9:
            for _ in range(n):
                skip_val(f, 9)
        else:
            sz = ARR_SZ[et]
            f.read(sz * n)
    elif t in ARR_SZ:
        f.read(ARR_SZ[t])
    else:
        raise ValueError(f"unknown gguf value type {t}")


def main(path):
    tensors = []
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            sys.exit("not a GGUF")
        ver = struct.unpack("<I", f.read(4))[0]
        n_tensors = struct.unpack("<Q", f.read(8))[0]
        n_kv = struct.unpack("<Q", f.read(8))[0]
        for _ in range(n_kv):
            k = rd_str(f)
            t = struct.unpack("<I", f.read(4))[0]
            skip_val(f, t)
        for _ in range(n_tensors):
            name = rd_str(f)
            nd = struct.unpack("<I", f.read(4))[0]
            dims = [struct.unpack("<Q", f.read(8))[0] for _ in range(nd)]
            dt = struct.unpack("<I", f.read(4))[0]
            struct.unpack("<Q", f.read(8))[0]  # offset
            tensors.append((name, dims, dt))

    def bucket(name):
        n = name
        for s in (".layers.", "blk."):
            if s in n:
                n = n.split(s, 1)[1]
                n = n[n.find(".") + 1:] if "." in n else n
                break
        for p in ("attn_q_proj", "attn_k_proj", "attn_v_proj", "attn_output",
                  "indexer_q", "indexer_k", "ple_key", "ple_value", "ple_norm",
                  "linear_attn", "conv1d", "attn_gate", "ffn_gate_inp", "ffn_exp",
                  "ffn_shared", "ffn_gate", "ffn_up", "ffn_down", "hc_", "hyper",
                  "nextn", "eh_proj", "output"):
            if p in n:
                return p
        return n if n.count(".") < 3 else "other"

    groups = defaultdict(lambda: defaultdict(list))
    for name, dims, dt in tensors:
        b = bucket(name)
        dtn = DTYPES.get(dt, str(dt))
        groups[b][dtn].append((name, dims))

    print(f"{path}  gguf v{ver}  tensors={n_tensors}")
    for b in sorted(groups):
        for dtn, items in sorted(groups[b].items()):
            dims = items[0][1]
            print(f"  {b:16s} {dtn:6s} x{len(items):4d}  shape={dims}  eg {items[0][0][:64]}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "gguf/Qwen3.8-Flash-Next-Q4.gguf")
