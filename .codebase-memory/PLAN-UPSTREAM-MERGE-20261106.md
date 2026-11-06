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
- 01:33 **G-REVIEW #1 = SMASH** (ses_e533c7029): F1 CRITICAL brace-elision
  (VALIDATED, mine) + F2 HIGH lost prefetch feature (VALIDATED, mine) +
  "0-warnings claim false" stands against ME (stale incremental build;
  reviewer measured 2x-missing-braces/2x-missing-fields on 099f241, I
  measured 2x-excess on my intermediate 17-clause state - both true,
  different trees). CORRECTION (02:0x delta): my prompt #1 'refuted 2 of
  its 4 secondary claims (hotlist/write_v1 symbols)' attacked claims the
  reviewer NEVER MADE - the 4-symbol list was MY own confabulation from
  notes; hallucination mirror, logged honestly. Anchor sweep/sovereignty/
  graft/ABI verified intact by same review.
- FIX1 `7d85c81`: all 7 qwen4_moe_args sites audited brace-aware = 18
  fields (two auto-merged shared-row sites had 14/17 -> out_layout value
  landing in slot_mask[2] = silent row overlap on the DEFAULT-ON Qwen
  EXACT-VERIFY path); prefetch transplanted at MAIN-HEAD FIDELITY (not
  9000fb7): globals+take_prefetch+MAGIC define, b[7], reuse-gate, pads,
  caller w/ env, header decl, device helper + call block + specialization
  INSTANCE params extended (reuse stays 5-arg, matching main).
  LESSON: symbol-presence anchor sweep is INSUFFICIENT - dead-but-
  present code only shows as compiler -Wunused on a FORCED rebuild.
- FIX2 (device re-parent): my helper splice landed between
  'template <typename W>' and its kernel - hc_norm IS a template kernel -
  orphaned template = metal backend unavailable at runtime. Only ever
  catchable by a boot that DISPATCHES the kernel: Vision-Exp smoke (C4)
  proves nothing about Qwen kernels. Caught by C5-first qwen-fixture
  suite (session-rewind: ERR 'metal backend unavailable'); helper moved
  above the kernel doc comment exactly as main has it. Device .metal is
  invisible to host compile (no `metal` CLI on CLT-only box) => the
  QWEN FIXTURE BOOT IS THE GATE, must stay in the pre-land checklist.
- C5-rerun (sh_1acd9ae60, ~01:57): same pidfile-clean chain on fixed
  HEAD; wd4 disarmed (chain trap proven 3x); pass criteria: zero 'metal
  backend unavailable', rowcount-ab/verify-identity/session-rewind/
  golden-vectors execute (not skip), suite_rc=0.
- **G-REVIEW gate (operator directive 20261106): before landing
  merge/upstream into main, an independent fresh-context code review of
  the whole merge branch MUST run and its findings be banked.** Landing
  itself stays coupled to the day-16 seal window (F7 checkpoint
  round-trip + boot swap); batches + pre-land re-merge can complete
  overnight.
- 01:30 **STOP-TIER PASS (C4, pidfile-clean)**: full ./ds4_test on merged
  binaries suite_rc=0 / 0 FAILED ("ds4 tests: ok"; Qwen3.8-fixture skips
  match main baseline); merged-binary smoke boot 58781 served a real
  greedy chat request with invalid=0 -> Metal device structs (80-byte ABI,
  slot_mask grafts, attention core_rows split) exercised END-TO-END;
  restore 58834 old-binary ARGV-MATCH-FINAL; total downtime ~3 min.
  wd4 (90-min, file-based) still armed = passive engine-death cover
  until ~02:57.
- STATUS: batches A+B+C verified at every gate this box allows. Landing
  (merge/upstream -> main) pending G-REVIEW dispatch + pass; boot-swap +
  day-16 seal + F7 checkpoint round-trip remain one operator window.

DAY-16 MERGE-VERIFY ERA TALLY (for the seal manifest; all official-head
collectors unless noted): 57826 = MERGED-code era, worktree cwd, prod
capture env, 01:19:18-01:23:20 (records valid, pre-review code - flag
era or drop its convs at consumption); collector restart eras 58604,
62680, 63352, 63659, 63976 (live) - each saw ZERO client traffic (no
probes), flush at their stops; test boots 58173/58304 = lock-refused,
no records; smoke boot 58781 = dev capture (/tmp, excluded); ds4_test
processes = capture disabled by unset env (verified). Verdict: day-16
corpus integrity = 3c-era eras + 57826 merged-code era (disclose) +
clean tail.

