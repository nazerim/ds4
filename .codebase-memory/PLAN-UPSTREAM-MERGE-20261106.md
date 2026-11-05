# PLAN — Upstream merge-debt digestion (20261106, **v2**)

> v1 was SMASHED by independent review (00:5x). Its audit half (§1) was
> verified correct; its merge-strategy half was wrong in four load-bearing
> ways: calendar-based batching does not exist (upstream commit dates are
> not topologically monotonic - v1's "Batch A ~100 commits" was actually
> 1 commit, the ~200-commit storm sat in "Batch C"), ds4_metal.m weight
> understated ~4x (27 fork vs 102 upstream), the supersede premise was
> misattributed (parallel edits, not absorption), and "merge-base" was
> singular when main has TWO bases. v2 is rebuilt on the reviewer's
> method and my own re-measurement (calibrated: my hunk counts match
> theirs 13/13 on big-bang). Execution status: this doc is LIVE -
> operator free-rein 00:5x instructed proceeding once updated.

## 1. Lane audit (verified by review, unchanged)
| branch | state |
|---|---|
| main | == nazerim/main; ahead 458-460 / **behind 234** of origin |
| feat/kvstore-tokenizer-fingerprint | ds4-pr1020 worktree clean; precedent: already carries `Merge origin/main` |
| feat/kvstore-reload-loop-guard | synced (self-withdrawn upstream #1021; behavior lives fork-side) |
| pr1149 / pr1179 / pr1179-oct11 | 20 unique commits; **pushed to nazerim tonight** (were local-only) |
| fix/unterminated-tool-envelope | pushed, not in main = PR #1163 (MERGEABLE upstream, silent) |
| int8-moe-prefill | pushed; lever rejected; archive |
| pr1149-ab / pr1238 | fully in main ✓ |

## 2. Merge shape (v2, topological, measured)
- Bases: TWO (`b5aedb9` + `5379c1c`, main absorbed upstream Oct-9→19 via
  ff357e1). Debt = `main..origin/main` = **234 commits**, whose
  first-parent chain is **60 upstream merge/tip milestones** (upstream
  merges PRs on first-parent; that's why calendar slicing failed).
- Big-bang conflict set (7 files, reviewer-verified + my rerun):
  `.gitignore, ds4.c, ds4_server.c, ds4_metal.m, metal/qwen4.metal,
  tests/test_metal_command_memory.c, tests/test_qwen4_kernels.c`;
  52 overlap − 7 = 45 auto-merge; Makefile auto-merges CLEAN (but see
  gate G6 re cross-platform compile).
- Weights (fork vs upstream, base..X): ds4.c 63/150, ds4_server.c 83/10,
  **ds4_metal.m 27/102**, Makefile 21/33, ds4.h 27/1, tests/ds4_test.c 30/5.
- Batch points (first-parent thirds, merge-tree measured vs main):
  | batch | milestone | new commits | conflicts (files, ds4.c hunks) |
  |---|---|---|---|
  | A | `23c333bd` | +33 | ds4.c (2 hunks) + test_metal_command_memory.c (1) |
  | B | `c1bce729` | +25 | ds4.c (1), ds4_metal.m, qwen4.metal, test_metal_command_memory.c |
  | C | `b3f50180` (tip) | +176 | all 7 files (measured against LIVE merge/upstream at the time, not main) |
- Per-file conflict-hunk counting METHOD (banked, calibrates 13/13):
  `merge-tree --write-tree` tree oid → `git ls-tree $tree <f>` → cat-file
  the stage-0 blob → `grep -c '^<<<<<<<'`.

## 2b. Supersede facts (Batch-0 evidence, `git cherry` patch-id)
- pr1179-oct11: **18/18 '+' (NOT superseded)**; pr1179 17/18 '+';
  pr1149 2/2 '+'. The Metal-test-file conflicts are PARALLEL EDITS of a
  file that existed at base (added bd66c40 Sep 12 both sides), not
  absorption of our vendor tests. SUPERSEDED marking requires patch-id/
  diff proof, never conflict presence.
- Real absorption IS present: `95976d7 Merge #1238: speed up Qwen3.8
  Metal decode` (paradise-runner, merged Nov 3) sits inside the debt;
  our pr1238 was already folded into main, so expect ds4_metal.m/qwen4
  hunks to resolve toward upstream where they restate #1238.
- Net: Batch-0 conclusion = **nothing to drop from main; vendor branches
  stay as test-vendored (re-test #1179/#1149 post-land if still unmerged
  upstream).**

## 3. Execution (LIVE)
Worktree `/Users/naz/Projects/ds4-merge`, branch `merge/upstream` from
main. main + live engine untouched until landing. Never force-push.

Gates per batch, in order:
- **G1** tag `merge-pre-X`; `git merge --no-ff <milestone>`.
- **G2** conflict resolution under the **fork-preservation checklist**
  (per hunk): KV-cache-v2 lineage hooks (`ds4_td_segment_end` adopt/
  boundary capture), DSpark paths + stats-flush-at-session-free,
  DSML rewind-resume fast-path + `dspark_rewind_recent` gating, capture
  env `:-` defaults, session-sync RESET semantics + PAYUD fields
  (`DS4_SESSION_PAYLOAD_U32_FIELDS`, `IO_CHUNK`), tool_choice parsing,
  KV budget 524288. Reviewer confirmed these live INSIDE the big-bang
  ds4.c hunks - resolution is semantic, not mechanical.
- **G3** `make` (all 5 binaries) in worktree.
- **G4** test tiers: coexist (engine running): `--server`,
  `--mtp-slice` subsets, `tests/test_dspark_rewind.py` (host mocks);
  stop-tier (engine stopped, free-rein authorized windows): `./ds4_test`
  bare full suite + `make dspark-acceptance`. Stop-tier once per batch
  (~45 min), engine restored via snapshot recipe + 4-point verify +
  sentinel after EVERY stop.
- **G5** artifact convention: ls-files grep empty + check-ignore matches
  (regression precedent 7d7b8cc).
- **G6** cross-platform accepted-risk line: `make cuda` / `rocm` /
  `strix-halo` cannot be compile-gated on this macOS box - Makefile
  auto-merge cleanness ≠ compilability. Operator/Linux-box follow-up
  noted; not blocking for Metal-first fork.
- **G7** push `merge/upstream` + append batch note (state, resolutions,
  gate results) to this doc.
- **Pre-land (v2 addition, F6):** main keeps moving under the batches →
  final step = `git merge main` into merge/upstream, re-run ALL gates,
  then (and only then) land.
- **Landing (operator window, combined with day-16 seal):** full stop-
  tier green → seal day-16 (engine down anyway) → **new-binary-reads-
  old-checkpoints round-trip gate (v2 addition, F7: boot candidate
  binary against sealed day-16 capture + segment files, byte-compare
  lineage metadata + `invalid=0`, before re-arming sentinel)** → merge
  merge/upstream into main → snapshot-recipe boot + 4-point verify.
- Rollback: `revert -m 1` per landed batch; worktree = free rollback
  until landing; blade holds corpus not binaries.

## 4. Steady state (post-digestion)
- Weekly: `merge-tree` probe vs origin/main; delta on overlap files >
  ~30 commits → mini-batch same week. First-parent slicing, never dates.
- Lane discipline: `git log --oneline --branches --not --remotes` must
  print empty before idle; lanes merge main-moves within a day.
- Post-land: refresh PR branches #1020 + #1022 (merge commit, PRs
  un-conflict); re-test vendor #1179/#1149 if still open; F1 gate work
  resumes on settled ds4.c.

## Execution log (append)
- 00:5x v1 SMASHED by review ses_e5364bce0 (findings F1-F7); v2 rebuilt.
- Batch-0 complete (cherry evidence above).
- 01:0x **Batch A** `2c931a0` (+33c): 3 hunks; PLE-prefetch taken theirs,
  RCAB infra grafted around _impl rename; metal-test usage union.
  Coexist-tier green. (My batch-A adapter was OBSOLETE by batch B —
  upstream had independently rederived the fork's split; reverted.)
- 01:1x **Batch B** `45cfb26` (+25c): stage_embeddings/stage_ple split +
  decode ple_overlap adopted; metal ABI union round 1 (n_hc slot 13,
  out_layout 14). Coexist-tier green.
- 01:1x **Batch C** `47e22a7` (+176c, tip): 28 hunks/6 files. Deep
  resolutions: (a) attention: upstream core_rows split adopted with the
  fork per-row-verify mechanism grafted inside - upstream else-path IS
  the fork default (bit-identity preserved), fork env takes precedence
  over upstream's T==3 2/1-split when armed; (b) WIRE FORMAT: upstream's
  v3 reuses tag 0x51573803 (fork's V2 lineage format) with different
  layout - resolved by fork-sovereignty: trio functions swapped to fork
  bodies, upstream tail becomes runtime-only (+reset re-added at load),
  upstream's legacy(0x...02)=whole stays compatible with our V1; cost =
  one predictor rebuild after restore, no silent misread; (c) metal ABI
  union round 2: slot_mask[3]+masked_tokens joined, out_layout last,
  sizeof 76->80 TRAILING-PADDING trap caught by both asserts (bumped in
  host _Static_assert AND device static_assert); (d) server vision:
  SERVER_MAX_IMAGES=256 gate (upstream arrays sized by it) hoisted ahead
  of fork keep-images policy; (e) shared_dense env knob hoisted to
  upstream's new early consumer, duplicate deleted.
  Build: 0 warnings 0 errors; coexist-tier green (server/mtp-slice).
- FINDING (pre-existing, NOT merge): tests/test_dspark_rewind.py
  prefix_slots variants red on MAIN too - test mock not updated for
  Nov-3/4 instrumentation (end_cause/ds4_td_segment_end/gate). Engine
  lane owns the fix (mock-only). Reported for morning.
- 01:2x STOP-TIER running (free-rein window): engine down, full ./ds4_test
  + make dspark-acceptance in worktree, capped (40/20 min), trap-restore
  + detached 45-min watchdog + shell-notification = triple coverage;
  results appended below when green.
- ROUND-1 FACT (01:19): stopgateC ran with NO model symlink in worktree
  -> suite + dspark-acceptance skipped (rc0 misreport; capped-rc reads
  the tail-pipe). Its restore trap fired from WORKTREE cwd (script bug:
  no cd in trap) -> collector PID 57826 booted from WORKTREE = merged
  binary accidentally running production collect 01:19:18-01:23:20 with
  PRODUCTION capture env (blade paths, banner OK, invalid=0) -> doubles
  as boot-smoke PASS for batch C binary. DAY-16 MANIFEST era note:
  57826 = merged-code era (~4 min); 58173/58304 = failed lock-refused
  boots (no records); 58604 = main old-binary collector 01:25:52+.
- ROUND-2 ABORT (01:21, stopgateC2): bare `stop` from main found no
  pidfile (engine's was in worktree) -> pidfile-based stop no-op ->
  correct ABORT-by-design fired; trap restored from main cwd (58604).
- ROUND-3 ABORT (01:23, stopgateC3): watchdog3 was launched as INLINE
  bash -c whose command line contains the engine's argv pattern string ->
  `pgrep -f 'ds4-server --model'` self-matched forever -> fake drain
  timeout -> abort before suite; ARGV diff then read the watchdog (the
  real engine 58604 was healthy, invalid=0, pidfile at main).
- LESSONS BANKED (bind all future chains): (1) engine liveness ONLY via
  PIDFILE (alive()/status), never pgrep -f argv text; (2) watchdogs are
  FILE-BASED scripts so their cmdline carries no engine strings;
  (3) restore traps must cd to the intended repo absolutely; (4) suite
  needs <cwd>/ds4flash.gguf symlink (it is itself a symlink to the 97.6 GB
  blade model - "119-byte stub" in old docs = the symlink's size field).
- ROUND-4 (C4, launched 01:27): pidfile-clean rerun - stop 58604 -> full
  suite WITH model -> merged-binary smoke (dev-redirect capture) -> trap
  restore main collector. wd4 file-based, 90 min.
- **G-REVIEW gate (operator directive 20261106): before landing
  merge/upstream into main, an independent fresh-context code review of
  the whole merge branch MUST run and its findings be banked.** Landing
  itself stays coupled to the day-16 seal window (F7 checkpoint
  round-trip + boot swap); batches + pre-land re-merge can complete
  overnight.
