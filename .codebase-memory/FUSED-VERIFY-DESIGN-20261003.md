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

### B1 attribution (2026-10-03) — CORRECTED 15:00 after live bisect; the
### original version misattributed the battery divergence to grouped (and
### misread the shared-dense-only control output). Corrected story:

1. DS4_QWEN4_MOE_DEBUG_BISECT in-engine replay (all MoE layers, T=2 AND T=3
   cycles, real production inputs): grouped mid/grouped down == slot twins
   with ZERO element diffs, chain-composition also zero. Grouped is
   BIT-EXACT (the earlier claim "grouped breaks bit-identity" was wrong:
   every MIN=1 run - shared-dense-only, slot-mid+grouped-down, full-grouped
   - shares ONE divergence fingerprint (prompts 3@884, 7@1520 vs the
   shared-slot baseline); the common ingredient, not grouped, is the cause).
2. THE actual exactness fact (now proven, not assumed): shared-dense
   (dense gemv + swiglu + reduce shared_src=2) is NOT bit-identical to
   shared-as-slot (slot-kernel branch + reduce shared_src=1), and the T=1
   serial reference is the SLOT variant. The original `shared_dense =
   T > 8u` gate therefore guards verify exactness, not just perf - do not
   lower it.
3. The grouped kernels REQUIRE shared_dense (their host has no shared slot:
   "the batch runs the shared expert as dense projections") - which is why
   the grouped experiment appeared non-exact: it dragged shared-dense with
   it. Grouped at verify size needs shared-as-slot plumbing (below), not a
   shared-dense exception.
4. Measured prizes stand: grouped (with dense shared) verify T3 26.5 vs
   27.95 slot baseline (-1.35..1.45ms/cycle, ~+4.5% wall at forced-3:
   55.1s vs 57.2s battery). The exact version must preserve most of it.

### B2 - SHIPPED 2026-10-03 (`c7eb582`, DS4_QWEN4_MOE_GROUP_EXACT, default off)
Design as specced below and verified: `out_layout` args field ((row-stride<<6)
| slot-base) threaded through mid/down/grouped kernels via
`qwen4_moe_pair_row`; grouped routed + shared dispatched through the slot
kernels' own shared branch at NS=0/grid.y=1 (bit-exact by construction);
reduce untouched baseline form. Measured (M5 Max, battery + goldens):
forced-depth-3 55.4 vs 57.2 s (-3.1% wall) byte-IDENTICAL to the split
baseline; adaptive-exact vs adaptive-baseline IDENTICAL (T=2 path included,
prefill tails 2..8 also ride grouped); golden vectors pass env on AND off;
full `make test` green (25 OK, exit 0). Prefill-tail and shallow paths gain
the expert dedup too (small free bonus at T=2). NOT yet default - operator
flip decision after B3.

### B3 - NEXT: policy re-tune (the remaining unlock)
`qwen4_spec_depth`'s engagement gates (perfect-8-bit window, reject-streak
disengagements) were calibrated against the OLD verify delta (5.0 ms deep
vs 22.9 shallow); exact grouped cuts it to ~3.7 and makes deep cycles
positive in isolation, but adaptive fires deep on only 0.5% of cycles, so
the shipped win needs the POLICY to actually engage. Re-tune loop (all
identity-safe via battery parity): relax bits window, revisit the
reject2-streak rule against measured P(a2|a1)=0.705, consider content-class
engagement (code/JSON continuation). Metric: adaptive+exact wall <
adaptive baseline AND byte-identical. Then revisit B1-split-removal and
widths >3 (P(a3|a2)) on top.

### Original B2 design sketch (superseded by shipped note above)

Goal: exact grouped verify MoE = grouped routed (proven bit-exact) +
shared computed by the UNTOUCHED slot-kernel shared branch (bit-exact by
identity with baseline) composed through the ORIGINAL baseline reduce
(shared-as-slot, shared_src=1, n_out=11 stride). Two kernel-host changes:
- grouped mid/down: write/row-index via a stride-11 output layout when the
  caller keeps shared as a slot (list membership pair = t*10+s unchanged;
  mid/part row = t*11+s). Reuse a qwen4_moe_args pad slot for `out_stride`.
