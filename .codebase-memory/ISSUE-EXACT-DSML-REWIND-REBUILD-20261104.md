# ISSUE: EXACT+DSpark mode-boundary rewind falls back to full-context re-prefill (multi-minute silent stalls)

Status: ROOT-CAUSED 20261104 (live incident day: repeated "server stall"
reports). Fork-local fix in progress before upstreaming.
Owner refs: ds4_server.c generation loop, ds4_session_rewind_speculative
(ds4.c), ds4_session_sync_internal reset branch (ds4.c:79564).

## Symptom
- Tool-bearing (DSML) agent turns freeze mid-generation for minutes with
  NO log output, then resume and complete normally. Engine never dies.
- Observed durations scale with context size. Measured law:
  `stall_seconds ~= context_tokens / prefill_rate`, prefill_rate
  observed 195-600 t/s (geometric mid ~350; longer contexts run
  slower per chunk - attention grows).

| evidence (20261104)        | ctx      | stall   | implied rate |
|----------------------------|----------|---------|--------------|
| morning "282s request"     | ~117k    | 282 s   | 415 t/s      |
| fresh session mid-DSML gap | ~83k     | 176 s   | 471 t/s      |
| compacted session gen 2100 | 106,950  | 302.5 s | 353 t/s      |
| big session rebuild        | 144,692  | 742 s   | 195 t/s      |

## Stack (captured 4x independently during stalls, 100% of samples)
generate_job_inner (ds4_server.c:16736)          <- server_generation_rewind call
  -> server_session_sync (14517)
    -> ds4_session_sync (ds4.c:78345)
      -> ds4_session_sync_internal +2528 (ds4.c:79564)   <- RESET branch
        -> metal_graph_prefill_chunked_range -> waitUntilCompleted (GPU busy)

## Mechanism (code chain)
1. EXACT sampling (`--mtp-exact-sampling`) re-samples the boundary token
   whenever a verified block crosses the greedy <-> payload-sampling
   mode line (ds4_server.c:16724-16727: `next_greedy !=
   greedy_tool_syntax -> resample = true`). DSML tool blocks cross this
   line constantly (structural tokens greedy, argument payloads
   temperature-sampled).
2. -> `server_generation_rewind` (ds4_server.c:14666). Fast path =
   `ds4_session_rewind_speculative`: ds41-family branch (verify_start/
   rewind_end fields) and the snap0/1/2 block inside ds4_session_rewind
   are BOTH model-family-gated (DEEPSEEK41 / QWEN4_EXP) and DEAD for
   the stalling engine (DeepSeek-V4-Flash = DEEPSEEK4 family). The live
   fast path for us is the unconditional tail (ds4.c:89197) ->
   `dspark_rewind_recent` (ds4.c:89113), reading generic session
   fields set by `dspark_rewind_begin` (ds4.c:64178) in the shared
   DSpark verify: dspark_rewind_start/end, rewind_logits,
   rewind_prefixes, rewind_frontier (a session-owned spec-frontier
   snapshot buffer - candidate substrate for a family-correct fix).
3. Fast path BAILS when any gate fails: logits missing;
   `dspark_rewind_end != checkpoint.len` (anything committed since the
   last verify); `pos` outside [start, end); window span >
   DS4_DSPARK_MAX_BLOCK_SIZE=16 (ds4.c:2914). CRITICAL AMPLIFIER:
   DEEPSEEK4 sessions have NO snapshot fallback after the bail -
   ds4_session_rewind's state_ok=true paths are qwen4-only and
   glm-only, so our family always truncates + invalidates ->
   every single bail becomes a full rebuild. Which gate fires first is
   UNPROVEN - M0 measures it.
4. Slow path: `ds4_session_rewind(session,pos)` +
   `rebuild = ds4_session_common_prefix(...) != prefix.len` ->
   `server_session_sync(prefix)` -> prompt is SHORTER than the live
   checkpoint / prefill_cap -> `ds4_session_sync_internal` RESET branch
   (ds4.c:79547-79588): `checkpoint_valid=false`, capture invalidated,
   graph state reset, FULL re-prefill of the entire truncated prompt
   (`metal_graph_prefill_chunked(prompt->len)` at ds4.c:79564).
5. Observability: the RESET call DOES wire ds4_session_note_prefill_progress
   (ds4.c:79566) but the server leaves `s->progress` NULL on the rewind
   path (cleared at ds4_server.c:15947/15983, never re-armed inside
   server_generation_rewind) - plumbing exists, emitter unwired ->
   minutes of pure silence (unlike the 'tool checkpoint rebuild' path,
   ds4_server.c:15176+, which logs + keepalives).

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
1. REAL WIN: give DEEPSEEK4/Vision-Exp a family-correct snapshot restore -
   the substrate already exists (`dspark_rewind_frontier`, session-owned
   spec-frontier buffers surviving the verifier call per the comment at
   ds4.c:64175-64176); wire a rewind-recover path through it (analogous
   to the qwen4 snap0 set), or relax the dspark_rewind_recent gates that
   M0 shows firing. Sizing needs M0's bail classification first.
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
