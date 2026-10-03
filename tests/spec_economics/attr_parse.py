#!/usr/bin/env python3
"""Attribution parser: average the DS4_QWEN4_TIMING=2 per-stage ms/chunk
lines over T=2 verify chunks at pos >= MIN_POS, per log file, and print the
stage deltas across configs.

Usage: attr_parse.py <min_pos> <label>=<path> [<label>=<path> ...]
Stage groups: ple hc_attn gdn attn hc_ffn moe (ds4.c qwen4_graph_forward_tokens).
"""
import re
import sys
from collections import defaultdict

LINE = re.compile(
    r"prefill stage ms/chunk \(pos=(\d+) T=(\d+) ok=\d+\): "
    r"ple ([\d.]+) hc_attn ([\d.]+) gdn ([\d.]+) attn ([\d.]+) hc_ffn ([\d.]+) moe ([\d.]+)"
)
STAGES = ["ple", "hc_attn", "gdn", "attn", "hc_ffn", "moe"]


def main() -> int:
    min_pos = int(sys.argv[1])
    cols = {}
    for arg in sys.argv[2:]:
        label, path = arg.split("=", 1)
        acc = defaultdict(list)
        with open(path, errors="replace") as f:
            for m in LINE.finditer(f.read()):
                pos, T = int(m.group(1)), int(m.group(2))
                if T != 2 or pos < min_pos:
                    continue
                for name, val in zip(STAGES, m.groups()[2:]):
                    acc[name].append(float(val))
        cols[label] = {s: (sum(v) / len(v) if v else 0.0, len(v))
                       for s, v in acc.items()}
    labels = list(cols)
    if not labels:
        print("no data")
        return 1
    n = cols[labels[0]][STAGES[0]][1]
    print(f"config           " + " ".join(f"{s:>8}" for s in STAGES) + f" {'total':>8}")
    means = {}
    for label in labels:
        row = [cols[label][s][0] for s in STAGES]
        means[label] = row
        print(f"{label:<15} " + " ".join(f"{v:8.2f}" for v in row) + f" {sum(row):8.2f}")
    if len(labels) >= 2:
        base = labels[0]
        for label in labels[1:]:
            d = [m - b for m, b in zip(means[label], means[base])]
            print(f"delta {base}->{label:<8} " + " ".join(f"{v:+8.2f}" for v in d) + f" {sum(d):+8.2f}")
    print(f"n_chunks={n} (pos>={min_pos}, T=2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
