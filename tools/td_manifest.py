#!/usr/bin/env python3
"""Build MANIFEST.json for a harvested DS4_TRAIN_DUMP corpus.

Usage: tools/td_manifest.py <corpus-dir> [--note "engine info"]...
Groups records by idx.jsonl flush batches -> conversations (conv ids),
computes per-conv integrity (chain %, labels[0]==seed, pos range) and the
authoritative per-conv DROP list. The drop rule is exactly one predicate,
shared with tools/td_classify.py: n_label == 0 (records without any resolved
lookahead: true frontier ends, and v1.2-era adoption-bypass tails which the
avail=0 guard self-quarantined). NOTE: v1.2-era final flushes could leave
records with valid labels but OPEN streak (255); those are KEPT — an unknown
cycle-gap is not a training defect.
Writes <corpus-dir>/MANIFEST.json next to the data; prints a summary.
"""
import glob
import json
import os
import struct
import sys
import time

REC = struct.Struct("<IiBBBB5i")
SZ = REC.size + 12288


def conv_batches(ipath):
    cur = 0
    out = []  # (conv, start, end)
    for line in open(ipath):
        d = json.loads(line)
        out.append((d["conv"], cur, cur + d["flushed"]))
        cur += d["flushed"]
    return out


def main():
    d = sys.argv[1]
    notes = []
    if "--note" in sys.argv:
        notes = [sys.argv[sys.argv.index("--note") + 1]]
    manifest = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema": {"record_bytes": SZ, "header": "<IiBBBB5i",
                   "feat": "fp8 e4m3, 3x4096 taps, row-major layers",
                   "labels": "int32[5] lookahead tokens (-1 unused)"},
        "engine": "DeepSeek-V4-Flash-Vision-Exp + DSpark support gguf, "
                  "confidence 0.45, scheduler window 12, model_fp "
                  "11038508403456853112",
        "notes": notes,
        "files": [],
    }
    tot_recs = tot_drop = 0
    for rpath in sorted(glob.glob(os.path.join(d, "train_pid*.recs"))):
        stem = os.path.basename(rpath)[:-5]
        ipath = rpath[:-5] + ".idx.jsonl"
        data = open(rpath, "rb").read()
        n = len(data) // SZ
        rows = [REC.unpack_from(data, i * SZ) for i in range(n)]
        batches = conv_batches(ipath) if os.path.exists(ipath) else []
        spans = {}
        for c, a, b in batches:
            spans.setdefault(c, []).append((a, b))
        convs = {}
        drops = []
        for c in sorted(spans):
            cspans = spans[c]
            recs = sum(b - a for a, b in cspans)
            posmin = min(rows[a][0] for a, b in cspans)
            posmax = max(rows[b - 1][0] for a, b in cspans)
            # per-conv verification across its batch spans
            bad_seed = chain_tot = chain_hit = 0
            idxs = [i for a, b in cspans for i in range(a, b)]
            for i in idxs:
                if rows[i][3] and rows[i][6] != rows[i][1]:
                    bad_seed += 1
            for k in range(len(idxs) - 1):
                i, j = idxs[k], idxs[k + 1]
                if j != i + 1:  # gap between this conv's batches
                    continue
                g = rows[j][0] - rows[i][0]
                if 1 <= g <= 5 and rows[i][3] > g:
                    chain_tot += 1
                    chain_hit += (rows[i][6 + g] == rows[j][1])
            # untrusted records: n_label==0 (v1.2-era adoption bypass left
            # these label-less via the avail=0 guard — self-quarantined, no
            # wrong-lineage labels possible). In-batch breaks are recorded
            # as provenance only; their records always have n_label==0.
            drop = [i for i in idxs if rows[i][3] == 0]
            breaks = []
            for a2, b2 in cspans:
                breaks += [i for i in range(a2, b2 - 1)
                           if rows[i + 1][0] <= rows[i][0]]
            drops.extend(drop)
            convs[c] = {"recs": recs, "batches": len(cspans),
                        "pos_min": posmin, "pos_max": posmax,
                        "chain": f"{chain_hit}/{chain_tot}",
                        "labels0_eq_seed": bad_seed == 0,
                        "dropped": len(drop),
                        "lineage_breaks": breaks}
        tot_recs += n
        tot_drop += len(drops)
        manifest["files"].append({
            "stem": stem, "records": n, "bytes": len(data),
            "convs": convs, "drop_indices": drops,
        })
    manifest["totals"] = {"records": tot_recs, "drop": tot_drop,
                          "keep_pct": round(100 * (1 - tot_drop / max(tot_recs, 1)), 2)}
    with open(os.path.join(d, "MANIFEST.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"MANIFEST: {tot_recs} records, {tot_drop} dropped "
          f"({manifest['totals']['keep_pct']}% kept) across "
          f"{len(manifest['files'])} pid file(s)")
    for fl in manifest["files"]:
        sizes = sorted((v["recs"] for v in fl["convs"].values()), reverse=True)[:6]
        print(f"  {fl['stem']}: convs={len(fl['convs'])} top sizes={sizes}")


main()
