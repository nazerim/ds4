# RISK-1 — Final upstream issue draft (verified against antirez/ds4 @ 0aaea5a)

Status: FINAL DRAFT 2026-10-03. Every cite re-derived from the upstream tree at
`0aaea5a` (verified via local `origin/main` refs; origin = github.com/antirez/ds4).
Supersedes the stashed draft in `bb12114` (`.codebase-memory/UPSTREAM-ISSUE-RISK1-DRAFT.md`),
which had three cite errors fixed here: reduce pipeline getters are at **:3783/:3831**
(not :3782/:3829), the packed-reduce limit guard is at **:29467** (not :29594), and the
"no limit query anywhere" framing corrected to "none on the two affected paths"
(upstream has ~60 queries in the older dsv4 dense code, first :4964, last :45586).
**POSTED 2026-10-04 00:4x as antirez/ds4 issue #1176**
(https://github.com/antirez/ds4/issues/1176); posted title: "Metal: split-K
attention reduce and qwen4 indexer select dispatched at 1024 threads/
threadgroup with no per-pipeline limit check" (body above, plus a note that
the fork ships the guard shape).

---

## Title

Metal: split-K attention reduce and qwen4 indexer select dispatched at 1024
threads/threadgroup with no per-pipeline limit check — silently wrong decode
attention on Apple GPUs whose pipeline limit is below 1024

## Body

### TL;DR

Two decode-hot dispatch families in `ds4_metal.m` request 1024 threads per
threadgroup without ever consulting
`pipeline.maxTotalThreadsPerThreadgroup`:

1. the dsv4 split-K flash-attention reduce
   (`kernel_flash_attn_ext_vec_reduce` / `..._rope`), dispatched at
   `32 * nwg` = 1024 with `nwg` baked into the pipeline as function
   constant 501, and
2. the qwen4 indexer top-k select kernels, dispatched at a literal
   `MTLSizeMake(1024, 1, 1)`.

`maxTotalThreadsPerThreadgroup` is **per pipeline**, not per device — it
shrinks with kernel register pressure. On a GPU where these pipelines' limit
lands below 1024, the threadgroup is truncated to the allowed size. Neither
kernel's work accounting adapts:

- **Reduce kernels:** the split-K reduction strides by the *baked* `NWG`
  constant (`for (short i = sgitg; i < DV4; i += NWG_)`,
  `metal/flash_attn.metal:1433`). simdgroups beyond the cap never run, so
  their `DV4/NWG` slices of every output row are never summed or written —
  the destination keeps stale data from the previous step. Wrong attention
  output on **every decode token**.
- **qwen4 IDX_SELECT kernels** are width-generic (`const uint nth = ntg.x`,
  `metal/qwen4.metal:1738`, strided histogram passes), so a smaller
  threadgroup leaves parts of the selection passes unscheduled — stale
  `sel` block indices → wrong sparse-KV blocks, again every decode token.

The host width and the kernel stride are two halves of one contract nothing
checks. A dispatch over the pipeline's own limit surfaces either as a
validation error or as a silent truncation depending on driver/GPU
generation; where it is silent, the engine keeps serving confidently wrong
tokens. This is the same bug class as oMLX #4063 (silently wrong decode
attention on M1 Max).

Note this is **not** a "query the limit everywhere" gap: the older dsv4 dense
paths do query the limit (~60 sites in `ds4_metal.m`, first at :4964, last at
:45586). The hazard is that the two newest decode-hot families query nothing.

### Sites (line numbers at upstream main `0aaea5a`)

All pipelines below are created with plain `newComputePipelineStateWithFunction:`
— no pipeline-descriptor limit declaration, and no runtime query at or near
the dispatch:

| family | file:line | width | notes |
|---|---|---|---|
| flash-attn reduce dispatch | `ds4_metal.m:27938`, `:28731`, `:29311`, `:29674` | `MTLSizeMake(32u * nwg, 1, 1)` | `nwg` defaults to 32 → 1024; `DS4_METAL_FLASH_NWG` clamped to [1,32] at `:29364`–`:29383`; runs every decode step |
| reduce pipeline getters | `ds4_gpu_get_flash_attn_reduce_rope_pipeline` `ds4_metal.m:3783`, `ds4_gpu_get_flash_attn_reduce_pipeline` `:3831` | — | bake `nwg` as function constant 501 (`:3800`, `:3852`); neither checks the created pipeline's limit |
| qwen4 IDX_SELECT dispatches | `ds4_metal.m:49185`, `:49189`, `:49461` | `MTLSizeMake(1024, 1, 1)` | top-k select (`_pre`, plain, `_rows`) |
| qwen4 dispatch chokepoint | `qwen4_dispatch_resident` `ds4_metal.m:48352` | — | passes the caller's `tg` straight to `[enc dispatchThreadgroups:grid threadsPerThreadgroup:tg]` at `:48448`; the entire qwen4 path contains **zero** `maxTotalThreadsPerThreadgroup` queries |

Contrast: the single-pass packed reduce alternative already guards itself —
`packed_pipeline.maxTotalThreadsPerThreadgroup >= packed_threads` at
`ds4_metal.m:29467`. The split-K path is the one left unchecked.

