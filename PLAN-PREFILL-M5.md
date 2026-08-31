# PLAN-PREFILL-M5.md — M5 Max prefill: indexer pipeline levers (2026-08-31)

Companion to DS4FORK.md "PREFILL — M5 Max Performance Attribution".
Target: the indexer score/top-k pipeline — the only ctx-scaling non-MoE cost
(22% + 7.5% of chunk time at 65k, ~42% combined at 160k).

## Facts this plan rests on

* Tensor API matters and is tuned: `DS4_METAL_DISABLE_METAL4=1` drops 34816
  prefill from 581 → 340 t/s (1.71x). The ceiling is real, not folklore.
* Dense MPP matmul (`kernel_mul_mm_mpp_direct_rhs`, attn-out 8192x4096 @2048
  tok) sustains **21.8 TF/s**. The indexer score NAX kernel
  (`kernel_dsv4_indexer_scores_nax`) plateaus at **13.5–14 TF/s** → ~1.5x
  kernel headroom, priced at +8% prefill @65k, +12% @160k.
* `MTLCounterSampleBuffer` per-encoder attribution is **unavailable** on this
  GPU (device exposes only the "timestamp" counter set — no stage-input /
  visibility counters). Batch-internal timing must keep using the
  end/begin_commands stage-split profilers (`DS4_METAL_*_STAGE_PROFILE`).
  → HC/sinkhorn + fused-residual decomposition stays parked; revisit only
  via targeted stage-split boundaries copied from the MoE pattern.
* Source-injection envs (`DS4_METAL_DSV4_MISC_SOURCE`,
  `DS4_METAL_ARGSORT_SOURCE`, `DS4_METAL_MOE_SOURCE`, …) allow JIT kernel
  sweeps without rebuilding the binary — this is the sanctioned experiment
  loop (see ds4_metal.m:4446).

## Exactness doctrine (from this session's Q4-MPP experiment)

`metal_prefill_variant_bench` fails on ANY logit reorder (129278/129280
floats differ at ~0.3% from accumulation-order change alone). Every lever
below therefore ships gated: env/candidate flag → balanced A/B → promote to
M5-default only with `--quality` keeping the reference path, mirroring how
`kernel_mul_mm_id_*_mpp` variants are selected.

## Lever 1 — indexer score NAX tile retune (no output-order change if
accumulation sequence per (token,comp) is preserved)

Current shape: TM=16 tokens × TN=32 comp × NK=32 over D=128, 128 threads,
4 simdgroups, dot staged f32 in threadgroup. Upstream comment records a
swept loss at TN=64 only. Untried axes:

* TM=32 tokens (same TN) — halves ktg re-staging per token (K rows reused
  across twice the tokens — this is the 22-vs-14 TF/s gap candidate).
* NK=64 with double ktg buffer.
* Register-side accumulation (`get_results` direct) vs threadgroup `dot`.

Protocol: variant .metal sources in /tmp, `DS4_METAL_DSV4_MISC_SOURCE=…` +
`DS4_METAL_INDEXER_STAGE_PROFILE=1` bench at ctx 65536, compare
score ms/call @comp=16384 and TF/s; winner goes through
`metal_prefill_variant_bench` (new env candidate) for exactness, then
ds4-bench sweep + server spot-check.

## Lever 2 — topk merge without score-row re-reads

Block kernel (`kernel_argsort_f32_i32_desc`) emits indices; each of the 4
merge rounds re-gathers float scores from the original 16384-wide row
(random 4B touches ≈ cache-hostile). Fix: pack (orderable-score-bits,
index) into uint64 scratch rows at the block stage; merges read packed rows
sequentially; final selection unpacks indices. Bit-identical order iff the
pack preserves the current comparator's tie-breaking (must read the exact
sort predicate first). Host change: scratch_row_bytes ×2, plus kernel arg
field. Expected: topk 580 → ~300 ms/chunk @65k (+3–4%).

## Lever 3 — f16 score buffer (deferred, dependent)

Halves score writes (256 → 128 MB/layer-call) and enables 32-bit packed
merge keys (f16 order-preserving transform << 16 | idx14..16). Two gates:
(1) tie analysis: f32→f16 collisions change the top-512 selection set only
if a collision straddles the k=512 boundary — must quantify on real score
distributions before shipping, else reference-f32 stays default;
(2) `n_comp ≤ 65535` index packing ceiling vs 512k-ctx (128k+ comp rows at
ratio 4) → needs wide-mode fallback. Parked behind lever 2.

## Out of scope

MoE gemm (roofline), attention kernels (0.4% share), chunk size (plateaued),
thermal (~5%, flat), ANE (dead track), HC/sinkhorn (needs stage-split
boundaries — separate ticket if lever 1/2 land).

## Status log (session results — all measured 2026-08-31 on this M5 Max)

* [x] Counter-buffer feasibility probe → **dead**: device exposes only the
  "timestamp" counter set (no per-encoder stage-input counters). Apple GPU
  batch-internal attribution stays with end/begin_commands stage splitters.
* [x] Lever 1 → **marginal**. vA (dot-layout swap for conflict-free epilogue
  reads, source-injected `dsv4_misc`): 81.5→80.3 ms/call (−1.5% stage, ~0.3%
  prefill; bit-exact). vB (single K=128 cooperative run, one barrier/pair):
  90.9 ms — **regression**; the 4-chunk double-buffered pipeline is what hides
  staging latency. The kernel's ~14 TF/s is the M5 NAX plateau for these
  32x32x32 shapes, not a defect; TM=32 would need host-grid rework and is
  untested territory upstream (their sweep only covered TN). NOT promoted
  (vA below noise floor; variants live in /tmp, cheap to re-try).
* [x] Lever 2 → **negative, hypothesis falsified**. Implemented sidecar
  merge (score carried next to indices, exact tie semantics preserved; gated
  `DS4_METAL_INDEXER_TOPK_SIDECAR`, new `_sc` kernels appended to
  argsort.metal). Result: 28.6 vs 27.9 ms topk@16384 — slightly worse. The
  merge's "random" score gathers hit an L1-resident 64 KB row; carrying
  scores doubles sequential bytes for nothing. **Reverted** (both files).
* [x] Lever 3 → **dropped**: premised on gather cost (falsified) and on
  halving merge reads (also L1); only real saving is the 256→128 MB score
  write (~0.4% of chunk), against genuine tie-collision exactness risk and
  the n_comp>65535 packing wall. Not worth it.

## Final decomposition (reconciles the decay exactly)

Per-token costs: non-MoE residual minus indexer ≈ 0.59 ms (2k) vs 0.71 ms
(65k) — flat within noise. The indexer goes 0.05 → 0.56 ms/token. The entire
ctx-decay is indexer score+topk growing as O(ctx/4) at hardware rate. There
is no software inefficiency left to harvest in the measured buckets;
further prefill gains require either algorithmic change (multi-level index —
model semantics, not this repo's to decide) or the un-profiled flat
residual (shared/qkv/HC/compressor ≈ 0.6-0.7 ms/tok, ~9% of prefill —
splittable later via MoE-pattern stage boundaries, but ctx-flat so it does
not close the long-ctx gap).

**Bottom line for the question "what's our possible improvement":** after
implementing and measuring all three candidate levers, the honest ceiling
on M5 Max with this model was ~0-3% (vA), not the 10-15% estimated from the
TF/s plateau alone. The prefill path is within measurement noise of its
tuned limit; long-ctx prefill speed is bounded by the indexer's inherent
FLOPs, and the remaining practical levers are hardware-level (bandwidth,
tensor throughput) or model-level (indexing structure).
