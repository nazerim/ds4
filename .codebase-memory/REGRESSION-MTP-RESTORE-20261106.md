# REGRESSION — merged tree breaks Qwen3.8 round-trip identity (20261106)

**Status: merge/upstream@5c8dff1 HELD at landing gate.** Batches A+B+C
build clean, coexist-tier green, Vision-Exp suite green, boot smoke
green - but the QWEN FIXTURE tier (the one that exercises Qwen Metal +
MTP snapshot paths) is RED on merged / GREEN on main.

## C6 matrix (single-variable bisect, same machine, same model file)
| run | result |
|---|---|
| merged, session-rewind, DS4_TEST_GLM_MTP=1 | ERR (7x ds4_test.c:236, 2e-3 logprob identity) |
| merged + DS4_QWEN4_NO_PREFETCH=1 | ERR (prefetch transplant NOT implicated) |
| **main**, same test + env | **OK, 'ds4 tests: ok'** |
| merged plain | ERR (stable, not flake) |

## Failing arms (both = round-trip identity: live-continue vs restore-continue)
- `session-rewind` :236 - snapshot save->load->eval top-8 logprob match
  (mtp branch only; DS4_TEST_GLM_MTP also arms DS4_QWEN4_SPEC_FORCE_ACCEPT=1)
- `prefill-checkpoints` :385 (test_qwen_prefill_scores_equal, 1e-5 logits,
  live vs checkpoint-restored session) - 8 occurrences in full-suite run;
  C7 [3]/[4] isolates per-arm verdict.

## Passing on merged (scope markers)
verify-identity OK | rowcount-ab OK (stream-diff=0) | rewind-RESAMPLE OK |
golden-vectors OK | logprob-vectors OK | full vision-model suite OK |
boot smoke OK. The split is sharp: forward/compute paths fine;
**save->load->continue numerics broken.**

## Prime suspects (sovereignty seam = fork payload trio inside upstream runtime)
1. **Tail-state mismatch**: fork reader restores `mtp_pos` (V1/V2
   semantics, no tail concept) + my 3-line `mtp_tail_valid=false` reset;
   upstream runtime (kept, auto-merged) consumes tail caches in forward
   (61793-era `mtp_tail_valid && idx+T-1==mtp_tail_pos`) and takes a
   PREDICTOR-REBUILD path on restored sessions while the live session
   rode the TAIL path -> different float op order -> 1e-5/2e-3 fails.
   Upstream's own trio solved this by PUTTING THE TAIL ON THE WIRE
   (v3); our wire cannot (V2 lineage sovereignty).
2. Auto-merged neighbors of the swapped trio (stage_payload_span / delta
   chaining at 66300/64500 callers) may assume upstream trio state fields.
3. h[13]-era live-KV restore code (`saved_full_live/saved_raw_live`)
   reading fork payloads.

## Engine-lane options (decision owed in the morning)
- **A (recommended long-term): forward-integration.** Adopt upstream's
  v3 trio as the base and re-implement fork V2 span-slicing + capture
  hooks (ds4_td_segment_end, checkpoint_valid, skip_overwritten) INSIDE
  it, incl. a tail solution that round-trips (persist tail OR prove
  rebuild identity to 1e-5). This is a design merge, not a patch.
- B: teach the fork reader to synthesize upstream-expected MTP runtime
  state so restore==live numerics (may be impossible while live uses a
  cached tail the wire doesn't carry).
- C: keep the merge branch as-is (all other gates green), land only
  batches A+B for now (clean), defer C - but batch C is 176/234 commits,
  so the debt stays open either way.

## Process rules this produced (already in PLAN log)
- QWEN FIXTURE BOOT is a mandatory pre-land gate (device .metal is
  invisible to host build; Vision-Exp boot proves nothing for Qwen).
- Suite exit code lies (rc=0 with 'ds4 tests: 15 failure(s)') - parse
  the summary line, not $?.
- Anchor sweeps must be reference-graph based (dead-but-present missed
  the whole prefetch feature).