- add a shared-slot-only dispatch: reuse kernel_qwen4_moe_mid/down with
  slot base 10 (dispatch y from the shared slot only), i.e. `slot_base`
  in the kernel's slot index math (grid.y stays 11, or start-index arg).
Baseline composition check afterwards is cheap: the DS4_QWEN4_MOE_DEBUG_
BISECT replay is the oracle (all-diff-zero => ship; defaults flip via
SHARED_DENSE_MIN staying 8 - grouped must gain its own row-count gate
`T >= 2` decoupled from shared_dense).

### Original (superseded) attribution notes:

Per-group T3-vs-T2 verify delta (profiler-synced numbers, single session):
verify_deep 27.85 vs verify_shallow 22.85 ms -> **5.0ms**; TIMING=2 group
breakdown attributes ~**3.1ms to MoE** (20.3 vs 17.2), ~0.6 gdn, ~0.6 hc_ffn,
~0.3 hc_attn, ~0.3 attn, ~0.2 ple. The MoE share is per-(row,slot) expert
weight re-stream at T<=8 (slot path). Experiments:
- shared-dense at verify T (DS4_QWEN4_SHARED_DENSE_MIN=1, routed still
  slot): byte-IDENTICAL, zero savings (27.94ms) - shared reads overlap.
- shared-dense + grouped experts (default knobs at MIN=1): saves 1.35ms
  (26.5ms, forced-3 battery 55.1s vs 57.2s) but **breaks slot-reference
  bit-identity** (same prompts 3/7 diverge as B0 - the grouped kernel's
  per-row reduction order differs from the slot kernel, which IS the T=1
  serial reference).
=> the remaining exactness blocker is now ONE question: make the grouped
mid/down kernels reproduce the slot kernels' per-row accumulation order
(metal/qwen4.metal MOE_MID_Q4K_GROUPED / DOWN_MXFP4_GROUPED vs
MOE_MID/DOWN slot variants - compare dequant/simd_sum order). Everything
else on the deep path is genuine per-row compute.

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

### B1 — RESULT 2026-10-03 (evening, `e02f173`): chunk-wide selection is
### BIT-EXACT; the decode split geometry is what binds.
Experiment under PER_ROW (env `DS4_QWEN4_VERIFY_INDEXER_BATCH`, default off):
verify chunks run score/select/expand ONCE chunk-wide (legacy universe,
tile_max) and split ONLY attn_decode per row. rowcount-ab 110 pairs abs
63..263 (entire sparse regime) on both T=2 and T=3 grids: every stage hash
identical, maxabs EXACTLY 0.0; identity 3-grid flips=0 (03/06/07 + copy
default); battery x2 run-identical, 10/10 serial-equal, 71.5/71.7 s.
=> the per-row block universe never binds (the in-kernel visible masking is
sufficient, as the original code comment suspected); only per-row DECODE
key counts/splits must change. BUT the prize is small (~0-5%): with the
indexer already batched, per-row decode costs only ~0.5 ms/cycle. The
dominant remaining tax is the matvec per-row WEIGHT RE-READS
(~4.5-5 ms/cycle, shallow verify 22.53 drift vs 27.98 per-row-v2; profiler
numbers 20:1x) - i.e. the fused window must make the mv_ext projection
kernels row-INVARIANT (per-row reduction tree == T=1 tree inside one
chunked dispatch), which is the real B2 kernel work, not the decode ladder.
Stack bonus: adding `DS4_QWEN4_MOE_GROUP_EXACT=1` (B2, validated tonight
under per-row: battery 10/10 serial-equal, 3-grid flips=0, x2 run-identical)
takes the verify stack to 66.8-67.0 s - FASTER than serial (69.6-73.8)
while bit-identical to it; deep(T=3) cycles 37.0 ms vs 28.3 drift.

