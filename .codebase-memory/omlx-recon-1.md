# oMLX → ds4 Recon (Qwen3.8 Flash Next, M5 Max)

Scope note: oMLX local tags stop at v0.6.2; HEAD (ac34387) is past it. No `v0.7.0rc1`
tag exists locally, so "60 commits before v0.7.0rc1" was approximated as the ~70
commits ending at HEAD. Recon was budget-limited: Scratch/ notes and ds4-tree
verification were NOT completed (see "Could not determine").

## Candidate table

| Technique | What it does | oMLX evidence | Measured speedup | Portability | Rationale |
|---|---|---|---|---|---|
| Fused GDN prework kernel (verify widths S=3..9) | One Metal launch replaces ~10 dispatches/GDN layer/verify cycle: conv-state concat, depthwise conv1d, SiLU, q/k/v split, 2x ones-weight RMS norm, scalar scales, next-conv-state slice | `omlx/patches/qwen35_gdn_prework.py` @ 293d697 | 48 layers x ~0.29 ms @ S=4 on M3 Ultra (~14 ms/verify cycle); "largest remaining verify-cycle cost" | **High** | It IS a Metal kernel; ds4 has the same GDN + MTP-verify structure |
| Verify SDPA split (chunked causal vector attention) | Replaces per-row SDPA fallback loop (L dispatches + 2L slices + concat per layer) with ceil(L/row-limit) causal block calls, bottom-right aligned | `omlx/patches/qwen35_verify_sdpa_split.py` @ 293d697 | +4..9% decode with speculation on (mlx-serve `splitCausalSdpa`) | **High** | Pure scheduling/tiling change to verify-width attention; no new numerics |
| Fused MoE decode router top-k | One simdgroup-per-row launch replaces softmax→argpartition→take_along_axis→renorm (5 tiny ops/layer/token); bit-identical selected-expert set | `omlx/patches/qwen35_moe_router.py` @ a9de32c | ~51 µs → ~5 µs/layer; ~2 ms of ~9 ms decode step (Qwen3.6-35B-A3B, 40 layers) | **Medium-High** | If Qwen3.8 Flash Next is MoE (Qwen3-Next family is), direct decode win; kernel is plain Metal |
| NAX qmm GPU kernels for M5 | Genuine group-size-64/128 quantized-matmul Metal kernels using M5-family tensor units; used as ANE-hybrid GPU suffix, falls back to classic Metal | `omlx/custom_kernels/qwen35_prefill/csrc/qwen35_qmm_nax.metal` @ db1423d | Resolved a prefill regression on M5 vs tensor-unit path (no absolute tok/s recorded) | **Medium** | Pure .metal source; target hardware is exactly ds4's (M5 Max); needs porting of q4/q6/q8 affine layouts |
| q8 linear token-threshold routing | Native q8 tile only pays above `OMLX_QWEN35_Q8_LINEAR_MIN_TOKENS` (default 16384); below that use stock path for GDN b/a projections | `omlx/patches/qwen35_q4_mlp.py`, docs @ ac34387 | No tok/s; encodes measured crossover | **Medium** | Cheap heuristic for ds4's dequant/kernel-selection path at prefill |
| Affine q6/q8 (+oQ4e) ANE prefill | Requantizes MLP gate/up + GDN z/qkv to per-channel INT8, runs fixed-shape programs on ANE, merges SwiGLU natively; approx (not bit-exact) | `omlx/patches/qwen35_ane_prefill.py`, `csrc/qwen35_ane.mm` @ 12841f2, b2873ed, fbb98dc | No absolute numbers in docs; split-bank A/B ~1% prefill vs monolithic | **Low** | Private AppleNeuralEngine APIs, ObjC++, fixed-shape compile at startup; M5 Max is single-die (bank load failures documented); high risk |
| Dual-ANE bank packing / split tuning | Packs 112 fixed-shape procedures into 2 resident ANE programs; tuner benchmarks GPU-only / MLP / MLP+GDN splits | `omlx/admin/ane_tuning.py` @ db1423d, d7e43a4 | Split banks ~1% faster prefill, but monolithic bit-stabler | **Low** | Same private-API constraint; only useful as methodology for split tuning |
| Scheduler: batch under contention + chunk-align prefill to fixed shape | Prefers batching when scheduler contended; aligns prompt chunk size with ANE shape; reserves last token for decode kickoff | `omlx/scheduler.py` @ fc640a8, fbb98dc | No numbers | **Medium** | Chunk-alignment idea applies to ds4 prefill chunking even without ANE |
| DFlash 2 draft defaults | Draft window default 2048, draft sink 0, per-request seed/min_p plumbing | `omlx/engine/dflash.py` @ cfa6653, c1a3d44 | No numbers | **Medium** | Tunable draft-verify window params worth A/B against ds4 MTP defaults |
| GDN SSD exactness toggle | Restores exact GDN SSD output by default vs approximate cached path | `omlx/settings.py` @ 49b4cea | No numbers | Low | Correctness knob, not perf |

## Top 3 for ds4 Qwen3.8 Flash Next

1. **Fused GDN prework for MTP verify widths** — hook: `metal/` kernels + MTP
   draft/verify path (GDN recurrence subsystem). Port the kernel from
   `qwen35_gdn_prework.py` (self-contained Metal source in `_SOURCE`). Hard gate:
   only for verify width S>=3; decode (S=1) and prefill keep existing path.
   Biggest single measured verify-cycle cost removed in oMLX (~0.29 ms x #GDN
   layers per cycle).
2. **Verify SDPA split (chunked causal attention)** — hook: ds4 MTP verify
   attention (metal/ attention kernels / verify batch construction). Replace any
   per-draft-row attention loop with row-limit-chunked causal calls against
   `keys[:kv_len-(L-c1)]`. +4..9% decode with speculation measured upstream;
   low risk, no new kernel math.
3. **Fused MoE decode router top-k** — hook: metal/ kernel set + decode path
   (batched-session decode benefits most). One simdgroup per row; must replicate
   tie-break-to-highest-index to keep expert selection bit-identical. ~10x on
   the router chain, which was ~20% of a decode step on a comparable MoE.

Runner-up for M5 Max specifically: the NAX qmm Metal kernels (db1423d) — only
oMLX artifact tuned for M5 tensor units that is pure Metal and portable.

## Already in ds4 (do not duplicate) — partially verified

From task description + AGENTS.md (ds4-tree grep NOT run, budget): MTP
speculative decoding, chunked prefill, GGUF Q4 loading, hybrid GDN/full-attention
Qwen3-Next architecture, KV cache v2, DSpark. The oMLX candidates above are
kernel-level fusions *inside* those subsystems, not reimplementations of them.

## Could not determine

- Scratch/ prior-work notes and measured tok/s (omlx, omlx-ops, omlx-glimmer,
  dflash-mlx, eagle, benchmark, ds4-kv-cache, mistral4-large-context dirs exist
  but were not read — tool budget).
- ds4-side verification: `DS4_QWEN4_PREFILL_CHUNK`, MTP flags, metal kernel
  names not grepped; whether ds4 verify attention already chunks causally, and
  whether Qwen3.8 Flash Next in ds4 is MoE (candidate #3 depends on it).
- Absolute prefill tok/s gains of ANE hybrid path on M5 Max (docs describe
  relative results only; dual-ANE is M3-Ultra-oriented, single-die chips hit
  bank-load failures).
- v0.7.0rc1 content: tag absent locally.
