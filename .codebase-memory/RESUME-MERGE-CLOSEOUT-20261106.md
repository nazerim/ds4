# RESUME — merge campaign closeout (20261106 ~18:15) — READ FIRST
Authoritative live state for any session starting now. Night-history detail
lives in the docs listed under POINTERS; this file is the map.

## Live state
- **main == nazerim/main == `fc2b7e3`** (clean tree). HEAD region carries
  the landed upstream merge `a389c87` (234 commits through origin/main
  b3f50180 as batches A/B/C + Option-A V4 payload format; rollback =
  `git revert -m 1 a389c87` + `make`, ~4 min; merge worktree
  `/Users/naz/Projects/ds4-merge` kept as anchor ~2 days).
- **Engine: PID 49076, merged code, NON-collect mode** (capture env
  absent - verified). Serving on 8001 since 17:45:39 under new defaults:
  `DSPARK_EXACT=1` + `DS4_REWIND_RESUME=1` are now family defaults
  (script `acec7ed`/`:354`); `exact: 1` verification = terminal banner +
  argv `--mtp-exact-sampling`, NEVER log grep. One rewind sentinel armed.
- **Standing data collection CLOSED.** day-16 sealed TRUNCATED
  (17:45; 42,791 recs, chain 100%, disclosures incl. 57826 boot-era-only
  + 9717 merged-code era), backed up checksum-clean, `active/` recreated
  empty. Re-enable = boot via `start-collect` explicitly (chain pattern,
  AGENTS/ds4-server.sh); do NOT resume standing capture unprompted.
  No day-17 seal is owed unless collection reopens.
- **Upstream merge debt = 0.** Steady state: weekly
  `git merge-tree --write-tree main origin/main` probe; batch at first-parent
  slices (NEVER calendar dates - upstream commit dates are not topologically
  monotonic, that SMASHed plan v1); per-batch gates in a worktree.

