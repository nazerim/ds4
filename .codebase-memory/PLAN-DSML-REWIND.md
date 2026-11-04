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

### M0 LABEL CAPTURED - production, first real rebuild (1104 19:28:26)

    generation rewind rebuild ctx=210468..210889:421 window_owner=dspark-window
    route=mode-boundary resample=1 reason=no-verify-window
    requested=212547 truncate=212546 window=212547..0 window_max=16 prefixes=4
    logits=1 frontier=212551 checkpoint_valid=1 common=0 kept_tokens=212546
    est_s=607 exact=1

Read it carefully, because it kills the doc's guess and points at a much
cheaper fix than M1/M2 as scoped:

- `reason=no-verify-window`, NOT `window-moved-past-frontier` and NOT
  `target-behind-window`. The gate that fired is
  `dspark_rewind_end <= dspark_rewind_start`, with `window=212547..0`:
  **start is live (212547) but end is 0.**
- The restore state needed was still present: `logits=1`, `prefixes=4`,
  `checkpoint_valid=1`, and the target `requested=212547` equals
  `window_start` exactly - the cheapest possible restore (prefix 0, memcpy the
  saved boundary logits). Instead 212,546 tokens were re-prefilled.
- Why end is 0 while start isn't: `dspark_rewind_begin()` (ds4.c:64179) sets
  `end = 0` up front and the window is only armed by `dspark_rewind_keep()`
  (ds4.c:64193, `end = checkpoint.len`). There are **five** keep sites
  (ds4.c:84046, 84122, 84571, 84922, 84960) but the verify paths have more
  exits than that. Two of them commit tokens and return **without arming the
  window**:
    - the prefix-extended commit (ds4.c:84134-84192): rolls back to `start`,
      commits `DS4_SPEC_PREFIX_SLOTS + 1` drafts, `spec_frontier_free`,
      `return n_accept` - no keep.
    - the reject-then-replay commit (ds4.c:84193 onward): restores the frontier
      at `start`, replays `replayed_drafts` tokens one by one,
      `return n_accept` - no keep either.
  After either exit, `dspark_rewind_start/prefixes/logits/dspark_rewind_frontier`
  still describe the old block while `checkpoint.len` has moved on, and `end`
  stays 0. `frontier=212551 = start+4` in the line above is exactly that
  residue.
- Which of the two exits fired is derivable from the same line, and it is NOT
  the prefix-extended one: `prefixes=4` means `dspark_rewind_begin` got
  `rows = draft_n = 5`, and that exit only fires at `commit_drafts ==
  DS4_SPEC_PREFIX_SLOTS + 1 == 6`, impossible for a 5-token block. A partial
  commit of 4 of 5 would have gone through the direct-partial path, which DOES
  keep (ds4.c:84122) - so reaching a non-keeping exit with 4 tokens committed
  means `prefix_ok` failed there and control fell through to the
  reject-then-replay path (ds4.c:84193+), which replays the accepted drafts
  with one-token evals and returns without ever arming the window. So the
  sticky state can be entered by a *single* failed row read / prefix commit,
  and once entered it persists.
- Counter gap worth closing while M2 is designed: `replay_fallbacks` is
  incremented only in the prefix-extended path (ds4.c:84168), not in the
  reject-then-replay path that actually strands the window here. Both the
  private-instance run (4530 cycles) and the production stats sample read
  `replay_fallbacks=0` - which is consistent with the prefix-extended path never
  firing and inconsistent with "no fallback happened". Attributing session
  stickiness from stats alone is therefore unsafe today; the M0 line is the only
  instrument that sees it.
- Consequence = the sticky ladder from the census. Once a session leaves the
  keeping paths, every later mode-boundary resample sees
  `end <= start` -> `no-verify-window` -> full rebuild, until some block happens
  to commit through a keeping path again. A session that lives in
  partial/reject territory (EXACT sampling at temperature > 0, DSML payload
  tokens the drafter keeps losing) never re-arms: one rebuild per turn, with
  cost rising as ctx rises - which is precisely the
  `99,856 -> 106,934 -> ... -> 201,327` staircase, and tonight's 212k turn at
  ~607 s estimated.

### Measured outcome of that rebuild (19:38:28)

    generation rewind rebuild done ctx=210468..210889:421 kept_tokens=212546
    elapsed_s=601.4 rate_tps=353 rc=2

