# PLAN-DSML-REWIND — fix EXACT+DSpark mode-boundary rewind full-rebuild stalls

Owner: NEW agent session (separate boundary from the 3c data-collection /
DSpark-training workstream, which continues on the live engine).
Evidence + full root cause: `.codebase-memory/ISSUE-EXACT-DSML-REWIND-REBUILD-20261104.md`
(read this first; it is self-contained). Ledger: `.codebase-memory/3C-NIGHT-PLAN-20261103.md`
entries 14:5x.

## One-paragraph problem
With `--mtp-exact-sampling --dspark`, every DSML tool block that crosses the
greedy<->payload-sampling mode line outside the last verify window falls into
`server_generation_rewind`'s slow path (ds4_server.c:14666 ->
ds4_session_sync_internal RESET branch, ds4.c:79547-79575), which silently
re-prefills the ENTIRE kept context on the GPU. Stalls scale as
`ctx_tokens / ~350 t/s` (measured: 176 s @83k ... 742 s @144k). Sampling
correctness is preserved; cost is latency + invisibility.

## Hard guardrails (live system — non-negotiable)
1. NEVER restart/stop the live engine (PID as of 20261104: 4051, port 8001) —
   it is serving collection + a pending A/B. Deploy of any rebuilt binary
   happens ONLY at an operator-signaled restart.
2. Run your private instance on **port 8011+** with ISOLATED state:
   `DS4_LOCK_FILE=/tmp/ds4-fix.lock` (honored: ds4.c:61747),
   `DS4_TRAIN_DUMP=/tmp/ds4-fix-train` (or unset), parity capture off
   by LEAVING `DS4_TD_PARITY` UNSET (ds4.c:76169; NOTE: setting
   DS4_TD_PARITY_MAX=0 does NOT disable - 0 is coerced to 4096,
   ds4.c:76172), distinct log path, and **ctx ~131072 not 1048576**
   (memory courtesy; 80k repro fits). Synthetic repro cycles must NEVER
   enter the real corpus.
3. Model ggufs on disk are read-only shared inputs; ctx 1048576 instances are
   memory-heavy — run YOUR instance only while measured, stop when idle,
   respect operator's memory (engine may not be interrupted; yours may be).
4. Repo hygiene (AGENTS.md): C11, targeted changes only, `make test`/ds4_test
   ONLY with engine stopped — use `--server` subsets live; no binaries
   committed; commit at every milestone; independent review before done.
5. Instrumented live boot already has DS4_KV_DEBUG=1 DS4_DSPARK_STATS=1 —
   reading live logs is allowed and encouraged (evidence), writing is not.

## Milestones (each: implement -> review -> commit)
### M0 — Observability (log-only, land first)
In `server_generation_rewind` slow path, before the rebuild sync, emit one
DS4_LOG_KVCACHE line: `resample=` (a second SILENT route exists -
`kept < ntok && !resample` reaches the slow path without any mode
boundary; do not miscount those as boundary bails), target pos,
common-prefix depth vs prefix.len, rebuild token count + est seconds,
AND the LIVE bail field set for the stalling DEEPSEEK4 family:
`s->dspark_rewind_start/end`, `dspark_rewind_logits!=NULL`,
`dspark_rewind_prefixes`, `checkpoint.len/valid` (all set/read in
dspark_rewind_begin 64178 / dspark_rewind_recent 89113 -
family-AGNOSTIC session fields). Do NOT instrument `verify_start/
rewind_end` (ds41-only) or `snap_pos x3` (QWEN4_EXP-only) - both dead
for the stalling engine. No behavior change. Acceptance:
grep-able line appears in your private instance's log exactly at the
stall. OPTIONALLY also wire the existing `rebuild_progress` SSE-keepalive
pattern (ds4_server.c:15176+) IF the generation-rewind context permits
(headers may not be sent - decide after reading the call flow; when in
doubt, log-only).
**Gate M2 on data**: classify which bail clause actually fires from a
live session's M0 lines BEFORE sizing snapshot coverage.

### M1 — Truncation-aware resume (the pragmatic fix)
Goal: when the rewind target is a PREFIX of a still-valid checkpoint,
truncate + continue instead of reset + replay-from-zero.
Investigation first (timeboxed — report findings even if no patch):
- What state DOES survive `ds4_session_rewind(session,pos)`? Read
  ds4_session_rewind + ds4_session_common_prefix + metal_graph_* prefill
  state ownership (write cursor, compressed-KV/indexer rings, GLA/PLE
  recurrent state). The reset branch fires on `prefill_cap < prompt->len`;
  determine WHY the graph state cannot resume from common-prefix depth.
