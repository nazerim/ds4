# oMLX → ds4 recon, pass 3 — blockers closed (read-only)

Model under test: Qwen3.8 Flash Next (`DS4_SHAPE_QWEN4_EXP`, ds4.c:803-840), Metal, M5 Max 128 GiB.
No builds, no engine starts, no writes outside this file. Supersedes the "unverified" items in
`.codebase-memory/omlx-recon-2.md`.

## Section A — ds4 blockers

### 1a. MoE? YES — routed, 10 of 512 active + 1 shared
- `DS4_N_EXPERT_USED` = `g_ds4_shape.n_expert_used` (ds4.c:937); for Flash Next **10** (ds4.c:817).
- `DS4_N_EXPERT` **512** (ds4.c:816), `DS4_N_EXPERT_SHARED` **1** (ds4.c:818), `DS4_N_FF_EXP` **640** (ds4.c:819).
- GGUF cross-check is hard-fail, not advisory: `config_expect_u32("expert_used_count", required_u32(m,"qwen4exp.expert_used_count"), DS4_N_EXPERT_USED)` (ds4.c:7063); also `expert_count` (:7062), `expert_feed_forward_length` (:7066), `expert_shared_feed_forward_length` (:7068).
- Scratch estimator confirms routed FFN sizing: `(DS4_N_EXPERT_USED + 1u) * (E + 2u*DS4_N_FF_EXP)` (ds4.c:39663).
- **Candidate 3 premise (multi-expert routed MoE) is alive.** But see 1e/B: the router is already one fused kernel.

### 1b. MTP verify width: **T = 2 (default) or 3 (adaptive). Never 4+.**
- `ds4_engine_mtp_draft_tokens()` returns a hard **2** for qwen4 (ds4.c:62302-62304); `DS4_N_NEXTN_PREDICT = 1` (ds4.c:826), one MTP block at `DS4_N_LAYER-1`.
- Verify cycle: `ds4_session_qwen4_spec_cycle` (ds4.c:74355). `const int toks[3] = {first_token, d, d2}; const uint32_t T = deep ? 3u : 2u;` (ds4.c:74411-74413). Row logits buffer sized `4u*V` (ds4.c:74416).
- Depth policy `qwen4_spec_depth` (ds4.c:74321-74341): `DS4_QWEN4_MTP_DEPTH=2|3` forces fixed depth; default auto returns **2**, promotes to **3** only on a perfect 8-deep first-draft acceptance window (`bits>=8 && cycles>=8 && reject2_streak==0`), demotes at `bits<6 || reject2_streak>=2`.
- Comment at ds4.c:74331-74334 records the measured rationale: second draft accepts ~0.6 on prose (does not cover the wider cycle), ~0.95+ on deterministic continuations (+10-20%).
- Exact sampling or `DS4_QWEN4_NO_MTP_BATCH` pins depth to 2 (ds4.c:74366-74367).
- `qwen4_graph_mtp_steps` hard-caps predictor rows at `T > 3u → return false` (ds4.c:59055).
- **Verdict on the oMLX premise: S ∈ {2,3}. The S=3..9 window the oMLX prework targets is effectively unreachable in ds4 without raising `DS4_N_NEXTN_PREDICT` (a model-weight property) — plain NO for candidate 1 as scoped.**

### 1c. Verify attention is already multi-row/batched — and deliberately narrowed at T=3
- `qwen4_graph_attention_core` (ds4.c:58404) issues ONE batched call for all rows of the sub-batch: `ds4_gpu_qwen4_attn_decode_tensor(..., n_dense, ...)` (ds4.c:58415-58420) plus a second for the sparse tail (ds4.c:58443-58447). No per-row loop, no concat.
- Attention is dense-prefix + sparse-tail over pooled block keys (indexer/NSA style), split at `sparse_pos = (k_blocks+1)*ratio - 1`, `ratio = 4` (ds4.c:58411-58414). Kernels: `kernel_qwen4_attn_decode` / `_rows` (metal/qwen4.metal:2188, :2225), `kernel_qwen4_attn_merge{,_wide,_rows}` (:2254, :2330, :2346), `kernel_qwen4_idx_score{,_vec,_rows,_mm}` (:1536, :1605, :1628, :1656), `kernel_qwen4_idx_select` (:1726), `kernel_qwen4_idx_expand{,_rows}` (:2055, :2072), `kernel_qwen4_attn_prep{,_rows}` (:1390, :1422), `kernel_qwen4_idx_block_key{,_rows}` (:1492, :1508). Host dispatchers in `ds4_metal.m` (`ds4_gpu_qwen4_attn_decode_tensor`, `..._idx_score_tensor`, `..._idx_select`, `..._idx_expand_tensor`, `..._attn_prep_tensor`).
- At T=3 with `verify_rows_exact` (set only for the deep verify, ds4.c:74429-74430; field declared ds4.c:57652), `qwen4_graph_attention_tail` **splits into 2+1-row sub-batches on purpose** so every dispatch keeps the exact T≤2 kernel rounding (ds4.c:58479-58508). Same 2+1 exactness split exists in `qwen4_graph_hc_mix` (ds4.c:58322-58347).
- This is the *inverse* of oMLX's `qwen35_verify_sdpa_split`, which widens per-row loops into chunked causal calls. ds4 has no per-row SDPA loop to collapse.

