# PLAN-INT8-MOE — INT8-activation MoE prefill for Qwen3.8 Flash Next

Branch: `int8-moe-prefill`. Status: **feasibility proven, design settled, Stage A
not yet implemented.** Nothing here is wired into the engine yet, so the default
path is untouched and no benchmark number in this file is a claim about shipped
behaviour.

## 1. Why this lever, with measurements

ds4's Qwen3.8 prefill was re-attributed on 2026-09-29 because the split in
`PLAN-PREFILL-M5.md` was measured on DeepSeek-V4 q2-q4, not on this model, and
that plan explicitly put "MoE gemm (roofline)" **out of scope** — which is exactly
where the headroom is. Measured with `DS4_QWEN4_TIMING=2`, ms per 8192-token chunk:

| pos | ple | hc_attn | gdn | attn | hc_ffn | moe | moe share |
|---|---|---|---|---|---|---|---|
| 0 | 29.4 | 316.0 | 1302.3 | 916.7 | 365.5 | 1872.1 | 39.0% |
| 65536 | 30.9 | 322.0 | 1472.1 | 1298.0 | 398.5 | 2041.5 | 36.7% |

MoE is the largest single bucket at every depth. ds4 runs that bucket on FP16 NAX
tiles (`kernel_qwen4_moe_mm_mid_nax_t`, `metal/qwen4.metal:3915`) at the measured
~13.5-14 TF/s plateau recorded in `DS4FORK.md:1268`, and ds4 has **no INT8 MMA
path anywhere**. oMLX PR #3548 measures **42 TOP/s INT8 against a 44.21 ceiling on
this same M5 Max** (Qwen3.8-27B pp4096: 391.8 → 518.5 tok/s).

Arithmetic on our own attribution: a 3x on a 37% bucket is worth up to ~22% of
total prefill; discounting for the Stage-A quantization pass and the unchanged
non-MoE GEMMs, **+10-20% prefill is the honest estimate**. `PLAN-PREFILL-M5.md`'s
"0-3% ceiling" does not cover this lever: it excluded MoE gemm and predates that
PR by ten days.

## 2. Feasibility — proven, cheaply

The gating question was whether this toolchain can express an integer cooperative
tensor matmul at all, since ds4's existing "NAX" kernels are its own hand-written
half/float tile path rather than MLX's `steel/gemm/nax.h`. Answer: yes.

`tests/mpp_tensor_int8_probe.m` compiles, through the runtime Metal compiler on
this machine (Apple M5 Max, macOS 26.6.2), ds4's **own** probe kernel shape —
`tensor<device T, dextents<int32_t,2>>` buffer arguments, slices,
`matmul2d<matmul2d_descriptor(16,16,dynamic_extent), execution_simdgroups<4>>`,
cooperative-tensor store — and then builds a real compute pipeline from it:

    half (control)         OK  (compile + entry + pipeline; threads 32)
    int8 x int8 -> i32     OK  (compile + entry + pipeline; threads 32)
    uint8 x uint8 -> i32   OK  (compile + entry + pipeline; threads 32)

Two details worth keeping, both learned from failed compiles:
- The allowed source types per the static_assert in
  `MPPTensorOpsMatMul2dImpl.h` are `unsigned char`, `signed char`,
  `metal::uint4b_format`, `metal::int4b_format`, `float`, `bfloat`, `half`.
  Plain `char` is NOT in that list and fails — Metal treats it as distinct from
  `signed char`, so the kernel must say `signed char`/`int8_t` explicitly.
- `get_destination_cooperative_tensor` takes the **cooperative tensor types**
  (`metal::remove_addrspace_t<decltype(ct_a)>`), not element types. Passing
  element types is a substitution failure that looks like "int8 unsupported".
- Native 4-bit operand types exist (`metal::uint4b_format`), so weights may not
  need widening to 8 bits at all. Constructing a `uint4b_format` value from an int
  literal is not a plain functional cast; that path is unexplored and is NOT
  needed for the design below.

Reproduce: see the build line in the probe's header comment. It needs no model and
no engine, so it runs in seconds.

## 3. The alignment that makes this tractable

`metal/qwen4.metal:3930` and `:4089` set `constexpr int NR0 = 64, NK = 32` for the
routed-expert mid and down tiles, so **one K step is 32 values**. A Q4_K
super-block is 256 values split into **8 sub-blocks of 32**, each with its own
6-bit scale and min (see `qwen4_dequant_raw16`, `metal/qwen4.metal:3566`, type 12:
`group = b % 8`, `ds = d * sN`, `dm = dmin * mn`).

Therefore **one K step == one Q4_K sub-block == one (scale, min) pair**. The affine
correction lands exactly on the existing loop boundary, with no re-tiling and no
partial-group bookkeeping. This is the same structural property oMLX relies on when
it notes that its four fragment K-steps covering one affine group are summed before
the correction lands — except ds4 gets it for free at its existing NK.

## 4. Math