- `est_s=607` vs actual `601.4 s`: the 350 t/s estimate constant was accurate to
  0.9% at 212k ctx.  The census median of 407 t/s is the mid-depth value; depth
  really does slow prefill (195-600 band, ~353 t/s here), so keep the constant
  conservative rather than retuning it to 407.
- `rc=2` is `DS4_SESSION_SYNC_INTERRUPTED` (ds4.h:447), and the next log line is
  `chat ctx=210468..210889:421 client disconnected`.  So 601 s of GPU produced
  **nothing** for the user: the client gave up while the rebuild ran, the turn
  died, and the retry after it prefilled only 2,197 tokens in 10.2 s (the partial
  checkpoint survived, as ds4.h:427 documents).  The cost of this bug is not only
  latency - at deep ctx it converts working requests into failed ones, and the
  wasted work is invisible unless the rc is printed.

### Stickiness: measured twice, and it disproved my own correction
I told the operator a stranded session is "stuck for the rest of its life"; then I
"corrected" that to recurrent-not-terminal because 11 `reason=ok` entries appeared
after the first rebuild. Both readings were wrong in the same way - the truth is
worse and it took a second live sample 63 s later to show it:

    19:39:31 generation rewind rebuild ctx=211941..212138:197 route=mode-boundary
    resample=1 reason=no-verify-window requested=212526 truncate=212525
    window=212525..0 window_max=16 prefixes=4 logits=1 frontier=212530
    checkpoint_valid=1 common=0 kept_tokens=212525 est_s=607 exact=1

Same session, same turn, second full rebuild of 212,525 tokens - so the window
does NOT stay re-armed; it gets stranded again within the turn.  The census
already contained this shape (20261103 20:26:47 and 20:29:57: two stalls ~3 min
apart inside one turn).  Pattern per sample: `prefixes = draft_n - 1`, `end = 0`,
`frontier - start = tokens committed since begin` (4 then, 5 now).

That second arithmetic is what pins the exit.  With `draft_n=5` and 5 tokens
committed, the direct-full path would have kept, and it only skips keeping when
`final_logits_ok` is false; the partial path needs `commit_drafts < draft_n` (no)
and the prefix-extended path needs `commit_drafts == 6` (impossible).  So control
reaches the rollback-then-replay tail (ds4.c:84193+), which restores the frontier
at `start`, replays the accepted drafts one token at a time, and returns
`n_accept` **without ever arming the window**.  `dspark_verify_read_row()`
(ds4.c:~84240) is the failure that routes there - `metal_graph_read_spec_logits_row`
returning false for row `draft_n-1`.

Why calling `dspark_rewind_keep()` on that tail is *probably* correct, and what
M1' must still prove: after the replay, the ring rows covering `[start, end)`
were written by the replay itself in order, and `s->dspark_rewind_frontier` (the
snapshot taken at `begin`) still describes `start`, which is precisely the
"restore at start, replay up to pos" contract `dspark_rewind_recent()` implements
("at most one verifier block of replay").  Unproven parts to review: TP worker
commit ordering on that path, whether the capture invalidation that already runs
there is compatible with an armed window, and why the row read fails at all (a
cap, a wrap, or a transient - if it is systematic at depth, the strand is not
rare and every deep session pays it).
- M1 (truncation-aware resume from a common prefix) is the WRONG target: the
  engine never got a chance to use any prefix state, because a *liveness flag*
  was zero. Nothing about DEEPSEEK4 truncation needed to be solved here.
- M2 as scoped (extra snapshot coverage / memory sizing) is also not the first
  move: the state to serve this rewind was already in hand (`logits=1`,
  `prefixes=4`, target == window_start). The ~11.6 MiB question is moot for the
  observed failure.
- New M1' (minimal, to be reviewed, not assumed): arm the window on the two
  exits that skip `dspark_rewind_keep()`. It looks semantically sound because
  `dspark_rewind_recent()` is *built* to restore at `start` and replay the few
  tokens up to `pos`, and the gate already caps the span at
  `DS4_DSPARK_MAX_BLOCK_SIZE`. It must be reviewed for the TP worker path
  (`ds4_tp_send_verify_commit` ordering) and for capture invalidation
  (`ds4_session_dspark_capture_invalidate` already runs in those paths) before
  it is trusted - a wrong `end` would claim coverage the ring may not have.
