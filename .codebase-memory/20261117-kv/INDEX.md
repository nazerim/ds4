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
| `RFC-ladder-draft.md` | Draft upstream RFC issue for the retention/lineage design conversation | **Draft, independently audited 2026-11-17 (6 blocking findings, all applied; 1 finding rejected as a phantom quote).** Postable after a read-through. Claims ledger included; FIX 1/FIX 2 numbers deliberately quarantined from it. Needs an independent pass before posting. |
| `post-pr-barrier.md` | **The exact text filed as upstream PR antirez/ds4#1358** (below its `---` line) | **Posted 2026-11-17.** Body re-verified byte-identical against the live PR. Seven defects were caught by an independent proofread before posting — see the commit that added this file; `UPSTREAM-PR-barrier.md` keeps the gate evidence. |
| `post-issue1020-nudge.md` | Comment posted on our open PR #1020 (tokenizer fingerprint) | **Posted 2026-11-17.** Asks which `ext_flags` representation the reviewer meant, the one open decision, and flags the real coupling with #1343 in `ds4_kvstore_try_load_text`. |
| `BENCH-MANIFEST.md` + `barrier-{before,after}{,2}.csv` + `bench-stdout-rep2-*.log` | Raw sweeps behind the speed claim in public PR #1358: 4 arms x 32 context points, A/B/B/A order, with the exact command, the `--ssd-streaming` reason, sha256 per file, and recomputed means | **Current and load-bearing for a public claim.** Kept because the same numbers lived in the session temp dir, which has destroyed evidence twice. Re-derive the PR's +0.52 %/-0.27 % from these, not from the prose. |
| `proof-run1-raw.log`, `proof-run2-raw.log` | Unedited stderr from the instrumented upstream build: 12 barrier executions, `fsync=0/0 fullsync=0/0`, plus the matching `kv cache stored` lines | **Current.** Primary evidence behind `proof-execution-upstream-tree.md`, which is a transcription of these. |
| `maketest-control-b3f5018.log`, `server-suite-dcf77c4.txt` | The 17-failure control run on unmodified upstream base, and the `--server` pass on the exact sha filed as #1358 | **Current.** They are what makes the PR's "not green here, and not because of this patch" statement checkable. |
| `SIGNATURE-NOTE.md` | Why GitHub shows no Verified badge on our commits | **Current.** Pre-existing account state (all our PRs blank), not a regression; fix is an operator action. |
| `UPSTREAM-PR-barrier.md` | Scoped upstream contribution: durability barrier only, not the rewrite | **Current.** Contains the CONTRIBUTING.md compliance table, what still needs an engine-stop, and why the 3k-line rewrite should not be PR'd as a unit. |
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
- **"93 evictions / 17.46M tokens"** — this one was **reproduced**
  (`design-kv-io-lock.md:69-70`: "in fact exactly right — I reproduced 17,463,570
  tokens"), counting **slot evictions in one log set**. The 107 events / 18,917,350
  tokens in `HANDOFF` §9 counts **all retained qwen logs**. Different scopes, both
  stand. An earlier revision of this table called the 93 figure "not reproduced",
  which was wrong — recorded here so nobody re-retracts a verified number.
- **"0.30 s hold at 512 / 1.46 s hold at 2048"** — source report was in the lost temp
  dir; **unverifiable rather than disproved**. Hold telemetry cannot settle it either
  way: the warn threshold is 2.0 s (sub-2 s holds are never logged) and every hold line
  in every retained log shows `quantum=2048`, none at 512.
- **"solo 1,353 vs co-resident 859 t/s"** — **unreproduced, NOT fabricated, and
  misattributed in the earlier revision of this table.** They are in
  `research-serialize-prefill.md:118` (the *design* agent's file), not the fabricating
  mechanism report, whose inventions were `ctx=488387`, the empty 1117 02:50–03:07
  window and `ple_prefetch_window`. The interval-partition method behind 1,353/859 was
  never recovered (a proxy recount finds 14 long runs, not 88), so do **not** cite them
  as measurement — which also reaches §2.4's "~660 s / 56 % of wall destroyed"
  counterfactual and the §5.3/§6 cap-sizing built on it. The
  verified hold numbers are: mean 1.55 s, worst 3.92 s, for 2048-token chunks at
  522-890 t/s, with the store disk tail at 1.5% of total hold seconds.
- **"prefill interleaving causes the dips"** — 1.15x against an 86.6% base rate.
  Not a cause.

## Still unverified / open

- Whether the `84-87 % of long-prefill volume within 10 s of an eviction **or a
  `live kv cache miss reason=token-mismatch`**` correlation holds — the source
  (`research-serialize-prefill.md:133`) is a **union** of two triggers, which an
  earlier revision of this table dropped. Any re-derivation must exclude the
  triggering request itself: the miss/evict line is logged by the very request that
  then prefills, so the naive metric is ≈100 % by construction (a proxy run confirmed
  the circularity).
- FIX 1's off-grid `continued` store branch: proven by code reading and mutation
  tests only, never observed live.
- The 1.5 % hold figure re-derives **exactly** (23 stores, 2.44 s of 163.0 s window =
  1.49 %), but its "no evict storms" caveat is **false as written**: that same window
  contains 4 `evicting checkpoint` events and 3 back-to-back evict store→load pairs
  (479.6 / 190.4 / 231.1 ms), with 4 of 23 stores at `reason=evict`. Light evict
  traffic is already priced in. What would reopen S1 is evict **density** plus
  load-side holds — so `HANDOFF` §9.3's "did not reproduce the regime" must be read as
  "not at storm density", and the bar to overturn 1.5 % is higher than this bullet
  implied.
- 35 of 101 dips remain unattributed.
- Whether `F_FULLFSYNC` is honoured by this Thunderbolt bridge or silently ignored
  (returns success either way; 1.7 ms, kept on the asymmetry argument).