### 1d. GDN layer dispatch count at verify width: **6 for the GDN block; prework already ONE dispatch**
`qwen4_graph_linear` (ds4.c:58353-58398), per GDN layer, T∈{2,3}:
1. `qwen4_gemv(g->qkv, l->lin_qkv)` — 1
2. `qwen4_gemv(g->z, l->lin_gate)` — 1
3. `ds4_gpu_qwen4_gdn_front_tensor` — **1** (ds4.c:58368-58373; host ds4_metal.m:50250; kernel `kernel_qwen4_gdn_front` metal/qwen4.metal:4545, args :4525)
4. `ds4_gpu_qwen4_gdn_scan_tensor` — 1 (ds4.c:58385; `kernel_qwen4_gdn_scan` metal/qwen4.metal:708)
5. `ds4_gpu_qwen4_gdn_out_tensor` — 1 (ds4.c:58391; `kernel_qwen4_gdn_out` :937)
6. `qwen4_gemv(g->blk, l->lin_out)` — 1
`gdn_front` already fuses exactly oMLX's prework list: conv-state concat/history shift, depthwise conv1d, SiLU, q/k split, q/k L2-norm, 1/sqrt(D) scale, alpha/beta projections (folded GEMVs), `g = exp(ssm_a*softplus(a+dt_bias))`, `beta = sigmoid(b)`, next-conv-state slice, **and both MTP snapshots** (metal/qwen4.metal:4539-4600; snap_tok/snap2_tok fields :4533-4534).
Gate: `qwen4_graph_fused(g,T)` (ds4.c:58283-58290) — true for T≤2, and for T==3 only when `verify_rows_exact`; `DS4_QWEN4_NO_FUSE=1` forces the unfused path (**built-in A/B lever**, ds4.c:58280, :58285).
Unfused fallback = 4 dispatches instead of 1: alpha GEMV + beta GEMV + `conv_stream` + `gdn_prep` (ds4.c:58375-58383; kernels `kernel_qwen4_conv_stream` metal/qwen4.metal:473, `kernel_qwen4_gdn_prep` :652).
Full GDN-layer total ≈ **18 dispatches** at T=2 (≈20 at T=3): hc_mix(attn) 3 (ds4.c:58283 region, `hc_norm`+`lo` gemv+`hc_gate_mix`) + linear 6 + `hc_combine` 1 (ds4.c:58815) + hc_mix(ffn) 3 + MoE 5 (router gemv, `router_topk`, `moe_mid`, `moe_down`, `moe_reduce` — ds4.c:58544-58547, :58654-58669).
Layer mix: trunk = `DS4_N_LAYER - DS4_N_NEXTN_PREDICT` = **48**; `(il+1) % 4 != 0` → **36 GDN layers, 12 full-attention** (ds4.c:1013-1018, `n_full_attn_interval = 4` ds4.c:829). Layer 1 is also PLE (ds4.c:1020-1022).
Multi-row/batched variants already exist for wider batches: `conv_stream_rows{,2}` (metal/qwen4.metal:517, :569), `gdn_scan_rows{,2}` (:785, :830), `conv_halo` (:607), `conv_blocked` (:618), `gdn_scan_r4` (:877); dispatched at ds4.c:78758-78802 and ds4.c:79174-79185.