- 02:26 **C8 OPTION-A VERIFICATION (post f3b17e6): snapshot path FIXED,
  delta-chain path REMAINS.** session-rewind+GLM_MTP: OK (the gate that
  broke C-merge). prefill-checkpoints/restore-reuse/session-snapshot/
  vision-suite: all OK. 18 failures ALL in kv-delta (2x :384 id-mismatch,
  16x :385 1e-5 logit drift) - SAME arm was 8x red pre-v4 (C5-rerun), so
  v4 regressed nothing; the per-LINK tail semantics of chained stores is
  the remaining seam (parent link writes/reads tail state differently
  than live chain-replay does). Review #1 (ses_e530d83b9) claim-2 traces
  exactly those runtime branches - fix waits for its verdict rather than
  stacking speculation.

- 08:26 **C9 GREEN (v1.1 = 12b929d): kv-delta(MTP) 18->OK, session-rewind
  (MTP) stays OK. All functional gates passed.**
- 08:29 **F7 boot-side SHALLOW-VERDICT + leak incident**: merged binary
  boots clean vs production KV (543 files visible, zero error lines in
  8s window) but real resume requires live traffic -> moved to landing
  checklist as 'live-resume watchpoint on first sessions after boot
  swap'. INCIDENT: window leaked its dev boot (argv starts '--ctx',
  pkill pattern 'ds4-server --model' missed it) -> restore blocked x2 ->
  PID-kill recovery; collector down 2.5 min total (3448 restored,
  ARGV-MATCH-FINAL, invalid=0, sentinel armed). RULE: kill engines by
  PIDFILE pid, never argv pattern.
- 08:30 Review #2 (merged-outcome, fresh context) dispatched on
  merge/upstream@12b929d. Branch = all gates green modulo the 4 landing-
  checklist items it will re-verify.

- 08:4x **REVIEW #2 (merged outcome, fresh context ses_e51bcce3d):
  PASS_WITH_MINORS.** Scope verified clean (75/90 files byte-identical
  to upstream tip; all 15 diverging = documented conflict set + auto-
  merge unions; both raw-count deltas traced to upstream supersessions,
  zero content loss; kvstore link dispatch sound: terminality from walk
  position, envelope size validated pre-payload, fail-soft fallbacks).
  Format sovereignty certified (single emission site, triple-accept
  reader, one predicate + one layout helper -> closure; no path writes
  what it cannot read). Evidence ledger verbatim-matched except two
  bookkeeping nits, and the reviewer LIVE-verified our post-incident
  state (pidfile 3448 + invalid=0) as the F7-restore evidence for the
  manifest (minor 3 - stopgateF7.log ends at the failed attempt).
  Minors 1+2 now in DS4FORK.md; minor 4 = future stop-tiers keep a full
  main-baseline suite log next to the merged one.
  LANE STATE: merge/upstream@12b929d is LANDED-READY. Landing (merge to
  main + boot swap) held for operator timing: seal window tonight (clean
  day-16 era story) or immediately (implies swap at the NEXT engine
  restart either way, since restart-* rebuilds from main). Rollback:
  revert -m 1 per batch + pre-swap binary kept as ds4-server.bak.

- 08:46 **LANDED a389c87 (merge/upstream@12b929d -> main), BOOT-SWAPPED:
  merged binary is the LIVE collector since 08:46:29 (PID 9717, ARGV-
  MATCH-FINAL, invalid=0, early-reject scan=0, sentinel re-armed).**
  Gates on main binaries: build 0w/0e, G5 clean, session-rewind(MTP) OK,
  kv-delta OK. dspark-acceptance: INCAPSULATED box-limitation - fixture
  references gguf/DeepSeek-V4-Flash-DSpark-support-0731.gguf which never
  existed on this box (pre-Vision-Exp era; target identical pre-merge);
  runtime DSpark health instead evidenced by boot + sentinel + live day-
  16 traffic. DAY-16 SEAL MANIFEST addendum: collector eras 3448 (old
  binary) ends 08:44:29; merged-code era 9717 begins 08:46:29 - second
  merged-code disclosure alongside 57826. Rollback: revert -m 1 a389c87
  + make (~4 min); no .bak exists.
