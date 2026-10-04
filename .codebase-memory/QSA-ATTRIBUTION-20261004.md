# QSA / prefill attribution 2026-10-04 (overnight) — queue item 1 worked, C3 tested-and-declined, next lever named

Purpose: the ranked follow-up to the omlx-0.7.0 re-baseline (ds4 cold prefill
~1300-1400 t/s vs omlx ~1733-1748 flat; `omlx-v070-final-perf.md` §3.1).
Attribution-first per `omlx-v070rc1-perf.md` #3 — PLAN-PREFILL-M5's split
was DeepSeek-only. All numbers M5 Max, production exact stack
(SINGLE_TREE+GROUP_EXACT+HC_PAIR), single session.

## 1. Method + headline numbers

- `tests/spec_economics/prefill_probe.py` (durable tool): nonce-prefixed
  cold prompts, max_tokens=1, TTFT-implied prefill t/s; `text <tok> <out>
  <fixed-nonce>` mode = sparse-regime greedy text oracle (48 tokens).
- ds4 cold prefill: 13006 tok @16k -> 1347/1428 t/s; 51331 tok @64k ->
  1371-1408 t/s (boot-to-boot spread ~6%; engine restart is part of each
  config's cost budget).
- Stage buckets per 8192-token chunk (DS4_QWEN4_TIMING=2, sync regime —
  ratios only, NOT cross-config deltas, see OVERNIGHT FOURTH WINDOW):
  at pos>=32k: ple 36 / hc_attn 330 / gdn 1500 / attn 1350-1413 /
  hc_ffn 400 / moe 2090 ms => **moe ~39%, gdn ~28%, attn ~26%, hc ~13%**.

## 2. The attn bucket decomposed (model-free kernel benches, QWEN4_BENCH=1,
n_blocks=65536 shapes, T=1024): attn_mm (gathered 512-block sparse body)
8.0 ms, idx_score_mm 5.8 ms (=11.9 TF/s vs the 21.8 dense-MPP ceiling),
idx_select 1.7 ms, expand+gaps ~0.3 ms => ~60/28/8 split. At NO_ATTN_MM=1
the live 64k prefill attn bucket goes 1413->2600 ms (per-token decode body
is ~1.2 s/chunk slower than attn_mm — the existing MM kernel already earns
its keep).

## 3. C3 (score-sheet query waves = literal #3934 analogue): IMPLEMENTED,
MEASURED FLAT, REVERTED.

Wave loop over the sparse tail in `qwen4_graph_attention_core` (score ->
select -> expand -> attn body per W rows, sheet working set W x B instead
of chunk x B; 8192x537->1024x67 MB @64k). Invariance: PROVEN — every stage
is per-token independent; 16k-prompt 48-token greedy texts byte-identical
across W={off,512,1024,2048} (fresh engines, `tests/spec_economics/
results/20261004_qsa_text_*.json`); full suite 26 OK + prefill-checkpoints
OK under waves; decode/spec path untouched by construction (cT>8 only).
Performance: 1305 (control) vs 1299/1290/1285 t/s at 64k — flat-to-noise,
slightly negative (extra dispatches). The select re-read traffic it targets
is only ~8% of the attn bucket, and oMLX's motivation was their per-call
sheet ALLOCATION, not our traffic (g->score is allocated once at cap).
Verdict: reverted (`git restore ds4.c`); the experiment + proof-of-
invariance stand as the record; revisit only if a future score epilogue
(tile_max for C1) needs wave plumbing.

## 4. Ranked next levers (by measured share of prefill at >=64k)

1. **MoE prefill rate (~39% of time)** and **GDN scan (~28%)** dominate;
   the omlx gap is fundamentally there, not in QSA. POTENTIAL (sized
   2026-10-04): moe+gdn = 67% of >=32k prefill. 1.2-1.3x on both buckets
   (realistic arm-selection win): prefill +~11-15% (1350 -> ~1500-1560).
   Full 1.5x (tensor rate both): +~21% (~1660) -- near omlx parity
   (1733-1748). Below ~1.2x both, omlx stays ahead at long ctx. Bench fat:
   router f32 custom 727us vs dense-mm 142us; hc down f16 417 vs 272;
   counterexamples where custom wins (q8 gemm 258 vs 795, hc up 69 vs 208)
   -- the choice is per-(type,shape,rows) and step 1 is a dispatch audit of
   what the live prefill path ACTUALLY uses at 8192-row chunks (read-only +
   bench arms; zero engine cycles).
2. **C2 — idx_score_mm token-tile widen TM 16->32 + key-table staging
   reuse across row bands** (the true "wide QSA tiles"): score is 28% of
   the bench slice at n_blocks=65536 but scales with B, so at 64k ctx the
   bucket split is ~75% mm / ~14% score / ~4% select (mm is fixed-top-k).
   Ceiling prize at 64k ~1.5-2% total prefill (score runs 11.9 TF/s vs
   the 21.8 dense-MPP ceiling), more at 160k+ where B-linear score growth
   bites; the score accumulation walk is per-(token,head,block)
   k-ascending so TM changes are invariance-preserving if accumulators stay per quadrant
   (kernel comment + `--qwen4-prefill-checkpoints`/`--kv-delta`/
   `test_idx_prefilter` extended). Dedicated session; start with
   `metal_prefill_variant_bench` + `QWEN4_BENCH_ONLY="idx score"`.
