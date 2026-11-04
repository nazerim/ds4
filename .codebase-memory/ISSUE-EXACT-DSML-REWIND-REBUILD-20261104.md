# ISSUE: EXACT+DSpark mode-boundary rewind falls back to full-context re-prefill (multi-minute silent stalls)

Status: ROOT-CAUSED 20261104 (live incident day: repeated "server stall"
reports). Fork-local fix in progress before upstreaming.
Owner refs: ds4_server.c generation loop, ds4_session_rewind_speculative
(ds4.c), ds4_session_sync_internal reset branch (ds4.c:79564).

## Symptom
- Tool-bearing (DSML) agent turns freeze mid-generation for minutes with
  NO log output, then resume and complete normally. Engine never dies.
- Observed durations scale with context size. Measured law:
  `stall_seconds ~= context_tokens / ~350 t/s` (GPU prefill rate).

| evidence (20261104)        | ctx      | stall   | ctx/rate |
|----------------------------|----------|---------|----------|
| morning "282s request"     | ~117k    | 282 s   | 331 t/s  |
| fresh session mid-DSML gap | ~83k     | 176 s   | 471 t/s  |
| 90k session, sampled live  | ~90k     | ~150 s  | ...      |
| compacted session gen 2100 | 106,950  | 302.5 s | 353 t/s  |
| big session rebuild        | 144,692  | 742 s   | 195 t/s  |

## Stack (captured 4x independently during stalls, 100% of samples)
generate_job_inner (ds4_server.c:16736)          <- server_generation_rewind call
  -> server_session_sync (14517)
    -> ds4_session_sync (ds4.c:78345)
      -> ds4_session_sync_internal +2528 (ds4.c:79564)   <- RESET branch
        -> metal_graph_prefill_chunked_range -> waitUntilCompleted (GPU busy)

## Mechanism (code chain)
1. EXACT sampling (`--mtp-exact-sampling`) re-samples the boundary token
   whenever a verified block crosses the greedy <-> payload-sampling
   mode line (ds4_server.c:16723: `next_greedy != greedy_tool_syntax ->
   resample = true`). DSML tool blocks cross this line constantly
   (structural tokens greedy, argument payloads temperature-sampled).
2. -> `server_generation_rewind` (ds4_server.c:14666). Fast path =
   `ds4_session_rewind_speculative` restores from the verifier snapshot
   set (snap0/snap1/snap2 machinery; the comment at ds4.c:58719
   explicitly warns that missing snapshots mean "replays the whole kept
   context").
3. Fast path BAILS when the rewind target is outside the last verify
   window: `pos < d->verify_start || pos >= d->rewind_end`
   (ds4_session_rewind_speculative, ds4.c). Long DSML argument spans
   advance the verify window past the block-start boundary -> bail is
   the NORM for DSML turns, not the exception.
4. Slow path: `ds4_session_rewind(session,pos)` +
   `rebuild = ds4_session_common_prefix(...) != prefix.len` ->
   `server_session_sync(prefix)` -> prompt is SHORTER than the live
   checkpoint / prefill_cap -> `ds4_session_sync_internal` RESET branch
   (ds4.c:79547-79575): `checkpoint_valid=false`, capture invalidated,
   graph state reset, FULL re-prefill of the entire truncated prompt.
5. The rebuild emits NO progress and NO log (unlike the "tool checkpoint
   rebuild" path at ds4_server.c:15179+, which logs phase + duration and
   wires SSE keepalive). Client sees pure silence = "stall".

## Why everything else seen today fits
- Same-pin-twice (204,800 on the 344k session): pin = first DSML commit
  position; every later boundary sits behind the verify window there.
- Oscillation (clean 617-token turns between rebuilds): turns whose
  block stayed INSIDE the verify window take the fast path.
- Disk-KV rehydrate and budget were NOT the problem (full stores hit;
  eviction ruled out; post-compact session healthy until its first big
  DSML span).
- Data stays CORRECT (exact semantics preserved by resample; the cost
  is latency only) - collection is safe to continue.

## Repro recipe (deterministic)
1. Vision-Exp collect profile (`--dspark --mtp-exact-sampling
   --dspark-confidence 0.45`), any client with tools enabled.
2. Prompt > ~80k tokens (for visible pain), one agent turn with a LONG
   tool-call argument block (multi-KB write/shell payload) at
   temperature > 0.
3. Watch: decode halts mid-DSML_START for ~ctx/350 seconds, then
   completes; DS4_DSPARK_STATS=1 shows replay_fallbacks incrementing.

## Fix options (ranked)
1. REAL WIN: extend speculative-snapshot coverage so DSML spans keep a
   restorable window (per-payload-block snapshots; costs GPU memory for
   the snap sets across 43+ layers - needs sizing).
2. PRAGMATIC: truncation-aware sync: when the incoming prompt is a
   PREFIX of a still-valid checkpoint, truncate+resume
   (metal_graph_prefill_chunked_range from the surviving common prefix)
   instead of reset+replay from 0. Requires graph prefill state to
   survive truncation - feasibility under investigation.
3. NOW (v1, this commit): observability - one log line in
   server_generation_rewind naming the slow path, the common-prefix
   gap, and the rebuild token count; so silence becomes "known rebuild,
   ~M/350 s expected". Zero behavior change.

## Upstream posture
Fork-local first (per DS4FORK.md workflow), then offer upstream with
the repro + this analysis. Interim operator guidance: stalls scale with
context - compact early / keep giant sessions tool-light is NOT needed
for correctness, only for latency today.
