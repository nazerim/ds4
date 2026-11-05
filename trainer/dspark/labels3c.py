"""S1a/S1b label-slab builder (3C-NIGHT-PLAN-20261103).

Consumes D-S0 FINAL decisions from .codebase-memory/3C-NIGHT-PLAN-20261103.md.
Two modes:
  --mode conf    per-slot CONF ACCEPT slab from EXACT-ERA cycles only
                 (conf BCE: engine conf-row -> trainable confidence proj).
                 Input = parity conf_rows[:dlen]; label = three-case cap rule.
  --mode markov  per-cycle NEXT-TOKEN slab, era-AGNOSTIC (all clean eras),
                 for the trainable markov head (labels = committed stream).

Join: recs[i] <-> parity[i] are INDEX 1:1 (same record index; both written
under the engine's ok-gate, ds4.c:80681-80683, n_par==n_recs). We ASSERT the
positions agree as a safety net; index join is strictly safer than pos-unique
(no cross-conv collisions) and includes rejected cycles.

CONF accept-label three-case cap rule (D-S0-1), w = min(dlen, n_label-1):
  scan d<w for first mismatch k_lab (props[d]!=labels[d+1]);
   a) mismatch k_lab<w: 1 for d<k_lab, 0 at k_lab, MASK d>k_lab
   b) no mismatch, w==dlen: all 1 on [0,dlen)      (mask d>=dlen implicit)
   c) no mismatch, w<dlen: all 1 on [0,w), MASK w..dlen-1
  dlen==1 excluded (fake_argmax, target's own argmax not a draft, 80533).
  Conf rows beyond dlen are STALE -> never read (we only take [:dlen]).
  props[d] for d>=dlen are zero-filled -> never a "reject" label (80533 zone).

Output (npz, memory-capped): flat (total_slots,4096) conf f32 + per-slot
label int8 {0,1,-1=mask} + per-slot cycle-id + per-cycle meta (pos,dlen,
k_lab,w,n_label,conv). Markov mode: flat seed token + next token + valid flag.

Usage:
  uv run python -m dspark.labels3c --mode conf --pair RECSPATH PARITYPATH
       [--pair ...] --quarantine LOOP-QUARANTINE-*.json --out SLAB.npz
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import struct
import numpy as np

REC_HDR = struct.Struct("<IiBBBB5i")
RECORD_BYTES = 12320
PARITY_STRIDE = 81952
PARITY_HDR = struct.Struct("<IiBBH")
PAR_PROPS = struct.Struct("<5i")
N_EMBD = 4096


def conv_of_records(recs_path: str, n: int) -> np.ndarray:
    idx = recs_path.replace(".recs", ".idx.jsonl")
    conv = np.zeros(n, dtype=np.int32)
    cursor = 0
    with open(idx) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            cnt = int(r["flushed"])
            conv[cursor:cursor + cnt] = int(r["conv"])
            cursor += cnt
    if cursor != n:
        raise RuntimeError(f"idx cursor {cursor} != {n}")
    return conv


def quarantine_excl(recs_path: str, q) -> set:
    """Set of RECORD INDICES excluded in this file (loops.file_exclusions)."""
    from .loops import file_exclusions
    return file_exclusions(recs_path, q)


def build_pair(recs_path: str, parity_path: str | None, q, mode: str,
               cyc_offset: int = 0, allow_any_era: bool = False,
               file_id: int = 0, known_exact: frozenset = frozenset()):
    """One (recs[, parity]) pair. markov mode may run parity-less (labels
    come from recs alone); conf mode REQUIRES parity (conf-row features)."""
    stem = os.path.basename(recs_path)
    if mode == "conf" and parity_path is None:
        raise SystemExit("conf mode needs a parity path (features)")
    if mode == "conf" and not allow_any_era:
        # era guard (D-S0-3): pid>=53508 was a PROXY for the exact-era
        # boot, valid only under day-13's monotonic pid space; post-
        # restart boots recycle small pids (engine-lane restarts Nov 4-5)
        # -> --known-exact-pids carries operator-evidenced exact boots.
        try:
            pid = int(stem.rsplit("pid", 1)[1].split(".")[0])
        except ValueError:
            raise SystemExit(f"cannot parse pid from {stem}")
        if pid < 53508 and pid not in known_exact:
            raise SystemExit(f"{stem}: pid {pid} < 53508 = presumed seed-"
                             "batch era, conf labels INVALID (D-S0-3); if "
                             "this boot ran --mtp-exact-sampling pass "
                             "--known-exact-pids. --allow-any-era "
                             "overrides all guards (knowingly).")
    n = os.path.getsize(recs_path) // RECORD_BYTES
    npar = (os.path.getsize(parity_path) // PARITY_STRIDE
            if parity_path else -1)
    conv = conv_of_records(recs_path, n)
    excl_idx = quarantine_excl(recs_path, q) if q else set()

    rr = open(recs_path, "rb")
    mmr = mmap.mmap(rr.fileno(), 0, access=mmap.ACCESS_READ)
    if parity_path:
        pr = open(parity_path, "rb")
        mmp = mmap.mmap(pr.fileno(), 0, access=mmap.ACCESS_READ)
    else:
        pr = mmp = None

    recs = []
    for i in range(n):
        r = REC_HDR.unpack_from(mmr, i * RECORD_BYTES)
        nl = r[3]
        recs.append((int(r[0]), int(r[1]), list(r[6:6 + nl]), nl))

    stats = {"n": n, "npar": npar,
             "index_join": min(n, npar) if parity_path else n,
             "pos_mismatch": 0, "kept": 0, "skip_quar": 0, "skip_dlen1": 0,
             "skip_dlen_range": 0, "skip_nlabel": 0,
             "excl_idx": len(excl_idx),
             "quarantine": bool(q)}
    slots_conf = []   # per kept cycle: (conf_rows[dlen,4096], labels[dlen])
    mk_seed, mk_next, mk_valid = [], [], []
    mk_file, mk_ridx = [], []
    meta = []
    slot_rows, slot_lbl, slot_cyc = [], [], []
    cyc = cyc_offset
    nlim = min(n, npar) if parity_path else n
    for i in range(nlim):
        pos, seed, labels, nl = recs[i]
        if parity_path:
            base = i * PARITY_STRIDE
            ppos, pseed, dlen, _st, _r = PARITY_HDR.unpack_from(mmp, base)
            if ppos != pos:
                raise RuntimeError(f"index join broken at record {i}: recs "
                                   f"pos {pos} != parity pos {ppos}")
            props = list(PAR_PROPS.unpack_from(mmp, base + 12))
        if i in excl_idx:
            stats["skip_quar"] += 1
            continue
        if mode == "conf":
            if nl < 2:
                stats["skip_nlabel"] += 1
                continue
            if dlen == 1:
                stats["skip_dlen1"] += 1
                continue
            if dlen < 1 or dlen > 5:
                stats["skip_dlen_range"] += 1
                continue
            w = min(dlen, nl - 1)
            klab = next((d for d in range(w) if props[d] != labels[d + 1]),
                        None)
            lbl = np.ones(dlen, dtype=np.int8)
            if klab is not None and klab < w:
                lbl[klab] = 0
                lbl[klab + 1:] = -1           # MASK beyond reject point
            elif klab is None and w < dlen:
                lbl[w:] = -1                  # MASK unevaluated tail
            # else: all accepted within window (case b) -> all 1
            slot_rows.append(
                np.frombuffer(mmp, dtype=np.float32, count=dlen * N_EMBD,
                              offset=base + 32).reshape(dlen,
                                                         N_EMBD).astype(
                    np.float16))  # astype copies; no view outlives the loop
            slot_lbl.append(lbl)
            slot_cyc.append(np.full(dlen, cyc, dtype=np.int32))
            meta.append((pos, int(dlen), int(conv[i]), int(klab) if klab
                         is not None else -1, w, nl))
            stats["kept"] += 1
            cyc += 1
        else:  # markov: label[1] = actual next token (era-agnostic)
            valid = 1 if nl >= 2 else 0
            mk_seed.append(labels[0] if labels else -1)
            mk_next.append(labels[1] if nl >= 2 else -1)
            mk_valid.append(valid)
            mk_file.append(file_id)
            mk_ridx.append(i)
            stats["kept"] += valid
    mmr.close(); rr.close()
    if mmp is not None:
        mmp.close()
    if pr is not None:
        pr.close()

    out = {"stats": stats}
    if mode == "conf":
        rows = np.concatenate(slot_rows) if slot_rows else np.zeros(
            (0, N_EMBD), np.float16)
        out["conf_rows"] = rows
        out["slot_label"] = (np.concatenate(slot_lbl) if slot_lbl
                             else np.zeros(0, np.int8))
        out["slot_cycle"] = (np.concatenate(slot_cyc) if slot_cyc
                             else np.zeros(0, np.int32))
        out["meta"] = np.array(meta, dtype=np.int32) if meta else np.zeros(
            (0, 6), np.int32)
    else:
        out["mk_seed"] = np.array(mk_seed, np.int32)
        out["mk_next"] = np.array(mk_next, np.int32)
        out["mk_valid"] = np.array(mk_valid, np.int8)
        out["mk_file"] = np.array(mk_file, np.int16)
        out["mk_ridx"] = np.array(mk_ridx, np.int32)
    return out, cyc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["conf", "markov"], required=True)
    ap.add_argument("--pair", nargs=2, action="append", required=True,
                    metavar=("RECS", "PARITY"),
                    help="PARITY='NONE' for markov mode (recs-only labels)")
    ap.add_argument("--quarantine", default=None)
    ap.add_argument("--allow-any-era", action="store_true",
                    help="skip the conf-mode era guard entirely (knowingly)")
    ap.add_argument("--known-exact-pids", default=None,
                    help="comma list of pids < 53508 whose boot provably "
                         "ran --mtp-exact-sampling (post-restart pid "
                         "recycling; D-S0-3 proxy fix 20261105)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    known_exact = frozenset(int(x) for x in a.known_exact_pids.split(",")) \
        if a.known_exact_pids else frozenset()
    q = None
    if a.quarantine:
        from .loops import load_quarantine
        q = load_quarantine(a.quarantine)
    agg = None
    next_cyc = 0
    pair_stats = []
    for file_id, (recs, par) in enumerate(a.pair):
        par = None if par == "NONE" else par
        res, next_cyc = build_pair(recs, par, q, a.mode,
                                   cyc_offset=next_cyc,
                                   allow_any_era=a.allow_any_era,
                                   file_id=file_id,
                                   known_exact=known_exact)
        print("pair", os.path.basename(recs), res["stats"])
        pair_stats.append({"file_id": file_id, "file": recs,
                           "stats": res["stats"]})
        if agg is None:
            agg = {k: (v.copy() if isinstance(v, np.ndarray) else v)
                   for k, v in res.items() if k != "stats"}
        else:
            # concat simple fields (meta/slot arrays)
            for k in ("conf_rows", "slot_label", "slot_cycle", "mk_seed",
                      "mk_next", "mk_valid", "mk_file", "mk_ridx", "meta"):
                if k in agg and k in res and len(res[k]):
                    agg[k] = np.concatenate([agg[k], res[k]])
    if agg is None:
        raise SystemExit("no pairs")
    np.savez(a.out, **agg, _stats=json.dumps(pair_stats))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