### 1e. ds4-bench CLI + verify-depth targets
- Usage (ds4_help.c:123): `ds4-bench (--prompt-file FILE | --chat-prompt-file FILE) [options]`. Defaults (ds4_bench.c:216-224): model `ds4flash.gguf`, `--ctx-start 2048`, `--ctx-max 32768`, `--step-incr 2048`, `--step-mul 1`, `--gen-tokens 128`.
- Sweep/IO options (ds4_help.c:390-404): `--ctx-start/--ctx-max/--ctx-alloc/--step-mul/--step-incr/--gen-tokens/--teacher-forced-decode/--csv FILE/--dump-frontier-logits-dir DIR`. Common: `--prefill-chunk N`, `--power N`, `-t/--threads`, `--quality`, `--warm-weights`, `--simulate-used-memory NGB`, `--ssd-streaming*`, `--expert-profile FILE`, `--mtp-model FILE`, `--dspark`, `--dspark-confidence F` (ds4_help.c:170-203, ds4_bench.c:271-283).
- **Prefill and decode ARE reported separately.** CSV/stdout header (ds4_bench.c:782): `ctx_tokens,prefill_tokens,prefill_tps,gen_tokens,gen_tps,gen_first_ms,gen_steady_tokens,gen_steady_tps,kvcache_bytes`; row written at ds4_bench.c:1020-1030. `gen_steady_tps` excludes the first-token latency, so it is the decode number to compare.
- Canonical invocations (ds4_help.c:506-507, :524-525): `./ds4-bench --prompt-file long.txt --ctx-max 32768`; `--csv speed.csv`; **prefill only** `--gen-tokens 0`.
- **BLOCKER for MTP measurement:** `ds4_engine_options` built in ds4_bench.c:648-672 never sets `.glm_mtp`. The qwen4 spec cycle requires `s->engine->glm_mtp` (ds4.c:84531). So **ds4-bench cannot exercise the Qwen3.8 MTP verify path at all** — only prefill and greedy/DSpark decode. `--dspark` needs an external support GGUF (ds4_bench.c:389-390) and is a different mechanism (`e->dspark_weights.block_size`, ds4.c:62310-62318).
- MTP verify must be measured with the `ds4` CLI: `--mtp` sets `glm_mtp` (ds4_cli.c:2019-2020), `--mtp-timing` sets `glm_mtp + glm_mtp_timing` (ds4_cli.c:2027-2029) and prints `ds4: Qwen3.8 mtp: N verify cycles, M drafts accepted (P%)` at session free (ds4.c:73575-73578). Per-forward timing: `DS4_QWEN4_TIMING=1` → `Qwen3.8 forward(T=n) avg ms: stage/encode/gpu/read` every 50 calls (ds4.c:58866-58873); `DS4_QWEN4_TIMING=2` → per-stage-group GPU ms `ple hc_attn gdn attn hc_ffn moe` (T>1 only, adds syncs; ds4.c:58831-58836). Per-cycle accept trace: `DS4_QWEN4_SPEC_TRACE` (ds4.c:74293-74295). MoE stage profile: `DS4_QWEN4_MOE_PROFILE` (T>8 only, ds4.c:58543).
- `make mtp-verify-depth` (Makefile:1049-1057): skips unless `$DS4_TEST_MODEL` and `$DS4_TEST_MTP` exist, else runs `DS4_TEST_MODEL=... DS4_TEST_MTP=... ./ds4_test --mtp-verify-depth` — a **legacy-MTP correctness smoke**, not a tok/s harness.
- `make dspark-verify-depth` (Makefile:1039-1047): same shape with `$DS4_TEST_DSPARK`, `./ds4_test --dspark-verify-depth`. `make dspark-acceptance` (Makefile:1034-1037) runs `sh tests/dspark_acceptance_fixture.sh` with `DS4_DSPARK_MODEL`/`DS4_DSPARK_SUPPORT`.

