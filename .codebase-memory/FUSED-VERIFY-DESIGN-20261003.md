# Fused T=3 verify — scoped design (the measured +20% decode project)

Origin: `MTP-ACCEPTANCE-20261003.md` — forced depth-3 yields +26.6%
tokens/cycle but +2% WALL, because the exact T=3 path runs attention as 2/1
sub-batches: **C3/C2 > 1.266**. Blueprint source: oMLX #4041 (row-exact
verify windows). This doc scopes the ds4 implementation; kernel surgery is
deliberately left for a fresh, dedicated session with the engine-stopped
identity gates scheduled.

## Why T=3 must currently split (root cause, code-verified 2026-10-03)

`qwen4_graph_attention_tail` (ds4.c:58501) runs `attention_core` as 2/1-row
sub-batches when `verify_rows_exact`, each with its own `nba_sub` block
universe (:58518-58521). Two distinct geometry switches force this:

1. **Indexer score/select — soft constraint, fixable at the host level.**
   - `ds4_gpu_qwen4_idx_score_tensor` (ds4_metal.m:49305):
     `n_tokens > 2 && n_idx_head==4 && idx_dim==128` → hard-routed to
     `IDX_SCORE_MM` (tensor path; different sums per the recon's "GEMM
     becomes different-sums at >=2 rows" — wait, see B0 below: recon
     attributed flips to the MM path at >=2 rows; the exact serial-matching
     scorer is the SCALAR/VEC family).
   - `kernel_qwen4_idx_score` / `_vec` are row-agnostic: grid.y = n_tokens,
     and the kernel applies **per-row causal block masking internally**
     (`visible = (args.pos0 + tok + 1) / args.ratio`, qwen4.metal:1548).
   - `qwen4_idx_select` PRE-variant gate: `tile_max && vec && n_tokens <= 2`
     (ds4_metal.m:49342) — row-count gate again, reason = measured perf, not
     capability (grid is per-token).
   - BUT the host comment at ds4.c:58518-58521 claims a whole-chunk universe
     "would let rows select blocks a true 2-row pass could not see" — this
     CONTRADICTS the per-row `visible` masking unless select/expand/decode
     trust the universe for something the score masking doesn't cover. B0
     must resolve this contradiction (test, not prose) before anything else.
2. **Attention decode — hard constraint, kernel-level.**
   `ds4_gpu_qwen4_attn_decode_tensor` (ds4_metal.m:~49655): split-K ladder
   from chunk size — `n_keys = pos0 + n_tokens`, `n_splits = ceil(n_keys /
   qwen4_attn_split_keys())`, `keys_per_split` uniform across rows, FP32
   partials in `g->attn_part` (`part_bytes = attn_part_floats(n_rows,...)`).
   A row's accumulation boundaries therefore depend on the DISPATCH's row
   count, not the row's own position. Row 0 under a 3-row dispatch splits
   differently than under today's 2-row sub-dispatch → different sum order →
   not bit-identical to serial decode. THIS is the true core of C3/C2.

## Milestones (ordered, independently gated)

### B0 — RESULT 2026-10-03: NEGATIVE. Rows kernels are NOT the exact sub-batch geometry.

Experiment executed (branch prototyped in the working tree only — never
committed, now reverted; tooling kept in `tests/spec_economics/` with both
batteries' outputs saved):

- Wired the single-session exact T=3 attention through
  `qwen4_batch_attention_entries` (3 entries, one session's caches, env
  `DS4_QWEN4_VERIFY_ROWS_FUSED`), vs the sub-batch split, forced depth-3,
  temperature 0, identical 10-prompt battery.
- **Greedy identity BROKE: 2/10 prompts diverged** (char ~1364 in a story,
  ~1520 in UBI prose — accept-then-diverge = a verify row committed a
  different argmax than the split path would have).
- **It wasn't cheaper either: +0.3% wall (57.4 vs 57.2 s)** — the rows
  kernels dispatch `grid = (QWEN4_ATTN_MAX_SPLITS=64, n_head_kv, n_rows)`
  unconditionally; three rows burn 64-split-slot threadgroups, so the
  single dispatch costs ~what the 2/1 sub-batches saved.

Implications for B1/B2 (re-scoped):
1. The rows path's per-row geometry (`qwen4_attn_row_splits`, kps = fixed
   split-size windows) differs from the plain decode kernel's redistributed
   `keys_per_split = ceil(n_keys/n_splits)` — the "same arithmetic" batched
   comment holds between batched variants, not against the sub-batch exact
   path. B1's hoped-for "split only the decode half" is NOT free: the two
   halves disagree on selection/sums somewhere (score masking vs universe,
   or the kps model) — needs per-stage buffer diffs (score[], sel_blocks[],
   partials) to localize, before any fusion is attempted again.
