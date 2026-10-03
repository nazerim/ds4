#!/usr/bin/env python3
"""Parse `ds4: spec pos ...` trace lines from the server log.

Line format (ds4.c qwen4_spec_cycle trace):
  ds4: spec pos <u> token <d> draft <d> (accept|reject)[ (+accept2|+reject2)]

Cycles with a +suffix ran at verify depth 3 (two chained nextn drafts);
without one, depth 2 (single draft).  Marginal per-position acceptance:
  P(a1) overall / by depth, P(a2 | a1 & deep), mean tokens per cycle.
"""
import re
import sys

PAT = re.compile(
    r"ds4: spec pos (\d+) token (-?\d+) draft (-?\d+) (accept|reject)"
    r"(?: \+(accept2|reject2))?")


def main(path: str) -> None:
    total = deep = 0
    a1 = a1_deep = a1_shallow = 0
    deep_a2 = deep_a1 = 0  # accept2 counts (only meaningful when a1)
    for line in open(path, errors="replace"):
        m = PAT.search(line)
        if not m:
            continue
        total += 1
        acc = m.group(4) == "accept"
        if m.group(5) is not None:
            deep += 1
            if acc:
                a1_deep += 1
                deep_a1 += 1
                if m.group(5) == "accept2":
                    deep_a2 += 1
        elif acc:
            a1_shallow += 1
        a1 += acc
    if total == 0:
        print("no spec trace lines found")
        return
    print(f"cycles total={total} deep(T=3)={deep} shallow(T=2)={total - deep}")
    print(f"P(a1) overall   = {a1}/{total} = {100.0 * a1 / total:.1f}%")
    if deep:
        print(f"P(a1 | deep)    = {a1_deep}/{deep} = {100.0 * a1_deep / deep:.1f}%")
    if total - deep:
        print(f"P(a1 | shallow) = {a1_shallow}/{total - deep} = "
              f"{100.0 * a1_shallow / (total - deep):.1f}%")
    if deep_a1:
        print(f"P(a2 | a1,deep) = {deep_a2}/{deep_a1} = "
              f"{100.0 * deep_a2 / deep_a1:.1f}%   <- position-2 marginal")
    # mean committed tokens per cycle (greedy rows): 1 + P(a1) + P(a1&a2)
    tok = total + a1 + deep_a2
    print(f"mean tokens/cycle = {tok / total:.3f} "
          f"(= 1 + {a1 / total:.3f} + {deep_a2 / total:.3f})")
    print(f"implied speedup ceiling vs serial = {tok / total:.3f}x "
          f"before verify-cost discount")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "log/ds4-qwen.log")