## Campaign conclusions (do not re-litigate; evidence banked)
1. **DSpark conf-head line CLOSED - final epitaph:** retrained heads
   (confcal3, val AUC +2.25pp) transfer to live EXACTLY as calibrated
   (6.0-9.8% row drops = 13.0% proposal-level at 32k+ after prefix
   conversion, inside the sim's 12.8-14.6%); the falsified link is
   coverage->wall-time: EXACT pays a fixed full-model forward per CYCLE
   (verify is width-adaptive, ds4.c:86100), savings slope at measurement
   floor, and higher coverage inflates cycle count (+4-10% projected
   net). **Any retry must target verify-side economics, not gate
   thresholds/coverage.** Full arithmetic + review chain:
   PLAN-DSK-CALIBRATION-AUDIT-20261106.md (its AUDIT RESULTS is the
   corrected version; interpretation review confirmed-with-edits and the
   earlier "denominator illusion"/"quality-neutral" wordings are dead).
2. **V4 payload format** (0x51573804 = sliced spans + pending-trunk-row
   tail; V2 remains the tail-less default; V1/V2 permanently readable):
   rules + exclusivity guarantee in DS4FORK.md - vanilla-upstream
   binaries must NEVER point at ds4-kv-deepseek (shared tag 0x51573803,
   layouts differ; both cross-reads fail closed, rule is procedural).
3. **Stall class (DSML rewind): CLOSED in production** - now default-on
   via script; sentinel covers the two rare geometries (report if either
   moves off 0).

## Binding operating rules (all learned the hard way tonight)
- Engine liveness/kill ONLY via PIDFILE pid (`ds4-server.pid`), never
  `pgrep -f` argv patterns (watchdog self-match faked a drain timeout;
  leaked boot with `--ctx`-first argv evaded argv-pattern kill).
- Restore traps: `cd` to the repo absolutely; verify boot via the
  4-point recipe (argv diff, env via `ps eww`, log-line-1 invalid=0,
  terminal banner flags).
- Test suites: parse `ds4 tests:` summary line - **exit 0 lies** (rc=0
  with '15 failure(s)'). Qwen3.8 fixture tier needs `DS4_TEST_MODEL=
  gguf/Qwen3.8-Flash-Next-Q4.gguf DS4_TEST_GLM_MTP=1 [DS4_TEST_MTP_EXACT=1]`
  and is the ONLY way to exercise Qwen Metal/payload paths (device .metal
  is JIT - host build cannot see it; one bad kernel = whole library dead).
- Builds: warning counts only from a FORCED rebuild (rm .o); symbol-count
  anchor sweeps miss dead-but-present code (prefetch feature vanished to
  auto-merge while its symbols stayed - `-Wunused` was the sensor).
- Windows: chain trap-restore + per-step `run_capped` timeouts + detached
  FILE-BASED watchdog checking pidfile; heartbeat shells are single-shot -
  re-arm while any unattended window is open (none now).
- zsh traps (null_glob, no word-split), no probes on collecting/live
  engines, secrets never echoed (proxy key rotation still OPERATOR-OWED,
  see memo).

## Outstanding handoffs (queued, none urgent)
- **KV lane:** refresh #1020 (`feat/kvstore-tokenizer-fingerprint`,
  worktree ds4-pr1020) + #1022 (`feat/vision-image-budget`) onto landed
  main - known 4-file conflict map in PLAN-UPSTREAM-MERGE execution log
  (PR-STATUS doc has full state; #1163/#785 left MERGEABLE deliberately).
- **Engine lane:** F1 gate before any TP/--batched-session (their
  HANDOVER priority list); stale mock in tests/test_dspark_rewind.py
  (red on main since Nov-3/4 instrumentation, mock-only fix);
  PARITY-DUMP stale-row bug + wrong comment (ds4_td_parity_note, one-liner
  fix, see NOTES-BENCH-SIDE item 9); re-tighten relaxed ds4_test.c:887
  post-F7; unfiled upstream Metal-threading question = operator call;
  dormant stale test_qwen4_mtp_prefill marked in DS4FORK.
- **Operator:** DS4_API_KEY rotation (value appeared in tool output
  Nov 5); day-17+ engine restarts now serve merged code by default -
  first live-resume of V4-tagged checkpoints = watch sentinel +
  `payload size does not match` stays 0; `/tmp/stopgate*`/`sealS1`/
  `landL1`/`build-*` logs = ephemeral evidence, sweep after a week.
- **Verify-side economics reopen** (only DSpark speed door): per-position
  marginal verify cost model; substrate could reuse the sealed slabs
  (keep-flagged in dispositions); upstream #1179/#1149 worth re-testing
  when they move.

## POINTERS
- Merge campaign: `.codebase-memory/PLAN-UPSTREAM-MERGE-20261106.md`
  (plan v2 + complete execution log incl. every incident + era tally +
  review dispositions). PR states: `PR-STATUS-20261106.md`.
- DSpark audit: `PLAN-DSK-CALIBRATION-AUDIT-20261106.md` (method v2 +
  CORRECTED results + dispositions); script `trainer/dspark/
  audit_recal_20261106.py`; JSONs in /tmp + copies in
  `/Volumes/FireCuda520/ds4-train/labels3c/`.
- 3c campaign history/epitaph: `RESUME-3C-20261106.md` (updated; its
  pending list is DONE) + `3C-NIGHT-PLAN-20261103.md` (append-only
  ledger, ends at AUDIT INTERPRETATION CORRECTION block).
- Engine behaviors learned: `NOTES-BENCH-SIDE-ENGINE-BEHAVIOR-20261105.md`
  (+item 9). Engine-lane map: `HANDOVER-DSPARK-LANE-20261105.md` (dated -
  engine state stale, work list current). Fork notes: DS4FORK.md.
- Boot recipe: snapshot at $TMP/ds4-live-env.snapshot.txt (+
  $TMP/ds4-live-argv.txt reference); non-collect = source snapshot,
  `unset DS4_TRAIN_DUMP DS4_TD_PARITY DS4_TD_PARITY_MAX`,
  `./ds4-server.sh restart-vision-dspark`.
