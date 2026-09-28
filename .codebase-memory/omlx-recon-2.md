# oMLX → ds4 Recon, second pass (read-only)

Status: tool budget (soft limit 5 calls) exhausted early. Only TASK 1d and parts of
1a/1c are verified; everything else is marked **unverified**. TASK 2 (Scratch prior
art) was **not started**. Do not treat unverified items as settled.

## Section A — ds4-side verification

### a. Is Qwen 3.8 Flash Next MoE in ds4? — LIKELY YES (partial evidence)
- `ds4.c:39663` (inside `ds4_context_memory_estimate_with_prefill_mode`, the
  `ds4_model_is_qwen4()` graph branch) sizes qwen4 scratch with
  `(DS4_N_EXPERT_USED + 1u) * (E + 2u * DS4_N_FF_EXP)` — a used-expert count and a
  per-expert FF expansion dim. That only makes sense for a routed MoE FFN.
- `metal/moe.metal` exists (`ls metal/`, 27 kernels total).
- Not verified: whether `DS4_N_EXPERT_USED` > 1 for the Flash Next config
  specifically, whether routing is top-k softmax + renorm, or whether the router is
  a separate dispatch chain. Candidate 3 is therefore **not dead**, but its hook
  point is unconfirmed.

### b. Chunked causal attention across draft rows in MTP verify? — UNVERIFIED
- No grep was run for the verify attention path. Relevant files to open first:
  `metal/qwen4.metal`, `metal/flash_attn.metal`, `ds4_metal.m`, `ds4_gpu.h`.
- Unknown whether ds4 loops per draft row or issues a block-causal call.

### c. GDN linear attention at verify widths — PARTIALLY VERIFIED (state exists; fusion unknown)
- GDN/linear-attention code is present in: `ds4.c` (32 case-insensitive `gdn`
  hits), `metal/qwen4.metal` (30 hits), `ds4_metal.m`, `ds4_gpu.h`,
  `tests/test_qwen4_kernels.c` (from `grep -rni "gdn|gated_delta|delta_net|linear_attn" -l`).
- Layer hybridity is real: `ds4_qwen4_layer_is_linear(il)` gates per-layer
  attention vs recurrent handling (`ds4.c:39659`, used to compute `n_attn`).
- Conv state exists and is MTP-snapshotted: `ds4.c:39668-39675` reserves
  `3 * (DS4_N_LAYER - n_attn) * (DS4_N_LIN_V_HEAD * DS4_N_LIN_HEAD_DIM^2 + (DS4_N_LIN_CONV - 1) * DS4_N_LIN_CONV_DIM)` floats, with the comment "Reserve both MTP
  snapshots as well as live state". So: recurrent state, depthwise conv state of
  width `DS4_N_LIN_CONV`, and 2 MTP snapshots per linear layer.
- **Unverified:** whether conv-state concat / conv1d / SiLU / qkv split / RMS norm /
  scaling are separate Metal dispatches per layer at S=3..9 (the exact thing oMLX's
  fused prework collapses), and whether a verify-width fast path exists at all.

### d. DS4_QWEN4_PREFILL_CHUNK — VERIFIED
- Read at `ds4.c:39636` inside `static uint32_t qwen4_prefill_chunk_tokens(uint32_t ctx)`:
  default **8192**; `0` or `>65536` fall back to 8192; result clamped to `ctx`
  (`ds4.c:39634-39640`). Also referenced from `ds4.c:39652` via the `prefill_chunk`
  argument of the memory estimator, and exercised in tests at
  `tests/ds4_test.c:370-371`, `:444`, `:479-480`, `:695` (set to 128).
- **No fixed-shape alignment** — the only adjustments are the range clamp and the
  `> ctx` clamp. So oMLX's "align prompt chunk to a fixed ANE shape" idea
  (`omlx/scheduler.py`) has no ds4 counterpart today.
- Note: `PLAN-PREFILL-M5.md` exists at repo root and was **not read**; it is the
  most likely place for existing M5 prefill-chunk reasoning.

### e. MTP draft/verify parameters (draft width, accept policy) — UNVERIFIED
- Only indirect evidence: the "both MTP snapshots" comment at `ds4.c:39668` implies
  a 2-deep MTP draft (snapshot count 3 = live + 2), which is consistent with small
  verify widths but does not establish a draft-window/sink equivalent to oMLX
  DFlash 2 (window 2048, sink 0).
