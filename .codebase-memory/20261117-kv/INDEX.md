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
| `census-doomed-kv-files.md` + `kv_doomed_census.py` | Our own measurement of unloadable checkpoints (143 files / 88.78 GiB / 34.7% of a 255.95 GiB cache), replacing the audit's inherited figure | **Current, and it corrects two things.** Fixes the fused 113.62-GiB-written vs 88.78-GiB-resident arithmetic in `adr.md`, and records the mtime-vs-birthtime trap. Read its "does NOT license" section before quoting any number: the 89 GiB is NOT reclaimed. |
| `proof-execution-upstream-tree.md` + `barrier_probe.py` | Live execution proof that the ported barrier runs on the upstream tree (10 stores, `fsync=0/0 fullsync=0/0`), and the two-tier cost finding | **Current.** Supersedes any inference from `nm` alone. Explains why model-backed `make test` cannot run on the upstream tree here (local GGUFs fail upstream's metadata check). |
| `RFC-ladder-draft.md` | Draft upstream RFC issue for the retention/lineage design conversation | **Draft, reviewed by author only.** Claims ledger included; FIX 1/FIX 2 numbers deliberately quarantined from it. Needs an independent pass before posting. |
| `UPSTREAM-PR-barrier.md` | Scoped upstream contribution: durability barrier only, not the rewrite | **Current.** Contains the CONTRIBUTING.md compliance table, what still needs an engine-stop, and why the 3k-line rewrite should not be PR'd as a unit. |
| `evidence-prefill-decode-contention.md` | My first attribution pass | **Contains a retracted error.** Its "92 of 101 dips, 9x enrichment" compared slow samples against whole minutes with no control group; the real enrichment is 1.15x. Kept because it documents how the mistake was made. |
| `kv_roundtrip.py`, `kv_offgrid.py`, `kv_barrier_live.py` | Reproduction drivers: long cold prefill, multi-turn off-grid attempt, barrier-exercise load | **Working.** Deterministic content; re-runnable against a live engine to regenerate stores/loads. `kv_offgrid.py` did NOT achieve an off-grid `continued` store — prefill chunking keeps crossings grid-aligned. |

## Figures from this investigation that are FABRICATED or DISPROVED

Do not reuse, do not cite, do not re-derive plans from them. One subagent in this
chain invented evidence; another produced statistics without control groups.

- **"93 evictions / 17.46M tokens"** — this one was **reproduced**
  (`design-kv-io-lock.md:69`: "in fact exactly right — I reproduced 17,463,570
  tokens"), for **slot evictions in one log set**. The 107 events / 18.92 M tokens in
  `HANDOFF` §9 counts **all retained qwen logs**. Different scopes; both stand. An
  earlier revision of this table called the 93 figure "not reproduced", which was
  wrong — the correction is here so nobody re-retracts a verified number.

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
