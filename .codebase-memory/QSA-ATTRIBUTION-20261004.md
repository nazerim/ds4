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

## STEP 1+2 LANDED 2026-10-05 (engine-quiet window, zero engine cycles; harness rc=0)

**Step 1 — arm census (tool: `tests/spec_economics/gguf_arm_census.py`, reads
GGUF tensor-info straight from the header; tensor dtypes are GGML enum ids —
the DS4 table at ds4.c:2356).** The HANDOVER-20261004 lead is **disproven for
this model**: `Qwen3.8-Flash-Next-Q4.gguf` carries **no F16 projections at
all** except the HC pair — the census groups: dense projections (attn q/k/v/o,
qkv, gate, indexer q/k, ple key/value, ssm_out, shared experts, nextn eh_proj,
head) all **Q8_0**; router ffn_gate_inp **F32**; hc_ffn_down/up **F16**
(in 10240/out 320 and reversed); routed experts **Q4_K** gate/up + **MXFP4**
down (dtype 39); norms/conv/alpha/beta F32. At chunk=8192 on M5:
- Q8_0: `qwen4_gemv_rows` mm windows (f16<=64, q8-bmm<=32) are closed, BUT the
  custom q8 path itself prefers the **tensor-unit NAX mm kernels** at aligned
  >=32 rows (ds4_metal.m:19600 `kernel_mul_mm_q8_0_f32_nax_direct_rhs*`, with
  `prefill_unpack` f16-dequant fallback at >=32 generic rows) — so Q8 prefill
  is ALREADY on the fast arm family. No action.
- Router F32: takes dense-mm already (n_tok>8 clause). No action.
- **The only custom-vs-mm arm decision left in prefill is the HC F16 pair.**

**Step 1b — bit-exactness of that swap (`test_f16_arm_bitexact`, now permanent
in tests/test_qwen4_kernels.c, informational print, both arms x T={256,2048,
8192}, real HC dims):** verdict **DRIFTS everywhere** — hc down: byte-diff
99.997% of elements, max|d| 1.59e-4/1.74e-4/1.75e-4; hc up: 3.3e-5..3.6e-5.
So routing HC down to dense-mm (bench: 417→272 us at T=256, ~1.5x) is NOT a
free win: it moves the committed prefill stream (first token onward) =>
**operator golden decision, same class as the ST flip. NOT taken** — HC is
~13% of prefill and only the down-half of that arm benefits (up stays custom:
mm is 3x SLOWER there), realistic payoff a low-single-digit % of prefill wall
against a golden re-capture + a 3rd tree to maintain. Decision package
(battery-divergence sample under `DS4_QWEN4_PREFILL_F16_MM` knob) stays
constructible-on-demand; upstream #1179 independently confirms the direction
of travel (they force `legacy = exact` — matvec trees — in verify mode, never
mm).

**Step 2 — k32 MoE-mm port + bench (implemented, MEASURED FLAT, reverted):**
template `<uint NT, uint KS>` on `kernel_qwen4_moe_mm_mid/down` + `nt8_k32`
instantiations + `DS4_QWEN4_MOE_MM_KS` gate (same K order — accumulates the
same 8-wide sub-blocks ascending, so identity was expected). Our tree already
has `qwen4_mm_stage8`. M5 Max, Q4_K/MXFP4 bench (32 experts T=2048 x10 slots,
nt pinned 8 both sides): KS64 **10360.5/15380.5** vs KS32 **10382.5/15453.4**
us (32-expert/256-expert rows) — flat-to-slightly-worse: the occupancy
motivation is M3-Ultra-specific (matches #1179 gating it to that device +
Q8_0). Negative banked; tree restored (metal + host reverted), engine never
touched.

**Consequence for the queue:** the "arm selection" hope is mostly spent — the
live prefill path was already on the right arms for this quantization; the
remaining MoE/GDN potential (§4 item 1: 1.2-1.3x on moe 39% + gdn 28%) now
points at the *kernels themselves* (grouped-expert rates at 8192 rows vs the
mm tile path; GDN bucket decomposition per QSA-ATTRIBUTION SEED item 2),
not at the dispatch gates. Next cheap engine-free step: extend the bench to
true 8192-row moe-mm shapes (x nt variants) to price nt8-vs-alternates at the
live chunk size; then GDN per-kernel attribution before touching anything.

## BENCH EXTENSION + LIVE CONFIRM 2026-10-05 evening (engine stopped only for
## the TIMING=2 measurement, production restored PID 849, 0 WARN)

New durable bench arms (tests/test_qwen4_kernels.c): moe-mm at **T=8192**
(32-expert dense) and **512-expert live pack at T=8192 and T=2048** (shared
~1 GB weight arena); GDN prefill decomposition at T=8192 (qkv-proj q8 NAX /
ab-gemv+conv+prep / scan r4).

- **MoE geometry is NOT a lever.** q4k/mxfp4 32e T=8192: nt8(default) 26665 /
  nt4 26609 / nt2 26678 / nt1 26569 us — flat ±0.4%. 512e live chunk (T=2048,
  nt4 default): 10211.8 us; nt1 10147-10207, nt2 ~same — flat. Sparse 512e at
  T=8192 costs +16% vs the 32e dense proxy (31055 vs 26665 — tile
  fragmentation at ~160 pairs/expert); 512e T=2048 x 49 layers ≈ 500 ms ≈
  the live 582 ms bucket ✓ proxy validated.
- **GDN bucket decomposed** (per layer-chunk at T=8192): qkv proj (q8 NAX)
  **9.09 ms** | ab-gemv+conv+prep **4.23 ms** | scan r4 **9.73 ms**; with z
  (same shape as qkv) + lin-out (~5.5 ms scaled) the live 36-layer set sums
  ~37 ms/layer-chunk ≈ 1.34 s ≈ the 1.5 s bucket ✓. Split: **~60% projections
  (already tensor-unit), ~26% scan, ~12% conv+prep.**
- **Live TIMING=2 ratio confirmation** (production batched config, chunks run
  at T=2048 not 8192 — noted; pos>=40k samples): moe 582 / attn 395 / gdn 386
  / hc 242 / ple 18.6 ms per 2048-chunk => **36/24/24/15/1%** — consistent
  with the §1 shares.
- **Verdict: the MoE/GDN "arm selection" era is CLOSED.** Every dispatch
  window (f16 cap, q8 windows, nt tiles, k32, router mm) is either already
  taken or flat. Remaining levers are kernel-datapath class only: the scan
  (~26% of gdn = ~6% wall) is the one non-matmul candidate; everything else
  needs the tensor-unit/MPP rewrite (oMLX #4020 / #1149-class), a project,
  not a queue item.
- **Tail tallies done too:** Scenario L field = **0 `frontier-superseded`
  events** through the Oct-5 real-session day (17 evict + 109 continued + 10
  cold + 34 token-mismatch deletes; sweep runs on every evict pass,
  ds4_kvstore.c:1710 — armed, no traffic matched its condition; reclaim 0
  MiB). R2b field measurement is **not constructible from current logs** —
  the `kv cache hit ... load=NNN ms` line carries no chain depth (samples
  today: 121.6 ms @4096, 1609.4 @175252, 2691.1 @163840, 2777.5 max); a one-
  line enrich (depth= in the hit line) would make it measurable.