Q4_K stores `w = ds * q - dm` with `q` an exact 4-bit integer in [0,15]. Quantize
activations per row and per 32-group: `x ≈ sa * qa`, `qa` int8.

    sum_i x_i w_i  =  sum_groups sa_g * sum_{i in g} qa_i * (ds_g * q_i - dm_g)
                   =  sum_groups sa_g * ( ds_g * MMA_g  -  dm_g * RowSum_g )

where `MMA_g = sum_{i in g} qa_i * q_i` is the int8×uint8→int32 tensor output for
that K step, and `RowSum_g = sum_{i in g} qa_i` is computed once per (row, group)
during quantization.

Two properties matter:
- **The weights are represented EXACTLY.** 4-bit integers widen to 8 bits without
  loss and the product `qa*q` is exact in int32. So the only error source is
  activation quantization — strictly better weight fidelity than an approach that
  requantizes weights to int8 with an affine approximation.
- Per-32-group activation scales align with the correction loop, so scale storage
  is `[T][K/32]` floats (8192 x 80 x 4 B = 2.6 MB at the widest prefill chunk) and
  the error does not accumulate across groups the way a single per-row scale would.

## 4b. Numerical viability — measured, and it changes the recommendation

Simulated on CPU (`/tmp/int8_viability.py`, `/tmp/int8_twoterm.py`; both reproduced
in the commit message) with T=256, K=2560, 32-value groups, Gaussian activations
and Q4_K-shaped weights (exact 4-bit codes with per-sub-block scale and min).
Relative error of the expert dot product against a float64 reference:

| path | mean | p95 | max | int MMAs per K step | modelled prefill |
|---|---|---|---|---|---|
| int8 single term | 1.421e-2 | 5.732e-2 | 3.679e-1 | 1 | +32.4% |
| **int8 two terms** | **7.636e-5** | **3.023e-4** | **2.888e-3** | **2** | **+13.9%** |
| int8 three terms | 2.299e-7 | 7.728e-7 | 5.522e-6 | 3 | +0.0% |
| fp16 (ds4 today) | 8.668e-4 | 3.524e-3 | 2.896e-2 | 1 | reference |
| fp16 + COMP (ds4 today) | 6.219e-4 | 1.981e-3 | 1.960e-2 | 2 | reference |

Three conclusions, in order of importance:
1. **Single-term int8 is not usable.** 1.4% mean relative error is 16.4x worse than
   the path ds4 runs today, and it is fundamental 8-bit noise amplified by
   dot-product cancellation rather than an outlier problem: shrinking the
   activation group to 16, or widening it to 128 or the whole row, moved the mean
   only between 1.1e-2 and 1.7e-2. No group-size tuning rescues it.
2. **Two terms recover far more than the fp16 path.** 7.64e-5 mean, which is 11.4x
   BETTER than plain fp16 and 8.1x better than fp16's COMP variant, because two
   int8 terms carry roughly 15 bits against fp16's 11. This is the same
   compensation trick ds4 already ships for its half path (`COMP` stages
   x = xh + xr to ~2^-22 relative), applied to integers.
3. Three terms would be essentially exact but cost 3 MMAs at 3x throughput, i.e.
   break-even. **Two terms is the sweet spot: better accuracy AND ~+14% prefill.**

The speed column applies the measured ratio of oMLX's 42 TOP/s int8 against
ds4's ~13.5-14 TF/s fp16 NAX plateau to the measured MoE bucket (2041.5 ms of
5563.0 ms at pos=65536): `new_total = total - moe + moe * mmas / 3`.

Honest limits on these numbers: activations are synthetic Gaussian and weights are
uniform-random Q4_K-shaped, so the absolute values will move on real tensors.
The RANKING is a precision-bits argument rather than a distributional one, so it
should hold; the 3x MMA ratio is oMLX's measured kernel, not a ds4 kernel, and a
first ds4 attempt will not hit 42 TOP/s, so treat +13.9% as an upper bound and
+8-12% as the realistic expectation. Stage A cost is not in the model; a
back-of-envelope puts it near 0.8% of a chunk (two passes per layer over
8192x2560, ~84 MB each, against a 2041 ms bucket), which must still be measured.

## 5. Design

Stage A — activation quantizer (new kernel), TWO terms.
Input: the `[T][in_dim]` activation tile already rounded to half by
`kernel_qwen4_rows_f32_to_f16`. Output: `int8 qa[T][in_dim]`,
`float sa[T][in_dim/32]`, `int32 rowsum_a[T][in_dim/32]`, and the same three for
the residual term b. One threadgroup per row; each simdgroup lane owns a whole
32-group, so a group's absmax and sum are thread-local and need no cross-lane
reduction at all. Symmetric quantization (`sa = absmax/127`, no zero point), then
`r = x - sa*qa` is quantized the same way into the b term. Grid
`(ceil(n_groups/32), T, 1)` with 32-thread groups keeps lanes on adjacent groups,
so the reads stay coalesced.