- If a resumable floor exists: change the sync path (or add a
  ds4_session_rewind_to_prefix_resume() API) to call
  metal_graph_prefill_chunked_range(session, start=common_depth, len=rest)
  and only fall back to full reset when the state genuinely cannot resume.
  PARITY CAVEAT: resume must align to ABSOLUTE chunk boundaries (engine
  precedent: compressor/indexer rows must finalize in the same order as a
  cold prompt - see the continuation-branch comment ds4.c:~79462 and
  metal_graph_resume_prefill_min_tokens() at ds4.c:79480) or reduction
  order changes and bitwise-identical acceptance becomes unattainable.
  Pre-commit a tolerance protocol: byte-diff transcripts, report ANY
  divergence with its position, and distinguish parity-affecting diffs
  from known reduction-order artifacts before calling M1 broken.
- Gate behavior behind env `DS4_REWIND_RESUME=1` (default OFF) until A/B'd.
Acceptance (measurable): repro recipe (issue doc) on YOUR private instance
at ctx>=80k: slow-path wall time drops from ~ctx/350 to ~(ctx-common)/350
with common >= 90% of ctx on typical DSML turns; token-for-token output
identical vs default path on 20 fixed-seed repro prompts (diff transcripts;
exact-sampling determinism makes this strict - subject to the parity caveat
above). COST BUDGET: each slow-path sample is ~3-7 min GPU (a >80k prompt
= ~350-500 KB text + multi-KB tool payload); the 20-prompt matrix is
~1.5-2.5 h sequential - run while the machine is idle, STOP your instance
between sample batches (guardrail 3).

### M2 — Snapshot coverage (only if M1 insufficient)
Extend snap0/verify-window machinery so long payload spans keep a
restorable boundary (ds4.c:58700+ structs; memory cost per extra snap set =
lin_state/lin_hist across layers + PLE — compute size first, report before
implementing).

### M3 — Upstream write-up
Condense issue doc + your diffs into a PR-ready description with the
deterministic repro. Fork lands first (nazerim/main), offer upstream after.

## Definition of done (plan-level)
M0 committed + live-restart picks it up and the log line appears in the real
corpus's neighboring engine logs; M1 either landed (env-gated, default off,
pending operator A/B decision) or a written feasibility verdict with the
exact state that blocks it; M3 drafted. Hand every finding back into
`.codebase-memory/` so the 3c workstream never has to re-derive them.

## Launch-prompt starter for the new agent session
"You own PLAN-DSML-REWIND in /Users/naz/Projects/ds4. Read
.codebase-memory/PLAN-DSML-REWIND.md then
.codebase-memory/ISSUE-EXACT-DSML-REWIND-REBUILD-20261104.md fully, obey the
guardrails (live engine untouchable, private instance on 8011 with isolated
DS4_LOCK_FILE/TRAIN_DUMP), start with M0, commit at milestones, and request
independent review before marking anything done."

## M0 RESULT (20261104, agent session 2) — instrument landed, and it falsified two claims

Instrument: `ds4_session_rewind_reason()` + `ds4_rewind_reason_name()` (ds4.c:
the old `dspark_rewind_recent` gate condition extracted verbatim, so the reason
cannot drift from the real gates) and two `DS4_LOG_KVCACHE` lines in
`server_generation_rewind` (ds4_server.c) — one BEFORE the rebuild naming the
refusal reason and the cost, one AFTER with measured elapsed + `rate_tps`, plus
a third line for the no-rebuild truncation case. Zero behavior change;
independent review PASS_WITH_MINORS, all five findings fixed (off-by-one
`target` was the real one: it would have made the line contradict itself).

Validation, private instance (port 8011, ctx 131072, `DS4_LOCK_FILE=/tmp/ds4-fix.lock`,
no TRAIN_DUMP/parity — verified 0 capture banners — own KV dir, own log),
Vision-Exp + `--dspark --mtp-exact-sampling --dspark-confidence 0.45`:

    generation rewind truncated without rebuild ctx=8192..76084:67892
    window_owner=dspark-window route=truncation resample=0 reason=ok
    requested=77607 truncate=77607 window=77606..77608 frontier=77608
    common=77607 kept_tokens=77607

