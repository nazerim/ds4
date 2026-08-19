# ANE Prefill Offload — Feasibility Investigation (ds4 server)

Status: **investigated, not adopted** (Aug 2026). No code changes.
This document is self-contained for handoff: an agent picking this up can
start at "Revisit trigger / cheap pilot — HANDOFF" below without reading
the oMLX tree first (references cite exact files/lines).

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

### Revisit trigger / cheap pilot — HANDOFF

Do NOT integrate into the engine first. Build a standalone micro-bench in
`speed-bench/` with three gates; stop at the first gate that fails its kill
criterion. Total cost to a go/no-go answer: **2–4 focused days** (Gates 1–2);
Gate 3 adds 2–4 days only if the gates pass.

#### Porting source (all in oMLX `custom_kernels/qwen35_prefill/csrc/qwen35_ane.mm`, 1965 lines)

Only these pieces are needed; the bank/dual-ANE/fp16/SwiGLU machinery is not:

| Component | oMLX reference | Notes |
|---|---|---|
| Framework load | `load_ane_framework()` :95 | `dlopen("/System/Library/PrivateFrameworks/AppleNeuralEngine.framework/...")` |
| MIL text gen | `int8_linear_mil()` :154 | 1x1 conv, fixed shape `[1, C_in, 1, seq]`; hardcoded `buildInfo` coremlc version strings |
| Weight blob helpers | `make_blob()` :114, `quantize_rows()` :330 | per-output-channel INT8 + fp16 scales |
| Model build/load | `AneLinearModel::Impl` ctor :426–557 | `_ANEInMemoryModelDescriptor modelWithMILText:weights:optionsPlist:` → `_ANEInMemoryModel inMemoryModelWithDescriptor:` → `compileWithQoS:options:error:` → `loadWithQoS:` (QoS arg 21) |
| I/O surfaces | `make_surface()` :354, ctor :521–545 | IOSurface → `_ANEIOSurfaceObject`, wrapped as Metal buffers via private `newBufferWithIOSurface:`, `newSharedEvent` |
| Dispatch | `_ANERequest requestWithInputs:...` :547, `evaluate_and_signal()` :798 | `evaluateWithQoS:options:request:error:` on a dispatch thread; keep the BLOCKING input-pack wait — async/completion-callback variants destroyed overlap (:257–263 of the doc) |

Estimated new code: ~400–500 lines ObjC. ds4 already builds ObjC
(`ds4_metal.m`) and standalone benches — copy the Makefile pattern at
`Makefile:120–126` (`metal-prefill-variant-bench` target: `.o` rule +
`$(CORE_OBJS)` link + convenience target).

#### Gate 1 — compile + raw throughput (1–2 days)

1. Port the pieces above into `speed-bench/ane_linear_bench.m` (Objective-C,
   plain `main`, no engine headers needed).
2. Shape: ds4 Flash `q_b` projection — input_dim 1024, output_dim 32768,
   seq 4096 (matches `--prefill-chunk` default for Flash). Random weights
   are fine: this measures throughput, not quality. Memory: INT8 weights
   ~34 MB; fp16 output surface 32768×4096×2 = 256 MB; input surface 8 MB.
3. Loop N evaluations, time with the shared-event signal path, report
   tokens/s equivalent (4096 tokens per evaluation) and ms/eval.
4. Compare against the GPU cost of the same GEMM in prefill (derivable from
   `ds4-bench` per-chunk prefill numbers: 790 t/s @2k on M5 Max ⇒ ~5.2 ms
   per 4096-token chunk total, all layers — so a single projection must be
   a small fraction of that to matter).
- **Kill criterion:** the private runtime rejects the MIL or no working
  `buildInfo` version string is found within ~1 day of probing → stop; the
  API surface is not available on this macOS. (oMLX pins coremlc `3505.4.1`/
  `3510.2.1` for single programs and `3520.4.1`/`3520.5.1` for banks.)
- **Also kill if:** single-program ANE eval is slower than the GPU MXFP4
  cost of the same projection — no overlap story can then beat GPU-only.

#### Gate 2 — overlap headroom (+1–2 days)

1. Add a concurrent GPU stream executing a representative MXFP4 MoE GEMM.
   Reuse the existing harness `tests/test_mxfp4_metal.c` (`make
   test-mxfp4-metal`, Makefile:137) or lift its kernel setup into the bench.
2. Run ANE evals and the GPU stream concurrently; measure the composite vs
   each alone. Sync strategy: blocking input-pack wait + shared-event
   signal only — do not experiment with completion-callback launching
   (oMLX measured it 5.6% SLOWER than GPU-only).
- **Kill criterion:** composite throughput gain < ~10% over GPU-alone at
  the best tested split. Below that, the full integration cannot beat the
  oMLX M5 reference (+5%) after ds4's smaller overlap window.

#### Gate 3 — synthetic 43-layer loop (+2–4 days, only if Gates 1–2 pass)

Simulate one prefill chunk: per layer, GPU attention/MoE stream with the
ANE projection forked/joined at the right point, including the serial
sections (indexer top-k, compressor) that bound overlap. Decision number:
composite ≥ ~1.10x vs GPU-only. Anything less cannot justify the engine
integration cost (weight-window banks, mode flag, test carve-outs,
OS-update maintenance).

#### Known gotchas carried from oMLX

- M5 Max is single-die: ONE physical ANE instance, ~4 GiB weight window —
  no dual-ANE striping (oMLX measured unpinned single calls 39.5% slower
  than dual-pinned, but dual does not exist here).
- `kANEFAneInstanceHint` pinning accepts instances 1–4; probe which value
  the M5 Max exposes (omlx: M3 Ultra = 1 and 2).
- Eager compile is 15–40 s in oMLX; fine for a bench, relevant to server
  startup if ever integrated.
- The path is approximate INT8 — never let the bench feed engine
  correctness tests; it is throughput-only.