Stage B — weight operand. Keep the existing packed Q4_K read
(`qwen4_load_raw16`) but replace `qwen4_dequant_raw16`'s half output with a nibble
unpack to `unsigned char` in [0,15] staged into threadgroup memory, and carry the
per-group `(ds, dm)` into the epilogue instead of folding them into the operand.
No load-time repack and no extra resident memory: the checkpoint's own packed
weight stream is read unmodified.

Stage C — the GEMM. Same tiling as `kernel_qwen4_moe_mm_mid_nax_t` (64 expert rows
x NR1 tokens, K in 32-wide steps) with `matmul2d` instantiated over
`signed char`/`unsigned char` and an `int` destination. Per K step, run the tensor
matmul twice (term a and term b) into int32 cooperative tensors, then accumulate

    acc += sa_g * (ds_g * mma_a - dm_g * rowsum_a_g)
         + sb_g * (ds_g * mma_b - dm_g * rowsum_b_g)

into a float register tile. The SiLU(gate)*up epilogue and the half copy of `mid`
that the down tiles read stay exactly as they are, so the down-projection kernel
needs the same treatment independently and can land later.

Host wiring. A new env gate `DS4_QWEN4_MOE_INT8=1`, default OFF, checked where
`g_metal4_tensor_api_enabled` is consulted, plus a startup capability probe for the
integer form mirroring the existing half probe so the flag cannot be set on
hardware that lacks it. Buffers for `qa`/`sa`/`rowsum` join the existing qwen4
arena; note the memory estimator coupling at `ds4.c:39652`.

## 6. Validation plan (all of it, before any promotion)

1. Numerical: extend the existing `test_moe_mm_tiles_exact` family, which already
   bounds tensor-path versus simdgroup-path drift for the half kernels. Add an int8
   case asserting the relative error against the FP16 reference stays inside a
   documented bound, measured rather than assumed.
2. Kernel-level: `--metal-kernels` and `--metal-tensor-equivalence` groups.
3. End-to-end quality: `--quality` must keep the reference path available, per the
   doctrine in `PLAN-PREFILL-M5.md:29-35` — accumulation order alone changes
   129278 of 129280 logits, so exactness is not on offer here and the flag must
   stay opt-in until quality is demonstrated.
4. Speed: `ds4-bench --prompt-file <fixed> --ctx-max 65536 --gen-tokens 0 --csv`
   with the flag off and on, three runs each, median, same thermal posture. Note
   that decode measurements on this box drift with run position by more than the
   effect being measured, so prefill A/B must be counterbalanced (run the second
   configuration in reverse order) — measured 2026-09-29: sequential prefill fell
   1409 → 1257 t/s across five runs with no config change.
5. Model behaviour: MTP acceptance rate and a real prose sample, because a
   quantized FFN can move acceptance before it moves any aggregate speed number.

## 7. Risks

- Cannot be bit-exact, so it stays opt-in and the FP16 path remains the reference.
  Note however that section 4b measures the two-term int8 path as MORE accurate
  than both fp16 variants ds4 ships today, so "not bit-exact" here does not mean
  "less accurate" - the bar for promotion is a quality and acceptance-rate study,
  not an error bound.
- `metal::uint4b_format` as a direct operand is unproven here; the design above
  widens nibbles to `unsigned char`, which IS proven, so the 4-bit path is an
  optional later refinement rather than a dependency.
- Stage A costs bandwidth: it reads the half activations and writes int8 plus two
  side arrays. At 8192 x 2560 that is ~42 MB read and ~24 MB written per call,
  against a bucket currently costing 1872-2041 ms per chunk — so the overhead is
  noise IF the MMA gain is real, but it must be measured, not assumed.
- The down projection (`kernel_qwen4_moe_mm_down_nax_t`) reads the half `mid` the
  mid epilogue writes; converting only the mid kernel leaves the down kernel on
  FP16, so the win is roughly half of the MoE bucket until both land.
- Grouped-expert dispatch has its own gating (`DS4_QWEN4_MOE_NO_GROUP`, and the
  `mm_min = 64` rows threshold at `ds4.c:58569` whose comment records that 16 rows
  spread 10 choices over 512 experts and leave tiles nearly empty). An int8 variant
  must respect the same row-count economics or it will lose at small batch.

## 8. Increments, each separately committable

1. **[done]** Feasibility probe + this document. Proves the integer cooperative
   tensor path compiles and pipelines on this machine in ds4's own kernel shape.
2. Stage A quantizer kernel emitting BOTH terms + a unit test bounding its
   round-trip error against a CPU reference (the reference is the simulation in
   section 4b, so the test asserts the kernel matches the model that was validated).
   Self-contained: nothing calls it yet, so the default path cannot regress.
3. int8 mid GEMM behind `DS4_QWEN4_MOE_INT8=1` + the numerical test against the
   FP16 reference + the capability probe and startup log.
4. Benchmark, counterbalanced; record the numbers in this file and in DS4FORK.md.
   Promote to default only with `--quality` evidence and an acceptance-rate check.
5. Same treatment for the down projection, if 3-4 pay off.

If work stops partway, stop at an increment boundary: 1 and 2 are inert, 3 is
gated off by default, and nothing before increment 4 changes any shipped number.