### 1f. PLAN-PREFILL-M5.md — chunk size is CLOSED, do not re-litigate
- "**Out of scope**: MoE gemm (roofline), attention kernels (0.4% share), **chunk size (plateaued)**, thermal (~5%, flat), **ANE (dead track)**, HC/sinkhorn" (PLAN-PREFILL-M5.md:83-87).
- DS4FORK.md:1227-1228 (the measurement behind it): "`--prefill-chunk` 2048/4096/8192: 2048 wins @2k, loses @65k; 4096 ≈ 8192. **Default 4096 already optimal. Closed.**"
- Exactness doctrine (PLAN-PREFILL-M5.md:29-35): `metal_prefill_variant_bench` fails on ANY logit reorder (129278/129280 floats differ at ~0.3% from accumulation order alone) → every lever ships env-gated → balanced A/B → promote only with `--quality` keeping the reference path.
- Status log: Lever 1 (indexer NAX tile retune) **marginal, not promoted** (81.5→80.3 ms/call ≈ 0.3% prefill, bit-exact; single-K cooperative variant 90.9 ms = regression) (:88-92). Lever 2 (topk sidecar merge) **negative, hypothesis falsified, reverted** (28.6 vs 27.9 ms; gathers were L1-resident) (:93-99). Lever 3 (f16 score buffer) **dropped** (:100-103).
- Ceiling statement: "the honest ceiling on M5 Max with this model was ~0-3%, not the 10-15% estimated from the TF/s plateau alone. The prefill path is within measurement noise of its tuned limit" (:114-121).
- Note the scope caveat: that attribution ran on DeepSeek-V4 q2-q4 `fixed-0731`, not Qwen3.8 (DS4FORK.md:1196). ds4's qwen4 chunk default is **8192** via `DS4_QWEN4_PREFILL_CHUNK` (ds4.c:39636-39641), separate from `--prefill-chunk`/`DS4_METAL_PREFILL_CHUNK` (ds4.c:14315); it also sets `cap_tokens` for the whole qwen4 arena (ds4.c:73162-73163) and feeds the memory estimator (ds4.c:39652).
- Sanctioned experiment loop with no rebuild: source-injection envs `DS4_METAL_DSV4_MISC_SOURCE`, `DS4_METAL_ARGSORT_SOURCE`, `DS4_METAL_MOE_SOURCE` (PLAN-PREFILL-M5.md:22-25, ds4_metal.m:4446).

## Section B — verdicts

1. **Fused GDN prework (verify widths S=3..9) — NO-GO (already implemented, and the width premise fails).**
   Hook that already exists: `kernel_qwen4_gdn_front` (metal/qwen4.metal:4545) ← `ds4_gpu_qwen4_gdn_front_tensor` (ds4_metal.m:50250) ← `qwen4_graph_linear` (ds4.c:58368-58373), gated by `qwen4_graph_fused` (ds4.c:58283). It fuses every item on oMLX's list plus both MTP snapshots. Independently, ds4's verify width is 2 or 3 (1b), so oMLX's S=3..9 amortisation never applies. Risk of porting: strictly negative — duplicate kernel, new numerics against a bit-exactness doctrine (1f). Residual opportunity, if any: the **unfused** alpha/beta GEMV pair is already folded, so the only remaining per-GDN-layer fat is `gdn_scan` (sequential recurrence, 1 dispatch, metal/qwen4.metal:708; wider variants `_rows/_rows2/_r4` at :785/:830/:877 are used for batched decode, not for verify) — measure it with `DS4_QWEN4_TIMING=2`'s `gdn` bucket before touching anything.
2. **Verify SDPA split / chunked causal attention — NO-GO (inverted premise).**
   Hook: `qwen4_graph_attention_core` (ds4.c:58404) already makes one batched multi-row call per sub-batch; `qwen4_graph_attention_tail` (ds4.c:58455) already *narrows* T=3 into 2+1 for bit-exactness (ds4.c:58479-58508). There is no per-row SDPA loop to collapse, and ds4's attention is block-sparse (indexer top-k over ratio-4 pooled blocks), not dense causal SDPA — oMLX's `keys[:kv_len-(L-c1)]` bottom-right construction has no ds4 analogue. Risk: a "widening" port would break the exactness split at T=3 and change accepted-token behaviour.
3. **Fused MoE decode router top-k — NO-GO (already implemented).**
   Hook that already exists: `kernel_qwen4_router_topk` (metal/qwen4.metal:1122, args :1102, `QWEN4_ROUTER_MAX_EXPERT 512` / `MAX_USED 16` / `LANE_MAX 2` :1113-1115) ← `ds4_gpu_qwen4_router_topk_tensor` (ds4_metal.m:49135) ← `qwen4_graph_moe` (ds4.c:58544-58547). One 256-thread threadgroup per token does softmax + top-k + renorm **plus the shared-expert gate logit** in a single launch (metal/qwen4.metal:1117-1121) — a superset of oMLX's kernel, which stops at the router. Semantics note: ds4 breaks ties to the **lowest** index (metal/qwen4.metal:1117), oMLX/mlx-argpartition to the **highest** (`qwen35_moe_router.py` docstring) — so an oMLX port would silently change expert selection.
   Live MoE opportunity is elsewhere and already measured/tuned in ds4: `mm_min = 64` rows on Metal (ds4.c:58569-58572) with the comment that 16 rows spread 10 choices over 512 experts leaves tiles nearly empty and the per-token path wins "by 4% at sixteen rows" (ds4.c:58562-58568); grouped expert kernels gated on Q4_K/Q4_K/MXFP4 with `DS4_QWEN4_MOE_NO_GROUP=1` as the A/B (ds4.c:58636-58641).

