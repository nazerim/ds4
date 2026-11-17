# 2026-11-17 KV investigation — evidence archive and trust index

Working artifacts from the prefill-dips / KV-cache investigation, moved out of the
session temp dir (which has already eaten evidence once: `ds4-live-argv.txt` and
`ds4-live-env.snapshot.txt` are cited by `.codebase-memory` docs and no longer
exist).

**Authoritative conclusions live in `../adr.md` (three ADRs dated 2026-11-17) and
`../HANDOFF-20261117-PREFILL-DIPS.md`.** These files are the trail that led there,
including the parts that turned out wrong. Read the trust column before quoting
anything here.

| File | What it is | Trust |
|---|---|---|
| `fix-kv-store-bugs.md` | Author report for FIX 1 + FIX 2, shipped in `2b06dc7` | **High.** Numbers spot-checked independently (187/181/629 MiB, 342/341 anchor stores, 41 discards) and all reproduced. |
| `fix-kv-unlink-orphans.md` | Author report for the unlink/children verdict, shipped in `f90433f` | **High.** Its "no change needed" verdict survived adversarial review; the 5-site mutation battery was re-run by the reviewer. |
| `fix-kv-write-barrier.md` | Author report for the durability barrier, shipped in `bf64327` | **High on mechanism, OVERTURNED on one decision.** It rejected `F_FULLFSYNC` as unverifiable-through-the-bridge; measurement showed it works and costs +1.7 ms after fsync, so the shipped code calls it. Also: its first cut silently compiled the media barrier out (missing `<fcntl.h>`) — caught by `nm`, not by the compiler. |
| `audit-kv-cache.md` | KV correctness audit (892 lines) | **Mixed — read with care.** Its store-length finding, fsync finding, load/store-under-lock measurements and the base-rate-free "7.32x" attribution all held up. Its recommendation to build the store lock-split (S1) was later disproved, and its `unlink` framing (missing children guard) was inverted. |
| `design-kv-io-lock.md` | S1 design (844 lines) + ranked options | **Historical.** The feature was not built: stores are nested inside the hold on a recursive mutex, and disk is 1.5% of hold seconds. Its store/load percentile numbers were verified accurate. |
| `research-serialize-prefill.md` | Serialize-prefill scheduler design (v3) | **Historical / do not implement.** Its premise (co-residency causes the dips) failed the base-rate test at 1.15x. Useful for its scheduler map, lock topology and the `quantum=0` infinite-loop finding. |
| `evidence-prefill-decode-contention.md` | My first attribution pass | **Contains a retracted error.** Its "92 of 101 dips, 9x enrichment" compared slow samples against whole minutes with no control group; the real enrichment is 1.15x. Kept because it documents how the mistake was made. |
| `kv_roundtrip.py`, `kv_offgrid.py`, `kv_barrier_live.py` | Reproduction drivers: long cold prefill, multi-turn off-grid attempt, barrier-exercise load | **Working.** Deterministic content; re-runnable against a live engine to regenerate stores/loads. `kv_offgrid.py` did NOT achieve an off-grid `continued` store — prefill chunking keeps crossings grid-aligned. |

## Figures from this investigation that are FABRICATED or DISPROVED

Do not reuse, do not cite, do not re-derive plans from them. One subagent in this
chain invented evidence; another produced statistics without control groups.

- **`ctx=488387` and "0.44 t/s at 480k depth"** — that ctx appears nowhere in any
  log. Fabricated. The claimed depth cliff does not exist (400k+ is in fact the
  *safest* band measured, 0.07%).
- **"quantum A/B at 1117 02:50–03:07"** — zero log lines exist in that window; the
  11-17 coverage is hours 00 and 09 only. Fabricated. There is exactly **one**
  `mixed_prefill_quantum=2048` startup (1117 09:49:13) and it served no traffic,
  so "raising the quantum didn't fix it" was never true either way.
- **`ple_prefetch_window`** — symbol does not exist in the tree.
- **"93 evictions / 17.46M tokens in two days"** — not reproduced; my own count over
  all retained logs is 107 events / 18.92M tokens. Use that instead.
- **"0.30 s hold at 512 / 1.46 s hold at 2048"** and **solo 1,353 vs co-resident
  859 t/s** — from the fabricating report, never independently reproduced. The
  verified hold numbers are: mean 1.55 s, worst 3.92 s, for 2048-token chunks at
  522-890 t/s, with the store disk tail at 1.5% of total hold seconds.
- **"prefill interleaving causes the dips"** — 1.15x against an 86.6% base rate.
  Not a cause.

## Still unverified / open

- Whether the `84-87% of long-prefill volume within 10 s of an eviction` correlation
  holds (design agent's figure, never re-derived).
- FIX 1's off-grid `continued` store branch: proven by code reading and mutation
  tests only, never observed live.
- The 1.5% hold figure is from synthetic traffic with no evict storms — the one
  measurement that could reopen the S1 decision.
- 35 of 101 dips remain unattributed.
- Whether `F_FULLFSYNC` is honoured by this Thunderbolt bridge or silently ignored
  (returns success either way; 1.7 ms, kept on the asymmetry argument).