Clamping the dispatch width alone is *not* a valid fix for the reduce
kernels: it would contradict the baked `NWG` stride and change the
reduction, so the contract needs a guard (or an `nwg` re-creation at a
width the pipeline can actually run).

### Minimal fix shape (fail-closed guard, ~15 lines)

1. In `ds4_gpu_get_flash_attn_reduce{,_rope}_pipeline`: after PSO creation,
   compare `pipeline.maxTotalThreadsPerThreadgroup` against `32 * nwg`; if
   below, print one stderr line naming the kernel/limit and return `nil`
   (callers already handle a nil reduce pipeline), or re-create with a
   smaller `nwg` / fall back to the guarded packed path (`:29467`).
2. In `qwen4_dispatch_resident`, before `dispatchThreadgroups:`: if
   `tg.width > pipeline.maxTotalThreadsPerThreadgroup`, one stderr line per
   kernel and `return 0` (callers propagate; loud beats silently truncated).
   Width-adaptive clamping can come later per kernel, with the
   width-invariance proofs each kernel needs.

Sketch (shape only):

```c
/* ds4_gpu_get_flash_attn_reduce_pipeline(): after newComputePipelineStateWithFunction: */
if (pipeline.maxTotalThreadsPerThreadgroup < (NSUInteger)(32u * nwg)) {
    fprintf(stderr, "ds4: kernel_flash_attn_ext_vec_reduce needs %u threads/threadgroup "
            "but pipeline limit is %lu — refusing (set DS4_METAL_FLASH_NWG<=N)\n",
            32u * (uint32_t)nwg, (unsigned long)pipeline.maxTotalThreadsPerThreadgroup);
    return nil;   /* caller's existing nil-handling takes over */
}

/* qwen4_dispatch_resident(): before [enc dispatchThreadgroups:...] */
if (tg.width > pipeline.maxTotalThreadsPerThreadgroup) {
    fprintf(stderr, "ds4: %s dispatch width %lu exceeds pipeline limit %lu\n",
            qwen4_kernel_names[kernel], (unsigned long)tg.width,
            (unsigned long)pipeline.maxTotalThreadsPerThreadgroup);
    return 0;
}
```

Plus, to make device-generation questions answerable in one run: log
`maxTotalThreadsPerThreadgroup` / `threadExecutionWidth` once per pipeline at
creation behind `DS4_METAL_LOG_TG_LIMITS=1`.

### Repro sketch

Needs an M1/M2-class Apple GPU where these PSOs' per-pipeline limits land
below 1024 (register-pressure dependent per pipeline; not guaranteed even
there). One-shot diagnostic that settles it on any machine: add the
`DS4_METAL_LOG_TG_LIMITS=1` dump, run a short decode, and inspect the limits
reported for `kernel_flash_attn_ext_vec_reduce*` and
`kernel_qwen4_idx_select*`. On machines where a limit < 1024 appears, the
guards above should fire loudly; without them, greedy decode output diverges
from the same model run with `DS4_METAL_FLASH_NWG=1` (single-warp reduce,
which stays under any plausible limit) — a cheap bit-exactness A/B oracle.

### Scope / audience

Measured limits on M3/M4/M5-class GPUs sit at or above these widths, so this
likely never fires on current hardware — it is an older-silicon correctness
hazard. Upstream M1 Max field reports exist (e.g. #607), so the audience is
real. The structural half — two decode-hot families with zero limit queries
while the older paths query diligently — is what makes correctness
device-generation roulette rather than an invariant.

---

## Verification record (this draft)

- (a) PASS — 1024-thread reduce dispatch confirmed at `ds4_metal.m:27938,
  :28731, :29311, :29674` in the `0aaea5a` tree; default `nwg = 32` with
  env clamp at `:29364`–`:29383`; stride loop at
  `metal/flash_attn.metal:1433`; function constant 501 set at `:3800`/`:3852`.
- (b) PASS (as corrected) — getters `:3783`/`:3831` and the qwen4 chokepoint
  `:48352`→`:48448` contain no limit query; no query exists anywhere after
  `ds4_metal.m:45586` in the tree. Literal "no queries anywhere upstream"
  is FALSE — ~60 queries exist in the legacy dsv4 dense paths (first
  `:4964`); the draft was reworded accordingly.
- (c) PASS — antirez/ds4 #607 open, "Field report: DeepSeek V4 Flash q2-q4
  on two M1 Max 64 GB (distributed)" (2026-07-26). It does not report this
  bug; appropriate as cited — M1 Max audience evidence only.
- (d) PASS — no duplicate: all ~250 open issues scanned; title/body search
  for "threadgroup" hits only #768 (perf-campaign question), for
  "maxTotalThreadsPerThreadgroup" hits 0. Nearest cousins #975 (CUDA
  512-thread top-k) and #1128 (Metal shape rejects) are different issues.
- Fork guard commit `555ee22` verified present (fail-closed qwen4 guard +
  rms-norm twin swap + DS4_METAL_LOG_TG_LIMITS diagnostic).
