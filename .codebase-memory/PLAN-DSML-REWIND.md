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
   `DS4_LOCK_FILE=/tmp/ds4-fix.lock`, `DS4_TRAIN_DUMP=/tmp/ds4-fix-train`
   (or unset), `DS4_TD_PARITY_MAX=0`/unset parity dir, distinct log path.
   Synthetic repro cycles must NEVER enter the real corpus.
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
DS4_LOG_KVCACHE line: target pos, common-prefix depth vs prefix.len, and
rebuild token count + est seconds (`/350`). No behavior change. Acceptance:
grep-able line appears in your private instance's log exactly at the stall.
Optionally also wire the existing `rebuild_progress` SSE-keepalive pattern
(ds4_server.c:15179+) IF the generation-rewind context permits (headers may
not be sent — decide after reading the call flow; when in doubt, log-only).

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
- Gate behavior behind env `DS4_REWIND_RESUME=1` (default OFF) until A/B'd.
Acceptance (measurable): repro recipe (issue doc) on YOUR private instance
at ctx>=80k: slow-path wall time drops from ~ctx/350 to ~(ctx-common)/350
with common >= 90% of ctx on typical DSML turns; token-for-token output
identical vs default path on 20 fixed-seed repro prompts (diff transcripts;
exact-sampling determinism makes this strict).

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