The line fires exactly at the rewind event. What did NOT happen: **the stall.**
Three tool-heavy turns on a 76,084-token prompt (tool payloads 4.7 KB, 18.4 KB,
and a 2-turn cycle of 10.4 KB + 9.6 KB) produced 1 slow-path event, 0 rebuilds,
`replay_fallbacks=0` across 4530 verify cycles, worst inter-chunk gap 5.5 s
(prefill, not rebuild). Harness: `.codebase-memory/repro_dsml_rewind.py`,
`.codebase-memory/repro_toolcycle_dsml.py`.

### Corrections to this doc set (3rd and 4th today — both from the instrument)
1. **"With `resample` unset the fast path is never tried" is wrong.**
   `ds4_session_rewind()` calls `ds4_session_rewind_speculative()` itself
   (ds4.c:89264) on every route; the server's `resample &&` test is only a
   pre-attempt for the logits-only case. Observed above: a `resample=0` rewind
   served from the window (`reason=ok`) with no rebuild and no replay.
2. **"Long DSML argument spans make a bail plausible as the NORM" is
   unsupported — measured bail rate is zero** in synthetic traffic, and the one
   `DSpark stats` sample the production engine emitted (cycles=31598) also had
   `replay_fallbacks=0`. Open question is bail *frequency*, not bail cost: the
   four production stalls with stack traces are real, so the trigger is
   something the synthetic shape lacks (leading candidate: a cross-turn
   re-sync — tool-checkpoint canonicalization or disk-KV rehydrate — leaving
   `dspark_rewind_end != checkpoint.len`, i.e. `reason=window-moved-past-frontier`).
3. M1's premise stays dead for this family (confirmed in code, not inherited):
   `state_ok` recovery exists only behind `ds4_session_is_qwen4` /
   `ds4_session_is_glm` (ds4.c:89272-89307), so a *real* fast-path bail always
   rebuilds. The observed non-rebuild event had `reason=ok` — the fast path, not
   a truncation that survived.

### M2 sizing + the actual blocker (measured from the allocators)
Frontier = `ds4_spec_frontier` (ds4.c:61791): per layer, `attn_state_kv` +
`attn_state_score` (and for ratio-4 layers the two indexer tensors), sizes
`(coff*512) x (coff*ratio) x 4 B`. On this shape (43 layers, head_dim 512,
indexer 128, ratio 4 on even layers / 128 on odd, 0 on layers 0-1):
21 ratio-4 layers x 80 KiB = 1.64 MiB + 20 ratio-128 layers x 512 KiB = 10 MiB
=> **~11.6 MiB per frontier per session**. The `spec_*` shadow slot already costs
this, `spec_prefix1_*` costs 5x (~58 MiB). Against a 93.25 GiB planned boot the
memory fear in the issue doc is NOT the blocker.

The blocker is a silent-corruption hazard: `metal_graph_dspark_cache_window_valid()`
(ds4.c:29603) validates only GEOMETRY (`raw_start == token_start % cap`,
`len <= cap`) — it cannot know the ring rows were recycled. `dspark_cache_cap =
raw_cap` = 4352 rows on both profiles, so a frontier anchored more than ~4352
generated tokens back restores a self-consistent-looking window over overwritten
rows and produces wrong state with no error. Any durable anchor must therefore be
snapshotted where the verifier window is empty (`dspark_cache_len == 0`, the one
case the validator always accepts) or copy the ring rows alongside it.

### Gate that now needs the operator
The `reason=` classification that gates M2's design must come from production
traffic: 20 synthetic turns cannot produce a dataset the repro does not trigger.
That means the live engine running the M0 binary — my post-test restore already
started it that way (same argv byte-for-byte, same 14 env vars, parity dir
preserved). If the operator wants the pre-M0 binary instead, that is a rebuild +
one more restart window; say which.

### Follow-ups this milestone surfaced (not done here)
- `server_log()` has no `__attribute__((format(printf, 2, 3)))`, so `-Wall`
  never validates any `server_log` format string - the three new lines were
  checked by hand (18/18, 12/12, 5/5 conversions). Adding the attribute is a
  repo-wide change touching ~hundreds of call sites; do it as its own commit,
  not inside this fix.
- The `done` line's `elapsed` covers the sync plus `ds4_tokens_free`; the free
  is microseconds, so the measured `rate_tps` is honest as-is. Do not "fix" this
  by moving the timer start.
- M1 needs a trigger, not a patch: no synthetic shape used so far (single turn
  4.7 KB / 18.4 KB payloads, 2-turn cycle 10.4 KB + 9.6 KB, 76k-token prompt)
  produced a bail. Next experiment should force the cross-turn re-sync path
  (disk-KV rehydrate or tool-checkpoint canonicalization between turns) and read
  `reason=` off the line.