**Net: all three oMLX candidates are already present in ds4 in equal or stronger form. There is no port to do.** The NAX runner-up from pass 1 is also already in-tree: `kernel_qwen4_moe_mm_mid_nax_t` (metal/qwen4.metal:3915) and `kernel_qwen4_moe_mm_down_nax_t` (:4076).

## Section C — Scratch prior art (measured numbers + source)

Directly on our model/hardware (oMLX, MLX Python — not ds4, but same checkpoint class on M5 Max 128 GiB):
- `Qwen3.8-Flash-Next-oQ4e-mtp`, 106 GB disk / **69.6 GB resident**, 198k ctx: **MTP accept 68-89%, decode ~32-44 tok/s at 40-120k**; **cold prefill degrades super-linearly ~1000 tok/s @40k → ~224 @120k, IO-bound; "ANE prefill does not help, keep off"** — `omlx-ops/README.md:123`.
- Cold prefill matrix, same model (server-measured, non-overlapping nonces): 40k **992 tok/s** / 40.6 s; 80k **560 tok/s** / 145.3 s; 120k **224 tok/s** / 549.4 s — `muse-glimmer/flash-next-perf/perf_results2.json` (README at `flash-next-perf/README.md` warns the `decode_est_tok_s` field in that file is broken; `perf_results.json` is cache-polluted — 80k "38423 tok/s" is a warm-cache artifact, do not cite).
- ANE prefill on this model = **rejected**: fraction sweep off/0.20/0.25/0.30/0.375 at 40k/80k/120k → 0.375 neutral at 120k, worse at 40k; **ANE banks get RELEASED mid-prefill under memory pressure** (19.07 GB freed during one 163k prefill, 800 s) → keep OFF — `flash-next-perf/README.md` "Gotchas", `ane_sweep_results.json`, `ane_160k_results.json`.
- Prefill admission guard under-counts in-flight growth **~8.7x** (measured 237 KB/tok vs 27.28 KB/tok estimated) for qwen4_exp because ArraysCache GDN boundary snapshots (one per 2048-token block × 61 stateful layers) are uncharged; 203k prefill dies at progress 161,792 tok / 903 s — `muse-glimmer/flash-next-perf/upstream_drafts_2026-08-29.md:14-46`. Relevant to ds4 only as a caution: ds4 already reserves all three GDN state copies up front (ds4.c:39668-39671).

ds4 itself, on M5 Max (the closest apples-to-apples):
- **DSpark/spec ON-vs-OFF decode matrix** (`ds4-kv-cache/TODO.md:329-336`, logs `ds4-kv-cache/CacheTest/ds4-all-matrices.log`): 32k ctx OFF **17.9 t/s** / ON **19.9 t/s** / GREEDY **14.1 t/s** → ON/OFF **1.11**; 16k 17.3 / 17.4 / 14.5 → **1.00**; 64k ~19.7 / ~18-20 / ~14-16, unstable (50% cache hit). Short-context ratio was **0.83 (ON slower)**. Reading: **speculation only pays from ~16-32k context up**; at 150-token contexts verify overhead dominates. Also `ds4-kv-cache/README.md`: "DSpark at temp 1.0 does not work on Metal — spec path is gated on greedy (`temperature <= 0.0`)".
- ds4 Qwen3.8 prefill is "fast (~1150 t/s)" — `.codebase-memory/TODO-20260927.md:9` (in-repo, M5 Max).

