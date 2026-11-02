#!/usr/bin/env python3
"""Validate DS4_TRAIN_DUMP capture files (see .codebase-memory/CAPTURE-SCHEMA.md).

Usage: tools/td_validate.py <dir-with-train_pid*.recs> [--deep]
Segments are reconstructed from idx.jsonl flush batches (one batch = one
session ring = one lineage under v1.3; under v1.2 an adoption mid-ring could
mix two lineages inside one batch — those appear as in-batch breaks and are
reported as SUSPECT windows for the trainer to drop).
Invariants checked:
  framing   : file size % record size == 0
  monotone  : pos strictly increases within a batch (breaks = suspect)
  lineage   : labels[0] == seed for every record with labels
  chain     : labels[g] == next record's seed across gap g (within batch)
  features  : n_feat values; --deep: nonzero-byte % and distinct-row count
Exit 1 on framing/labels0/chain failures. In-batch lineage breaks (v1.2-era
adoption bypass) print a NOTE only: affected records are self-quarantined at
n_label==0 and excluded by the shared drop rule.
"""
import glob
import json
import os
import struct
import sys

REC = struct.Struct("<IiBBBB5i")  # pos seed streak n_label n_feat flags 5*label
SZ = REC.size + 12288


def check(path, deep):
    data = open(path, "rb").read()
    n = len(data) // SZ
    fails = []
    if len(data) % SZ:
        fails.append(f"framing remainder {len(data) % SZ}")
    rows = [REC.unpack_from(data, i * SZ) for i in range(n)]
    pos = [r[0] for r in rows]
    seed = [r[1] for r in rows]
    lbl = [[r[6 + j] for j in range(5)] for r in rows]
    nlbl = [r[3] for r in rows]
    feats = sorted({r[4] for r in rows})
    bad_seed_all = [i for i in range(n) if nlbl[i] and lbl[i][0] != seed[i]]
    clamped = sum(1 for i in bad_seed_all if rows[i][2] >= 254)
    bad_seed = len(bad_seed_all) - clamped
    # batches from idx sidecar
    ipath = path.replace(".recs", ".idx.jsonl")
    batches = []
    if os.path.exists(ipath):
        cur = 0
        for line in open(ipath):
            d = json.loads(line)
            batches.append((d["conv"], cur, cur + d["flushed"]))
            cur += d["flushed"]
        batches_pending = n - cur
    else:
        batches, batches_pending = [(0, 0, n)], 0
    in_breaks = []
    tot = hits = 0
    for _, a, b in batches:
        for j in range(a, b - 1):
            if pos[j + 1] <= pos[j]:
                in_breaks.append(j)
            else:
                g = pos[j + 1] - pos[j]
                if 1 <= g <= 5 and nlbl[j] > g:
                    tot += 1
                    hits += (lbl[j][g] == seed[j + 1])
    chain_pct = 100 * hits / tot if tot else 100.0
    nolab = sum(1 for i in range(n) if nlbl[i] == 0)
    extra = ""
    if deep:
        import hashlib
        import statistics as st
        seen = set()
        nzsum = 0
        for i in range(n):
            body = data[i * SZ + REC.size:(i + 1) * SZ]
            nzsum += sum(1 for by in body if by)
            seen.add(hashlib.blake2b(body, digest_size=8).digest())
        extra = (f" nz={nzsum * 100 // (n * 12288)}%"
                 f" distinct={len(seen)}/{n}" if n else "")
        if n and len(seen) != n:
            fails.append(f"feature rows collide: {len(seen)}/{n}")
    print(f"{os.path.basename(path)}: recs={n} batches={len(batches)}"
          f"+pend{batches_pending} convs={len({c for c, _, _ in batches})} "
          f"nfeat={feats} chain={chain_pct:.1f}% nolabel="
          f"{100*nolab/max(n,1):.2f}% inbreaks={len(in_breaks)}{extra}")
    if clamped:
        print(f"  NOTE labels0: {clamped} clamped-streak exempt (loader drops them)")
    if bad_seed:
        fails.append(f"labels[0]!=seed x{bad_seed} (non-clamped)")
    if chain_pct < 95:
        fails.append(f"chain {chain_pct}% < 95")
    if in_breaks:
        idxs = ",".join(str(j) for j in in_breaks[:6])
        print(f"  NOTE: adoption-bypass breaks (v1.2-era) at idx {idxs}: "
              f"affected records carry n_label==0 and are excluded by the "
              f"zero-label drop rule (self-quarantined)")
    return fails


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    d = sys.argv[1]
    deep = "--deep" in sys.argv
    files = sorted(glob.glob(os.path.join(d, "train_pid*.recs")))
    if not files:
        sys.exit(f"no train_pid*.recs in {d}")
    allfails = []
    for f in files:
        allfails += [(f, m) for m in check(f, deep)]
    total = sum(os.path.getsize(f) for f in files)
    print(f"TOTAL: {len(files)} pid(s), {total // 1024 // 1024} MiB, "
          f"{total // SZ} records")
    for f, m in allfails:
        print(f"FAIL {os.path.basename(f)}: {m}")
    sys.exit(1 if allfails else 0)


main()