3. **C1 — tile-prefiltered select for prefill rows** (MM epilogue emits
   per-8-block maxima; select reads B/8 + compact keys): ~1.7 ms of
   ~10 ms/layer-chunk at 256k => small; gate `test_idx_prefilter` must
   extend to T>2 rows first.
4. **C4 — attn_mm KT 16->32: CLOSED INFEASIBLE 2026-10-04, no golden
   decision needed.** Probe build (reverted same hour): the kernel declares
   KV[2*KT*256] half + Qs[16*256] + Sx/Ps/Dg/Id ~= 44,416 B of threadgroup
   memory at KT=32 vs the Metal device maximum of 32,768 B; pipeline
   creation fails loudly ("Threadgroup memory size (44416) exceeds the
   maximum threadgroup memory allowed (32768)"). At head_dim 256 a K+V pair
   of 32 keys is 32 KB alone -- tile width is HARD-CAPPED by smem, and
   dim-split staging workarounds reintroduce the same PV-chain order
   question while costing a day. The live lever for attn_mm is the
   tensor-unit rewrite class (oMLX #4020 / MPP) -- a separate project whose
   payoff must be measured against the MoE/GDN fronts (2.4x larger share).
5. Decode-side residual from 0': per-row split ladder in the T=2 decode
   dispatch (~0.5 ms/cycle, FUSED-VERIFY-DESIGN B2 historical spec).

## 5. Caveats recorded

- TIMING=2 sync inflates configs with different dispatch counts (bit
  twice today: rowe read +6ms/cycle slower under sync yet is -1.8ms in
  the async wall). For cross-config deltas use A/B knobs + MTP_PROFILE.
- Bench n_blocks=65536 is the 256k shape; at 64k everything scales ~B/64k
  linearly for score/select, attn_mm depends on top-k rows not B.
- prefill_probe filler ~1.3 tok/word; prompt_tokens from usage is truth.

## C2 RESOLVED NEGATIVE 2026-10-04 (quiet window): both score-MM widening
## shapes tested, both LOST, reverted (HEAD clean):
- wide tile (32 tok x 32 blk, 24.9 KB smem): bit-identical via the new
  harness pin (7 guard shapes, byte-exact vs the 16x64 tile), but bench
  8461 vs 5757 us at T=1024/n=65536 (+47%); Bk-resident 16x64 variant even
  worse (9283 us). The extra threadgroup memory halves occupancy and the
  occupancy cost dominates the staging savings -- the scorer is
  concurrency/barrier-bound, not staging-bound. DSv4 Lever-1's "not
  promoted" verdict now has a Qwen3.8 twin.
- Meaning for the 11.9/21.8 TF/s plateau: it is NOT tiling/staging. The
  remaining levers are the MMA datapath itself (oMLX #4020-class: move
  score+attn to tensor units / MPP-style specialization) -- a much larger
  project than the queue slot assumed -- or accepting the plateau.
- KEEP from the experiment: the bit-exact mm-vs-variant harness pattern
  (env-toggle, byte-compare across guard shapes) is the right pre-bench
  gate for any future scorer/attn kernel work; bring-up lesson pinned here:
  the epilogue enumeration bound must be tokens x blocks, not rows x
  blocks -- a wrong constant there let band 0 scribble band 1's rows with
  C-garbage and looked exactly like a staging bug.

## MoE/GDN prefill recon SEED 2026-10-04 (13:5x, from QWEN4_BENCH matrix,
model-free, M5). The last queue item's starting data:
- Kernel-choice wins are SHAPE-SPECIFIC and already mixed: prefill T=256 —
  router f32: dense-mm 142us vs custom 727us (mm 5.1x faster); q8 gemm
  6144x2560: custom 258us vs mm 795us (custom 3.1x faster); hc down f16:
  mm 272 vs custom 417 (mm faster); hc up f16: custom 69 vs mm 208
  (custom faster). => a per-(type,shape,rows) dispatch audit of the ACTUAL
  prefill path (which of the two arms every projection takes at chunk=8192)
  is step 1; several arms may be on the wrong side of that crossover.
- moe q4k/mxfp4 32-expert T=2048 x10 slots = 6643us/layer-chunk-arm; the
  live moe bucket is ~2.1s per 8192-token chunk (40% of that sits in the
  routed experts): compare against grouped-path rates at 8192 rows.
- gdn scan r4 T=1024 = 1137us/layer -> x36 linear layers x 8 chunks ~ too
  low to explain the 1.5s gdn bucket alone: the bucket is qkv/z/ga gemv +
  conv + prep + scan + out; attribute per-kernel inside the live path with
  TIMING=2 sub-buckets or a DS4_METAL_* stage profile before touching.
- Ceiling check: dense mm reference 21.8 TF/s; at 8192-row chunks Q4
  experts should approach it if occupancy holds.