2. A viable fused window must REPRODUCE the sub-batch path's exact per-row
   geometry inside one dispatch — i.e. a purpose-built kernel with
   per-row (n_splits, keys_per_split) computed like the plain kernel does
   for chunks of ≤2, not a repurposing of rows2.
3. The C3/C2 > 1.266 finding still stands (forced depth-3 still loses
   wall — the split is expensive); the payoff target (~+20%) still exists;
   only the "easy path to it" is closed. Cost-side next probe should
   PROFILE where the exact T=3 cycle actually spends (component timings,
   DS4_QWEN4_TIMING per-cycle) before writing kernels — the HC 2/1 split
   (:58349) also doubles gate/mix dispatches and may be the cheaper first
   target.

### B0(original) — resolve the universe-vs-mask contradiction (measurement, 1-2 h)
Test: single-dispatch T=3 through the VEC score + PRE select + expand (drop
the sub-batch split for the indexer half only), then compare greedy verify
outputs against the serial reference — the existing identity gate
`./ds4_test --mtp-verify-depth` (plus `--local-golden-vectors` and the
`d55b438` MTP payload checks) is exactly the oracle. Engine stopped (today:
cross-stop ds4-server first).
- If bit-identical: the nba_sub comment is over-cautious (score masking is
  sufficient) → B1 unlocks immediately.
- If not: identify which stage trusts the universe (instrument sel_blocks /
  n_sel diffs per row), fix that stage's per-row masking, retest.

### B1 — split only the decode half (host change, small)
`attention_core`: run score/select/expand ONCE at chunk width (all cT rows,
`tile_max` + vec always passed), keep the 2/1 sub-batch loop ONLY around
`ds4_gpu_qwen4_attn_decode_tensor`. Saves ~4 of ~10 exact-path dispatches
per layer. Expected C3/C2 from >1.266 to ~1.15: forced depth-3 flips from
-2% to roughly +8-12% wall on coding-class traffic (2.201/1.15 ÷ 1.739 ≈
1.10, plus adaptive-policy effects). Env-gated
(`DS4_QWEN4_VERIFY_FUSED_INDEXER`, default off), RED via mtp-verify-depth
before enabling.

### B2 — per-row split ladder (the real fusion, kernel work)
Make the decode kernel's split geometry row-local: `n_splits_r =
ceil((pos0+r+1)/split_keys)`, per-row `keys_per_split_r`, partial buffer
sized `sum_r n_splits_r × head_dim...` (host computes via the existing
`ds4_gpu_qwen4_attn_part_floats` extended to per-row), reduce kernel loops
per-row n_splits_r. Then T=3 (and T=k) runs ONE decode dispatch and every
row's sum order matches its own serial step — oMLX's "each row runs its own
score+SDPA" end-state. Gate: `DS4_QWEN4_VERIFY_ROWS_EXACT` env (per-cycle
read, default 3 = today's behavior) widened cap 3→8 with byte-identity pins
per row count (ds4_metal.m:5613-5635 discipline for row-geometry tables).

### B3 — economics re-measure + re-gate the policy
After B1 (and again after B2): rerun the acceptance battery
(`MTP-ACCEPTANCE-20261003.md` scripts, PERSISTED copies to be added to
tests/ tooling when touched). If forced-3 goes net-positive, re-tune
`qwen4_spec_depth` engagement thresholds (its "~0.6 on general prose"
calibration measured 0.705 on coding class). Only then consider draft
depths >2 (P(a3|a2) unmeasurable until then — lane-2 open question).

## Cost model note (why B1 is worth doing first)

Tonight's acceptance battery says the EXACT verify cycle already beats
serial decode throughput (adaptive 1.739 tok/cycle at ~65 t/s vs 33.7 flat).
Every dispatch removed from the exact path converts draft quality into wall
time ~1:1. B1 is ~30 host lines; B2 is kernel surgery (one decode kernel +
reduce + geometry tables); both have exact, model-level oracles already in
the repo (mtp-verify-depth is literally the autoregressive-identity
contract). No new test infrastructure needed; everything requires the engine
STOPPED for the identity runs.

## Standing constraints for the implementing session

- One engine resident at a time (omlx/ds4) — 128 GB machine.
- `make test` under Qwen is green (25 OK / guarded skips) — keep it that way;
  B0-B2 each land env-gated default-off until their gate passes.
- Fail-closed convention (every dispatcher returns 0 on unsupported shape →
  falls back to pre-split behavior, never silent).
