# UPSTREAM ISSUE DRAFT — Metal threadgroup-limit exposure (RISK-1 class)

Status: DRAFTED 2026-10-03, verified against upstream main `0aaea5a`
(all cites re-derived from upstream content via gh api; zero prior reports
of this class — closest #607, M1 Max field report, cross-ref as audience
evidence). **NOT POSTED — operator deprioritized (upstream responsiveness
low); performance work takes precedence.**

Title: Metal: split-K attention reduce dispatched at 1024 threads/threadgroup
with no per-pipeline limit check — silent wrong decode attention on older
Apple GPUs

Body:

## TL;DR

The decode-path split-K flash-attention reduce kernels are dispatched at
`32 * nwg` = 1024 threads per threadgroup, but no pipeline in `ds4_metal.m`
ever declares or queries `maxTotalThreadsPerThreadgroup` for them. On a GPU
whose per-pipeline limit is below 1024, the threadgroup is truncated to the
allowed size; the reduce loop strides by the *baked* `NWG` function constant,
so the untouched head_dim slices keep their previous contents — wrong
attention output on every decode token, with no error raised if the driver
doesn't validate.

## Mechanism

`kernel_flash_attn_ext_vec_reduce` (and the `_rope` sibling) split K across
`NWG` simdgroups and reduce with:

    for (short i = sgitg; i < DV4; i += NWG_) { ... }   // metal/flash_attn.metal:1433

`NWG` is function constant 501, so the stride is baked into the PSO, and the
dispatch width is `MTLSizeMake(32u * nwg, 1, 1)` with default `nwg = 32`
(1024 threads). If Metal caps this pipeline's `maxTotalThreadsPerThreadgroup`
below 1024, simdgroups `nsg >= limit/32` never run; their `DV4/NWG` slices of
the output row are never summed or written — the destination keeps stale data
from the previous step. Kernel stride and host width are two halves of one
contract nobody checks; clamping the width alone would also be wrong (it
contradicts the baked `NWG`), which is why this needs a guard, not a MIN().

A dispatch over the pipeline's own limit is either a validation error or a
silent truncation depending on driver/GPU generation. `ds4` checks the
command-buffer status (`ds4_gpu_wait_command_buffer`), so on validating
drivers the failure is loud; the hazard is the non-validating combination,
where the engine keeps serving confidently wrong tokens. Same bug class as
oMLX #4063 (silently wrong decode attention on M1 Max, not a crash).

## Sites (line numbers at upstream main 0aaea5a)

All PSOs are created via plain `newComputePipelineStateWithFunction:` —
`ds4_gpu_get_pipeline` (ds4_metal.m:2614) and the specialized getters
`ds4_gpu_get_flash_attn_reduce_pipeline` (:3829) / `..._reduce_rope_pipeline`
(:3782) — none declares a limit via pipeline descriptor, and none of the
dispatch sites queries the limit first.

| function | file:line | width | note |
|---|---|---|---|
| `kernel_flash_attn_ext_vec_reduce(_rope)` reduce dispatch | ds4_metal.m:27938, :28731, :29311, :29674 | `32*nwg` = 1024 | nwg default 32, env `DS4_METAL_FLASH_NWG` ≤32 (:29364-29383); runs every decode step |
| `kernel_qwen4_idx_select` / `_pre` / `_rows` | ds4_metal.m:49185, :49189, :49461 | const 1024 | qwen4 indexer top-k select; `qwen4_dispatch_resident` (:48352) passes the caller's `tg` straight to `dispatchThreadgroups:` (:48448) — the qwen4 path has **zero** limit queries anywhere |
| (structural) ~40 query sites exist only in the legacy dsv4 dense path (first: :4964) | — | — | the newer paths rely on nothing |

The qwen4 IDX_SELECT kernels are width-generic (`nth = ntg.x`, strided
passes; metal/qwen4.metal:1738), so a smaller limit truncates silently the
same way — stale `sel` block indices → wrong sparse-KV blocks, every decode
token.

## Minimal fix shape (fail-closed guard, ~15 lines)

1. In `ds4_gpu_get_flash_attn_reduce{,_rope}_pipeline`: after PSO creation,
   read `pipeline.maxTotalThreadsPerThreadgroup`; if `< 32*nwg`, print one
   stderr line naming the kernel and refuse the PSO (or re-create with a
   smaller nwg, falling back to the existing single-pass packed reduce,
   which IS guarded :29594).
2. In `qwen4_dispatch_resident`, before `dispatchThreadgroups:`: if
   `tg.width > pipeline.maxTotalThreadsPerThreadgroup`, one stderr line per
   kernel + return 0 (callers propagate; loud beats silently truncated).
   Clamping can come later per-kernel, with width-invariance proofs.

## Repro sketch

Needs an M1/M2-class GPU where these PSOs' limits land below 1024
(register-pressure dependent per pipeline). One-shot diagnostic that settles
it on any machine: log `maxTotalThreadsPerThreadgroup` / `threadExecutionWidth`
once per pipeline at creation behind `DS4_METAL_LOG_TG_LIMITS=1`, then dump
the table on M1 Max / M2 Max. `./ds4_test --metal-kernels` after adding the
guard should show it never fires on M3+, and a forced-low width should fail
loudly instead of returning numbers.

## Scope

Measured limits on M3/M4/M5-class GPUs generally sit at or above these
widths, so current-devices likely never fire — this is an older-silicon
correctness hazard (upstream M1 Max field reports exist, e.g. #607). The
structural half (no pipeline declares its limit, qwen4 path queries nothing)
is what makes it device-generation roulette rather than a solved invariant.

Note: our fork already ships the guard shape described above (commit
`555ee22`, with the DS4_METAL_LOG_TG_LIMITS diagnostic) — offered upstream as
a companion PR if the report lands.
