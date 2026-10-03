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
   the omlx gap is fundamentally there, not in QSA. Both need their own
   recon pass (no candidates banked yet; check grouped/dense MM choices at
   8192-row shapes vs `metal_prefill_variant_bench`).
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
4. **C4 — attn_mm KT 16->32**: the biggest attn-bucket item but NOT
   bit-exact (online-softmax accumulation order) => moves the committed
   stream => operator golden decision (HARD RULE stop-and-ask); only ever
   after the exact-world items land.
5. Decode-side residual from 0': per-row split ladder in the T=2 decode
   dispatch (~0.5 ms/cycle, FUSED-VERIFY-DESIGN B2 historical spec).

## 5. Caveats recorded

- TIMING=2 sync inflates configs with different dispatch counts (bit
  twice today: rowe read +6ms/cycle slower under sync yet is -1.8ms in
  the async wall). For cross-config deltas use A/B knobs + MTP_PROFILE.
- Bench n_blocks=65536 is the 256k shape; at 64k everything scales ~B/64k
  linearly for score/select, attn_mm depends on top-k rows not B.
- prefill_probe filler ~1.3 tok/word; prompt_tokens from usage is truth.