Speculative-decoding levers already tried and rejected (oMLX/DFlash/EAGLE — transferable policy lessons):
- **Wider/tree verify blocks lose even when acceptance rises**: `verify=dflash` acceptance 51.7%→**57.3%** but tok/s **29.5→16.8** (full-block verify cost); `ddtree` acceptance **60.6%** but **13.7 tok/s** (tree verify 20.5 s) — `eagle/README.md:57-59`. **This is the strongest available evidence that pushing ds4's verify width above 3 would regress.**
- Draft-window 1024→2048 (drafter's full SWA): 28.2→**29.5 tok/s**, acceptance unchanged 51.7%. Sinks 64/256/512: **no effect** — `eagle/README.md:56-60`.
- Acceptance ~52% is the drafter/block-policy ceiling; "context knobs exhausted" — `eagle/README.md:60-61`.
- Drafter weight quantization: bf16 24.6 tok/s (draft 2222 ms) → **w8-runtime 29.5 (228 ms)** → w4a16 gs128/gs64 28.4/28.7 (acceptance 49.8-50.2%, 4-bit qmm slower than 8-bit) → oQ8 26.3 (350 ms). w6 unsupported (2/4/8 only) — `eagle/README.md:51-56`.
- Target-side quantization is the real lever, not the drafter: bf16 9.3 → oQ6 **21.5** → oQ6+DFlash **28.2-29.5 tok/s** (3.2x bf16). bf16+DFlash only 10.3 (per-cycle overhead). Quantizing the bf16 drafter (draft 1939→318 ms) did **not** move wall clock — ~197 ms/cycle unaccounted overhead dominated — `eagle/README.md:39-50`, `omlx-ops/README.md:336-338`.
- Bundled MTP head beats an external draft model decisively: oQ6e+bundled MTP **~52 tok/s** vs oQ6e+external bf16 VLM draft (block 3) **~35.5** vs bf16+bundled **~21** — `omlx-ops/README.md:178-182`. ds4 uses the bundled head (`DS4_N_NEXTN_PREDICT=1`), consistent with the winning config.
- ANE prefill on Qwen3.8-27B-oQ4e (88k-140k): best case **+2.6% @87k (25% fraction) / +4.9% @140k (20% fraction)**; 50% fraction **-20%/-17%**; >30% overhead-dominated — `omlx-ops/README.md:376-395`, detail `omlx-glimmer/docs/experimental/qwen35_ane_prefill.md:244-246`. NAX qmm variant sweep @88k: variants 4 and 7 **+5%** over default 0, variant 1 **-3%** (`OMLX_QWEN35_QMM_NAX_VARIANT`) — `omlx-ops/README.md:417-431`.
- MoE expert offload is a cliff, not a tuning knob: 12.5% resident → **28 tok/s warm / 5.6 tok/s cold**; 25% → 25 / 4.2; at 20% on a tighter budget **1.8 tok/s** and one run measured **646 s to first token, 0.09 tok/s** (slab faulted in) — `omlx-glimmer/docs/MoE_Expert_Offload.md:180-187`, `:283-289`.
- oMLX decode ceiling sanity check on a dense-128B MLA model: ~819 GB/s / 70.6 GB ≈ **11.6 tok/s bandwidth ceiling**, measured 7.4 tok/s @85k — `mistral4-large-context/README.md:584`, `:358-359`. Useful as the "is this memory-bound or kernel-bound" test before any kernel work.
- oMLX-vs-direct mlx-vlm gap: at 25.6k, oMLX ~60 tok/s prefill vs direct 127 tok/s (~2x server overhead); 2k 170 vs 5-6.6 decode — `mistral4-large-context/README.md:556-575`. Reminder that harness overhead can dwarf kernel deltas.
- `omlx/tests/bench_results.csv` and `omlx-glimmer/tests/bench_results.csv` are **test fixtures** (Qwen3.5-4B, synthetic concurrency sweep), not measurements — do not cite.
- `benchmark/` contains no perf numbers at all: it is the remote SWE-bench/Terminal-Bench runner harness (M1 Air → M5 Max via `auth_proxy.py` on 8002), reporting only `acc` — `benchmark/README.md:1-97`.
- `ds4-kv-cache/` is marked **HISTORICAL — CLOSED/FIXED** (anchor instability fixed in the fork; do not re-explore) — `ds4-kv-cache/README.md:3-4`.
- `omlx-glimmer/docs/experimental/qwen35_fp16_decode.md` has **no measured numbers** — it only documents the `Qwen B1/T1 fused GDN prework engaged` log line and warns "alone is insufficient to claim a whole-request speedup" (:15, :37). Confirms oMLX's Qwen4 fused decode prework exists but was never quantified.

## Section D — highest-value lowest-risk first change

**Do not port anything. The single highest-value, lowest-risk action is a zero-code measurement that either kills or justifies the only remaining live lever — raising the verify width — and it uses env knobs that already exist.**

Why this one: Section B shows all three oMLX ports are already in ds4, and Section C shows the two things that actually moved tok/s on this model class were (i) speculation economics and (ii) target-side quantization/memory residency — not kernel fusion. ds4's own depth policy already encodes a measured claim (second draft accepts ~0.6 on prose, ~0.95+ on deterministic continuations, +10-20% when depth 3 engages; ds4.c:74331-74334) that has never been re-validated on this checkpoint, and `DS4_QWEN4_MTP_DEPTH` makes it a one-env-var A/B. `eagle/README.md:57-59` is the prior-art warning that wider verify can regress despite higher acceptance, so this must be measured, not assumed.

Procedure (no rebuild; two processes, sequential — never concurrent, this box is at 128 GiB with a 70 GiB map):

Before (baseline, 3 runs each, median):
1. Prefill/decode harness (1e): `./ds4-bench -m ds4flash.gguf --prompt-file <fixed 40k prompt> --ctx-start 40960 --ctx-max 40960 --gen-tokens 256 --csv /tmp/base.csv`. Record `prefill_tps` and `gen_steady_tps`. This is the non-MTP reference and the only ds4-bench-valid number.
2. MTP harness (ds4-bench cannot do this, 1e): `./ds4 --mtp-timing -m <qwen4 gguf> --prompt-file <same> -n 512 --temp 0` with `DS4_QWEN4_MTP_DEPTH=2` and `DS4_QWEN4_TIMING=1`. Record wall tok/s, the `Qwen3.8 mtp: N verify cycles, M accepted (P%)` line, and `forward(T=2) avg ms: gpu`.
3. Same with `DS4_QWEN4_MTP_DEPTH=3`; record `forward(T=3) avg ms: gpu` and the depth-3 acceptance.
4. Same with `DS4_QWEN4_MTP_DEPTH` unset (auto policy) to see how often depth 3 actually engages; add `DS4_QWEN4_SPEC_TRACE=1` on one short run to confirm.
5. Fusion A/B, zero code: repeat step 2 with and without `DS4_QWEN4_NO_FUSE=1`. The delta **is** the value of the already-landed fused GDN prework across 36 GDN layers — this retroactively prices oMLX candidate 1 on ds4 hardware and closes the question permanently.
6. Stage attribution if step 5 is interesting: `DS4_QWEN4_TIMING=2` on a T>1 batch gives per-group GPU ms (`ple hc_attn gdn attn hc_ffn moe`, ds4.c:58831-58836); `DS4_QWEN4_MOE_PROFILE` only fires at T>8 so it will not help at verify width.

After (only if a change is warranted):
7. If depth 3 wins by >3% sustained on real prose, the change is to the **policy constants** in `qwen4_spec_depth` (ds4.c:74333-74340: `bits >= 8u`, `bits < 6u`, `qwen4_reject2_streak >= 2u`) — not a kernel. Re-run steps 2-4, plus `./ds4-server` spot-check on a real conversation, and confirm the `verify_rows_exact` 2+1 splits (ds4.c:58479, ds4.c:58322) still produce identical accepted tokens.
8. Any kernel change must follow the PLAN-PREFILL-M5.md:29-35 doctrine: env-gate → `metal_prefill_variant_bench` balanced A/B → promote with `--quality` keeping the reference path.

Residual risks: (a) all Section A/B line numbers are from the current working tree, which is mid-work — re-grep before editing; (b) no live measurement was taken in this pass (hard constraint), so the depth-3 and NO_FUSE deltas are still unknown; (c) ds4-bench's inability to set `glm_mtp` means every MTP number must come from `./ds4 --mtp-timing`, whose wall-clock includes tokenizer/CLI overhead — compare only like-for-like; (d) Scratch oMLX numbers are MLX-Python on a different quant (oQ4e, 69.6 GB resident) than ds4's GGUF checkpoint, so they bound expectations but are not ds4 baselines; (e) `flash-next-perf/perf_results.json` and the `decode_est_tok_s` field of `perf_results2.json` are cache-polluted/broken — excluded above, but they are still on disk and easy to miscite.