- First M1' step before any patch: add the counter/diagnostic that would prove
  the diagnosis, i.e. count the reject-then-replay exit that commits tokens
  while leaving `dspark_rewind_end == 0` (a "window stranded" counter next to
  the existing stats, plus its `DS4_DSPARK_STATS` field). Without it the fix is
  unfalsifiable: today `replay_fallbacks=0` and a stranded session are
  indistinguishable in the stats, and only a rebuild line reveals the state.
- New M2' (fallback if M1' is unsound): let the gate serve `pos ==
  dspark_rewind_start` when `checkpoint.len - dspark_rewind_start` is small,
  restoring + replaying those few tokens instead of the whole context. Note the
  hazard already documented: the window validator checks geometry only, so any
  relaxation needs an explicit bound on how far the ring may have moved.
- Frequency correction stands the other way from the earlier note: bails ARE
  rare per-turn in healthy sessions (9/9 window-served before this one) but
  *once a session falls out of the keeping paths they become per-turn*, so
  neither "never" nor "the norm" was right - it is bimodal per session.

### Deployment state (20261104 20:1x)
`DS4_REWIND_RESUME=1` is live on the engine (PID 9304 since 20:13:19, argv byte-
identical to the pre-deploy boot, all 14 original env vars preserved, verified by
process inspection). Plumbed into `ds4-server.sh` so a restart cannot silently
lose it:
- `arm_collect_env` (start-collect / restart-collect) exports it, defaulting to 1,
  and prints `DS4_REWIND_RESUME=...` in the collect-arm banner beside the other
  capture settings.
- The DeepSeek profile branch of `start_server` also defaults it to 1, so a plain
  `restart-vision-dspark` keeps the fix. It is deliberately not set for the Qwen
  profile: only the DEEPSEEK4 driver arms this window (review confirmed the gate
  is inert for qwen4/GLM/DS4.1), so it would be noise there.
- Rollback is one variable, both arms verified:
  `DS4_REWIND_RESUME=0 ./ds4-server.sh restart-collect` -> stays 0 through the
  profile default, restoring the previous engine's decisions exactly.
- TP note for any future multi-rank boot: leader and worker are separate
  processes; they must get the same value or their windows diverge and every
  mirrored rewind invalidates.

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

### Historical stall census (same day, mined from `log/ds4.log*` — no GPU needed)
Method: a rebuild appears in the ordinary decode progress line as one 50-token
chunk whose measured rate collapses (`decoding chunk=<rate> t/s` with rate
< 1 t/s at 30 t/s normal). Parse those lines, subtract the 50 tokens' normal
cost, and compare `ctx / stall_seconds` against the prefill band.

    stall chunks (rebuilds): 24      GPU time lost: 150.1 min
    median stall 311 s       max 832 s
    implied rebuild rate = ctx/stall_s: median 407 t/s, p10 226, p90 478 (n=24)
    stall ctx 49,164 - 201,327;  gen at stall 100 - 6,800
    worst single boot (log ending 18:33 today): 98.9 min lost

Why this is the strongest evidence in the doc set: `ctx/stall` lands inside the
independently measured prefill band (350-600 t/s at 50k-200k ctx) for 24/24
events, so every one of them is a full-context re-prefill - derived from
ordinary logs, not from an interpretation of a stack trace. Shape of each event:
one slow 50-token chunk then an immediately normal chunk (one rebuild per
crossing, decode resumes), always inside `TOOLS DSML_START`, several also
stalling on the chunk that closes the block (`DSML_END`) - i.e. both mode-line
crossings cost a rebuild. Some turns stall twice ~3 min apart (repeated bails).

Correction to M0's estimate constant: measured median is 407 t/s, so
`SERVER_REWIND_REBUILD_TPS 350.0` overestimates duration by ~15% - conservative,
left as-is deliberately.

Trigger candidate sharpened from the same mining: the stalling turns are
continuation turns that began from a reused frontier (one case prefilled 8
tokens because the previous turn's checkpoint was live; another hit disk KV at
40,960 tokens) - consistent with `reason=window-moved-past-frontier`, which is
exactly what the deployed line will label on the next real occurrence. Harvest
it passively: the instrument is in the running binary, so no synthetic traffic
and no extra restart is needed to get the label.

(This prediction was WRONG, and the record stays: the first real rebuild that
evening came from a turn that had just prefilled 80,612 tokens onto a live
checkpoint with no cache event at all, and the label was
`reason=no-verify-window` - a dead liveness flag, not a moved window. Lesson
for M2: the census can correlate cost with ctx, but only the instrument can
name the trigger.)
