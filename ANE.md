# ANE Prefill Offload — Feasibility Investigation (ds4 server)

Status: **investigated, not adopted** (Aug 2026). No code changes.

Reference implementation: [jundot/omlx](https://github.com/jundot/omlx),
`omlx/custom_kernels/qwen35_prefill/csrc/qwen35_ane.mm` and
`docs/experimental/qwen35_ane_prefill.md`. Field data point: Qwen3.8-27B-oQ4
on M5 Max via oMLX, 25% ANE split → **+5% prefill**.

## How oMLX uses the ANE

- Loads the private `/System/Library/PrivateFrameworks/AppleNeuralEngine.framework`
  at runtime (`dlopen` + `NSClassFromString` + `objc_msgSend`; classes
  `_ANEInMemoryModelDescriptor`, `_ANEInMemoryModel`, `_ANERequest`,
  `_ANEIOSurfaceObject`). No CoreML model files, no MLX dependency in the
  core mechanism.
- Emits **MIL text inline** for a single linear-as-1x1-conv with a FIXED
  shape `tensor<fp16, [1, C_in, 1, seq_len]>`, builds an in-memory model,
  then `compileWithQoS:options:error:` / `loadWithQoS:` (compiles to HWX
  internally). The MIL carries hardcoded `buildInfo` coremlc version strings
  observed from a specific macOS release — an OS-update fragility point.
- Weights are **requantized to per-output-channel INT8** with fp16 scales
  (`constexpr_blockwise_shift_scale`), so the ANE path is approximate, not
  bit-exact (measured final-hidden cosine ≥ 0.999, top-1 unchanged).
- GPU↔ANE I/O is zero-copy: IOSurfaces wrapped as Metal buffers via
  `newBufferWithIOSurface:` (private MTLDevice selector), synchronized with
  `MTLSharedEvent` and per-layer fork/join on dispatch threads.
- Split strategy: disjoint output-channel slices of MLP gate/up (and GDN
  z+qkv) run on ANE, the remainder on GPU, merged with a fused SwiGLU
  primitive. Default request was 53% on M3 Ultra; multiple procedures are
  packed into per-ANE "banks" (one resident program per physical ANE
  instance, pinned via `kANEFAneInstanceHint`).
- Scheduler chunk size is aligned to the fixed ANE shape (2048 or 4096
  tokens); short chunks, tails, decode, and verification stay on GPU. No
  synthetic padding — Qwen's pad token mutates KV/recurrent state.

### oMLX measured results

| Machine | Model | Prompt | Prefill change |
|---|---|---:|---:|
| M3 Ultra (dual ANE) | Qwen3.8-27B-oQ4e | 4K | +3.4% |
| M3 Ultra (dual ANE) | Qwen3.8-27B-oQ4e | 16K | +17.8% |
| M3 Ultra (dual ANE) | Qwen3.8-27B-oQ4e | 32K | +18.9% |
| M3 Ultra (dual ANE) | Qwen3.8-27B AWQ 4.85bpw | 2K body | 1.356x |
| M5 Max (single ANE) | Qwen3.8-27B-oQ4 | — | +5% at 25% split |

Key M5-family finding: with NAX GPUs the optimal ANE fraction sits **well
below** the classic ~50% optimum; early field testing saw a prefill
*regression* when the GPU suffix competed with tensor-unit prefill (fixed
with dedicated NAX qmm kernels). Dominant ANE downtime was input readiness
(queued GPU work producing the next input), not submission overhead. Launch
strategies that deferred ANE submission (completion callbacks, async waits)
destroyed device overlap and lost to the intentionally blocking input-pack
wait.

Constraints observed: ~4 GiB weight window per physical ANE instance
(single-die chips host one bank; larger banks fail with 0x20004 and fall
back to split banks or per-layer programs); ~121 resident model handles
(procedure banks work around this); eager compile 15–40 s at startup;
+~4 GB peak memory; decode throughput unchanged.

## ds4 applicability

### Architecture fit (DeepSeek V4 Flash: 43 layers, n_embd 4096)

Offloadable (dense GEMMs, roughly 40–45% of per-layer GEMM MACs):

- q LoRA up (`q_b`: 1024 → 64×512 = 33.5M MAC/token) and o LoRA down
  (`o_a`: 32768 → 1024, same size) — the two largest dense projections;
- indexer projection (~33.5M), kv projections, q/o LoRA down/up small sides;
- shared expert (3 × 4096 × 2048 = 25.2M), leading dense layers.

NOT offloadable:

- routed MoE experts (256 experts, top-6 — the dominant FFN cost; dynamic
  routing defeats fixed ANE shapes);
- indexer top-k / sparse-attention gather, ratio-4 KV compressor (Sinkhorn
  HC), embeddings, logits.

Shape alignment is favorable: Flash prefill chunk is 4096 tokens
(`ds4.c`: "Long Flash prompts default to 4096-token chunks; PRO defaults
to 8192"), matching a supported ANE shape; every full chunk of a long
prefill would route through ANE (better amortization than oMLX's one-chunk
4K case). ds4 already ships Objective-C (`ds4_metal.m`), so the integration
pattern is native to the codebase.

### Blockers and costs

1. **ANE weight window:** INT8 copies of all dense projections ≈ 5.9 GB
   (≈137M params/layer × 43) exceeds the ~4 GiB per-instance window on
   single-die M5 Max; would require split banks / per-layer programs and
   trimming to ~2 large projections per layer (~3.5 GB INT8 ceiling).
2. **Overlap window:** ds4's per-layer prefill has more serializing
   non-GEMM work (MoE routing/permutation, indexer top-k, compressor) than
   Qwen's dense stack — the very condition that shrank M5 gains in oMLX.
3. **Precision regime:** the ANE path is approximate INT8; conflicts with
   the exact-MXFP4 campaign, greedy-identity tests, and golden vectors —
   would need a mode flag plus test carve-outs.
4. **Fragility:** private APIs + hardcoded MIL buildInfo versions can break
   on any macOS update.
5. **Startup/memory:** +15–40 s eager compile, +4–6 GB peak memory
   (competes with KV budget on 128 GB machines).
6. **Scope:** prefill/TTFT only — decode, DSpark, MTP untouched.

### Expected value

ds4 Flash on M5 Max already prefills at 557–790 t/s (README bench table).
The best available reference (dense 27B model, tuned split, same machine
family) is +5%; ds4's smaller overlap window argues for **2–4% realistic,
possibly negative without careful tuning**. On a worst-case 90k-token
miss-prefill (~3 min at current speeds) that is ~5–10 s saved. The KV-cache
v2 retention work (PLAN-KV-REWRITE.md, `.codebase-memory/adr.md`) attacks
the same user-visible pain far more effectively by avoiding the prefill.

## Verdict

Technically viable — the mechanism is standalone C/Objective-C-portable
(MIL text generation, IOSurface sharing, shared-event sync), independent of
MLX. Strategically marginal on M5 Max: high engineering cost and permanent
OS-update fragility against a low single-digit percent prefill gain.

### Revisit trigger / cheap pilot

If pursued later, do NOT integrate first. Build a standalone micro-bench:

1. Compile one `q_b`-shaped INT8 conv program (1024 → 32768, seq 4096)
   against the private runtime on the current macOS.
2. Measure raw ANE evaluation throughput and the GPU-overlap headroom with a
   concurrent MXFP4 MoE stream.
3. Kill the effort unless the overlapped composite clears ~1.10x on a
   synthetic 43-layer loop; otherwise the full integration cannot beat the
   oMLX M5 reference ratio.