- No env var or struct field for draft width / accept policy was located.

### f. Benchmark harness — PARTIALLY VERIFIED
- `ds4-bench` binary and `ds4_bench.c` exist at repo root; `speed-bench/` directory
  exists; `make` builds `ds4-bench` per `AGENTS.md`.
- **Unverified:** exact CLI flags, and whether prefill and decode tok/s can be
  measured separately. `make dspark-acceptance`, `make dspark-verify-depth`,
  `make mtp-verify-depth` targets exist and are the likeliest MTP-width harnesses.

## Section B — candidate verdicts

1. **Fused GDN prework kernel (verify widths S=3..9) — NEEDS-MEASUREMENT.**
   Hook point: `metal/qwen4.metal` (GDN kernels) driven from `ds4_metal.m`, with
   state/snapshot layout defined near `ds4.c:39668-39675`. Risk: ds4 may already
   fuse some of these steps, or may not even run a wide-verify path (if MTP depth is
   effectively 2, S may never reach 3..9 and the whole oMLX premise evaporates).
   Must confirm the actual verify width before porting.
2. **Verify SDPA split / chunked causal attention — NEEDS-MEASUREMENT.**
   Hook point: `metal/flash_attn.metal` + `metal/qwen4.metal`, dispatched from
   `ds4_metal.m`. Risk: ds4 may already do block-causal verify attention (nothing
   found either way); a redundant port buys nothing and can regress numerics.
3. **Fused MoE decode router top-k — NEEDS-MEASUREMENT (not dead).**
   Hook point: `metal/moe.metal` plus the qwen4 router dispatch in `ds4_metal.m`;
   expert-count constant `DS4_N_EXPERT_USED`. Risk: ds4's router may already be a
   single fused kernel (common in C engines), and tie-break semantics must match
   bit-for-bit or accepted-token behaviour changes.

No candidate is GO or NO-GO on the evidence collected. All three are blocked on the
same two cheap questions: (i) what verify width does ds4 actually run, (ii) how many
dispatches does one GDN layer / one router currently cost.

## Section C — prior art in /Users/naz/Projects/Scratch

**Not started.** No file in `omlx`, `omlx-ops`, `omlx-glimmer`, `dflash-mlx`,
`eagle`, `benchmark`, `ds4-kv-cache`, or `mistral4-large-context` was read. Zero
measured tok/s numbers were recovered. This section is a known gap, not a negative
result — a follow-up pass should target `*.md` / `*results*` / `*bench*` files only.

## Section D — highest-value, lowest-risk first change

**Do not port a kernel yet. First measure the dispatch count and verify width.**
Cheapest real win available from verified evidence is prefill-chunk tuning, because
`DS4_QWEN4_PREFILL_CHUNK` (`ds4.c:39636`) is an existing env knob with default 8192
and no shape alignment — it is free to A/B with zero code risk.

Procedure (read-only recon could not run any of this; commands are proposals):
1. Confirm the harness: read `ds4_bench.c` usage/help and `Makefile` targets
   `dspark-acceptance`, `mtp-verify-depth`; establish whether prefill and decode
   tok/s are reported separately. If not, that gap must be closed first.
2. Baseline prefill: run `ds4-bench` on a fixed prompt with
   `DS4_QWEN4_PREFILL_CHUNK` at 8192 (default), 3 times, record tok/s median.
3. Sweep 1024 / 2048 / 4096 / 16384 / 32768 identically; keep the prompt, context
   size, and backend fixed. Note the memory estimator coupling at `ds4.c:39652` —
   larger chunks raise `scratch_bytes`, so watch for admission/OOM differences.
4. Decode/MTP baseline: run the MTP verify-depth target and record decode tok/s plus
   the observed verify width S (this simultaneously answers Section A.e and gates
   candidates 1 and 2).
5. Only if S >= 3 and per-layer GDN dispatches are counted as separate (Instrument
   `ds4_metal.m` dispatch sites, or read `metal/qwen4.metal` kernel list), promote
   candidate 1 to GO and port `omlx/patches/qwen35_gdn_prework.py`.

Residual risks: three of six TASK 1 questions and all of TASK 2 are unanswered;
any plan built on this pass alone would be guessing about verify attention,
MTP parameters, benchmark invocation, and all prior art.