### B1 — split only the decode half (host change, small) [historical spec, superseded above]
`attention_core`: run score/select/expand ONCE at chunk width (all cT rows,
`tile_max` + vec always passed), keep the 2/1 sub-batch loop ONLY around
`ds4_gpu_qwen4_attn_decode_tensor`. Saves ~4 of ~10 exact-path dispatches
per layer. Expected C3/C2 from >1.266 to ~1.15: forced depth-3 flips from
-2% to roughly +8-12% wall on coding-class traffic (2.201/1.15 ÷ 1.739 ≈
1.10, plus adaptive-policy effects). Env-gated
(`DS4_QWEN4_VERIFY_FUSED_INDEXER`, default off), RED via mtp-verify-depth
before enabling.

### B2 — RESULT 22:0x-22:4x (window 3): SINGLE-TREE implementation (env
`DS4_QWEN4_VERIFY_SINGLE_TREE`, default off) — one tree for everything, no
per-row dispatch cost.

Design pivot: the ext matvec family (kernel_mul_mv_ext_*_f32_r1_N) is
row-invariant by construction — each token row keeps its own lane walk +
shuffle tree inside one threadgroup, weights dequantized once (lx[ch]) and
shared across rows. The identity doc's drift was ext(T>=2) vs PLAIN-mv(T=1)
— different families. So instead of making the chunked path per-row, route
T=1 THROUGH ext (the same kernel, r1ptg(1)->2) and flip the two other
row-count-sensitive choices: HC gate/mix pair->generic (row-invariant per
tonight's NO_HC_PAIR proof) and keep attention decode per-row (the only
genuinely row-count-sensitive stage). Net: serial decode and spec verify
share one reduction tree by construction; the plain-mv tree is retired in
this mode.

Gates (this window): rowcount-ab 110 pairs T=2 grid + 60 pairs T=3 grid
prompt-03, all maxabs EXACTLY 0.0, 962/962 stage hashes identical; identity
3-grid flips=0 on 03/06/07 think-none AND think-high; battery: st_adaptive,
st_group x2 (run-identical), st_serial_ref — ALL mutually 10/10 (spec ==
serial in-tree, the defining property); vs the banked plain-tree streams the
ST world differs on exactly ONE near-tie (prompt 7 @ char 1555 — the same
site that surfaced as the v1 residual; the two trees break that tie
differently, both self-consistent). Default path (ST unset): full suite
26 OK / 0 ERR, goldens unbroken.

Cost (M5 Max battery, wall): ST plain 67.7 s; ST+MOE_GROUP_EXACT 62.4/63.0
(run-identical texts); ST serial (spec off) 67.1 s — spec pays ~6% inside
the ST world (drift world: 55.4 vs plain serial ~71.7). Per-cycle: shallow
verify 25.5-25.8 ms ST vs 22.5 drift vs per-row-v2 28.0; deep 31.2-31.7 vs
28.3/38.4. ST is the FASTEST known bit-exact mode (v2 66.8-67.0 -> 62.4)
and its serial fallback is also faster than the plain-mv serial (67.1 vs
~71.7) — the ext kernel wins at T=1 on this GPU. Remaining gap to drift is
the ext T=2 kernel itself (25.5 vs 22.5): it reads x per row and streams
weights once per threadgroup, but the plain-mv T=1 it replaced was already
bandwidth-optimal; closing 25.5 -> ~23 needs the true fused kernel (x
multi-row staging), the ORIGINAL B2 kernel work, still open.

Decision implications: ST changes the serial reference (one near-tie moves),
so it CANNOT be a drop-in default without golden re-capture; v2 remains the
default-audit mode that preserves today's exact stream. For an audit-only
mode, ST at 62.4 s beats v2 at 66.8. The p7@1555 near-tie is the empirical
proof that "the" greedy stream is tree-dependent — worth a line in the
identity doc next time it's touched.

### B2 — per-row split ladder (the real fusion, kernel work) [historical spec]
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
