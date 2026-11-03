"""S0 label-semantics probe (3C-NIGHT-PLAN-20261103 slot S0).

Answers 3C-HANDOFF-20261103.md section 6 with data, no trunk, header-only
reads (fast, safe beside the live engine):

Q(a) dlen boundary: does props[dlen-1] hold the LAST ACCEPTED proposal
     (H1: dlen = accept count) or the REJECTING slot (H2: dlen = accept+1)?
     Three independent discriminators:
       A1 pos-gap: next cycle starts at pos + (accepted + 1 bonus token),
          so gap == dlen+1 supports H1, gap == dlen supports H2.
       A2 accepted-prefix: for d < dlen (H1) / d < dlen-1 (H2),
          props[d] must equal the real token labels[d+1].
       A3 dlen==0 presence (only possible under H1) and props[dlen]
          (the would-be reject slot) vs the real token.
Q(b) dlen histogram + per-slot accept rates -> conf BCE label construction.
Q(c) seed-batch-era recs: label chain continuity + seed field sanity ->
     usable for era-agnostic markov next-token labels or not.

Usage: uv run python -m dspark.probe_labels3c --recs R.recs --parity P.bin
       [--tag name] [--out report.json]   (repeat pair by pairing 1:1)
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import struct
from collections import Counter

REC_HDR = struct.Struct("<IiBBBB5i")  # mirror_trunk.mirror_trunk
RECORD_BYTES = 12320
PARITY_STRIDE = 81952
PARITY_HDR = struct.Struct("<IiBBH")
PAR_PROPS = struct.Struct("<5i")


def load_recs(recs_path: str):
    """header-only: list of (pos, seed, labels[list]) in file order."""
    out = []
    with open(recs_path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        n = os.path.getsize(recs_path) // RECORD_BYTES
        for i in range(n):
            r = REC_HDR.unpack_from(mm, i * RECORD_BYTES)
            nl = r[3]
            out.append((int(r[0]), int(r[1]), [int(x) for x in r[6:6 + nl]]))
        mm.close()
    return out


def conv_record_lists(recs_path: str) -> dict[int, list[int]]:
    """same semantics as mirror_trunk.conv_record_lists (idx.jsonl runs)."""
    idx_path = recs_path.replace(".recs", ".idx.jsonl")
    n = os.path.getsize(recs_path) // RECORD_BYTES
    res: dict[int, list[int]] = {}
    cursor = 0
    with open(idx_path) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            cnt = int(r["flushed"])
            res.setdefault(int(r["conv"]), []).extend(
                range(cursor, cursor + cnt))
            cursor += cnt
    if cursor != n:
        raise RuntimeError(f"idx cursor {cursor} != {n}")
    return res


def load_parity(parity_path: str):
    """pos -> [count, (dlen, props)] from one linear scan."""
    pos_map: dict[int, list] = {}
    with open(parity_path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        n = mm.size() // PARITY_STRIDE
        for i in range(n):
            base = i * PARITY_STRIDE
            pos, _seed, dlen, _st, _r = PARITY_HDR.unpack_from(mm, base)
            props = list(PAR_PROPS.unpack_from(mm, base + 12))
            e = pos_map.get(int(pos))
            if e is None:
                pos_map[int(pos)] = [1, (int(dlen), props)]
            else:
                e[0] += 1
                e[1] = (int(dlen), props)
        total = n
        mm.close()
    return pos_map, total


def pct(a: int, b: int) -> float:
    return round(a / b, 4) if b else None


def analyze(recs_path: str, parity_path: str) -> dict:
    recs = load_recs(recs_path)
    convs = conv_record_lists(recs_path)
    pos_map, n_par = load_parity(parity_path)

    # distinct-pos view (histogram shape; dups are rarer same-conv collisions)
    dlen_hist = Counter(v[1][0] for v in pos_map.values())

    seed_eq_l0 = seed_eq_l1 = seed_other = n_lab = 0
    gap_by_dlen: dict[int, Counter] = {}
    joined = 0
    # A2/A3: per d: (matches, checks) for d < dlen, plus last-slot split
    pre_last_ok = pre_last_tot = 0       # d < dlen-1 (both hypotheses agree)
    last_ok = last_tot = 0               # d == dlen-1 (the discriminator)
    reject_ok = reject_tot = 0           # props[dlen] == labels[dlen+1]
    dl1_acc = dl1_tot = 0                # dlen==1: props[0]==labels[1]
    # A4: per-slot agreement between gap-derived accept run (k = gap-1)
    # and the labels chain; gaps > dlen+1 (skip-ambiguous) excluded.
    slot_stats: dict[int, list[int]] = {}  # d -> [gap_acc, lab_acc, n]
    agree = agree_n = skipamb = n_gap = 0
    # A5: labels-chain accept length k_lab vs observed gap (decides chain-stop
    # vs commit-window), + conv-adjacency sanity (interleaving-session damage)
    klab_hist: dict[int, Counter] = {}
    adj_neg = adj_n = 0

    for lst in convs.values():
        for k in range(len(lst)):
            pos, seed, labels = recs[lst[k]]
            gap = None
            if len(labels) >= 2:
                n_lab += 1
                if seed == labels[0]:
                    seed_eq_l0 += 1
                elif seed == labels[1]:
                    seed_eq_l1 += 1
                else:
                    seed_other += 1
            ent = pos_map.get(pos)
            if not ent or ent[0] != 1:
                continue
            dlen, props = ent[1]
            # A1 pos-gap (needs a next rec in same conv)
            if k + 1 < len(lst):
                gap = recs[lst[k + 1]][0] - pos
                gap_by_dlen.setdefault(dlen, Counter())[gap] += 1
            if len(labels) < 2:
                continue
            joined += 1
            if dlen == 1:
                dl1_tot += 1
                if props[0] == labels[1]:
                    dl1_acc += 1
            for d in range(min(dlen, len(labels) - 1)):
                ok = props[d] == labels[d + 1]
                if d < dlen - 1:
                    pre_last_tot += 1
                    pre_last_ok += ok
                else:
                    last_tot += 1
                    last_ok += ok
            if dlen < len(labels) - 1 and dlen < 5:
                reject_tot += 1
                reject_ok += props[dlen] == labels[dlen + 1]
            # A4: gap-run vs labels-chain per-slot agreement
            if gap is not None:
                n_gap += 1
                if gap <= 0:
                    adj_neg += 1
                adj_n += 1
                w = min(dlen, len(labels) - 1)
                kl = 0
                while kl < w and props[kl] == labels[kl + 1]:
                    kl += 1
                if kl == w and w < dlen:
                    kl = -kl        # window-limited full match: key as -w
                klab_hist.setdefault(kl, Counter())[gap] += 1
                if gap > dlen + 1:
                    skipamb += 1
                else:
                    kg = min(gap - 1, dlen)   # accepted run from the gap
                    # NB (S0 review): gap_accept is FORMULAIC at d=0 in the
                    # exact era (all gaps >=2 -> always 1); do not read it
                    # as "slot0 always commits". Gap is not an accept oracle
                    # there (A5); labels-chain is.
                    for d in range(min(dlen, len(labels) - 1)):
                        g_ind = 1 if d < kg else 0
                        l_ind = 1 if props[d] == labels[d + 1] else 0
                        s = slot_stats.setdefault(d, [0, 0, 0])
                        s[0] += g_ind
                        s[1] += l_ind
                        s[2] += 1
                        agree += g_ind == l_ind
                        agree_n += 1

    gap_summary = {}
    for dlen, hist in sorted(gap_by_dlen.items()):
        tot = sum(hist.values())
        top = hist.most_common(4)
        gap_summary[dlen] = {
            "n": tot,
            "gap==dlen": pct(hist.get(dlen, 0), tot),
            "gap==dlen+1": pct(hist.get(dlen + 1, 0), tot),
            "top": {int(g): c for g, c in top},
        }

    return {
        "recs": os.path.basename(recs_path),
        "parity_cycles": n_par,
        "n_recs": len(recs),
        "parity_distinct_pos": len(pos_map),
        "rec_seed_field": {"==labels[0]": pct(seed_eq_l0, n_lab),
                           "==labels[1]": pct(seed_eq_l1, n_lab),
                           "other": pct(seed_other, n_lab)},
        "dlen_hist_distinct_pos": {str(k): v for k, v in
                                   sorted(dlen_hist.items())},
        "A1_gap_by_dlen": gap_summary,
        "A2_prefix_d_lt_dlen_minus1": {"n": pre_last_tot,
                                       "match": pct(pre_last_ok,
                                                    pre_last_tot)},
        "A2_last_slot_d_eq_dlen_minus1": {"n": last_tot,
                                          "match": pct(last_ok, last_tot)},
        "A3_reject_slot_props_dlen": {"n": reject_tot,
                                      "match": pct(reject_ok, reject_tot)},
        "dlen1_props0_eq_labels1": {"n": dl1_tot,
                                    "match": pct(dl1_acc, dl1_tot)},
        "A4_per_slot": {str(d): {"gap_accept": pct(v[0], v[2]),
                                 "lab_accept": pct(v[1], v[2]),
                                 "n": v[2]}
                        for d, v in sorted(slot_stats.items())},
        "A4_agreement": {"n": agree_n, "rate": pct(agree, agree_n),
                         "n_joined_with_gap": n_gap,
                         "skip_ambiguous_gaps": skipamb},
        "A5_klab_vs_gap": {
            str(kl): {"n": sum(c.values()),
                      "gap_eq_kl_plus1": pct(c.get(kl + 1, 0),
                                             sum(c.values())),
                      "gap_eq_1": pct(c.get(1, 0), sum(c.values())),
                      "top": {str(g): n for g, n in c.most_common(5)}}
            for kl, c in sorted(klab_hist.items())},
        "A5_adjacency": {"n_with_gap": adj_n, "gap_le_0": adj_neg},
        "joined_pos_unique": joined,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recs", action="append", required=True)
    ap.add_argument("--parity", action="append", required=True)
    ap.add_argument("--tag", default="probe3c")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if len(a.recs) != len(a.parity):
        raise SystemExit("--recs/--parity must pair 1:1")
    report = {"tag": a.tag, "files": []}
    for r, p in zip(a.recs, a.parity):
        res = analyze(r, p)
        report["files"].append(res)
        print(f"== {res['recs']}  recs={res['n_recs']} "
              f"parity={res['parity_cycles']} "
              f"joined={res['joined_pos_unique']}")
        for key in ("rec_seed_field", "dlen_hist_distinct_pos",
                    "A1_gap_by_dlen", "A2_prefix_d_lt_dlen_minus1",
                    "A2_last_slot_d_eq_dlen_minus1",
                    "A3_reject_slot_props_dlen",
                    "dlen1_props0_eq_labels1", "A4_per_slot",
                    "A4_agreement", "A5_klab_vs_gap", "A5_adjacency"):
            print(f"   {key}: {res[key]}")
    if a.out:
        with open(a.out, "w") as f:
            json.dump(report, f, indent=1)
        print(f"report -> {a.out}")


if __name__ == "__main__":
    main()
