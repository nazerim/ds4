# oMLX exact-Lightning-MTP (#4023/#4041/#4050 line) ↔ ds4 verify_rows_exact — transfer analysis

Lane 2 of `.codebase-memory/omlx-v070-final-perf.md` §4 action #2. Read-only
(2026-10-02, engine RUNNING — no builds/tests). Sources: omlx-glimmer commits
`d403e460`(#4023), `38267d24`(#4024), `fd620428`(#4038), `f5bf6f7b`(#4039),
`e15e5b53`(#4041), `3c5f1d41`, `26375259`(#4050), `65515c3c`(#4031),
`043f1d9d`(#3958), `e37e07f5`(#4122), `f9d6cc29`; ds4 `ds4.c`/`ds4_metal.m`/
`metal/qwen4.metal`, PLAN-DSPARK-PERF.md, AGENT.md.

## 1. The three rules oMLX converged on (how row-exactness survives fusion)

1. **Move intermediates, never re-associate.** Fused GDN verify step = the
   decode step "for t=0..S-1 in order… only where the intermediates live
   changes" (qwen35_gdn_prework.py:545-547). MoE window kernels run "the
   one-token source verbatim" per row; only grid.y→row mapping changes so
   adjacent rows re-read one weight block from cache
   (qwen35_moe_routed_decode.py:335-339). Row-batched qmv keeps each
   (row,col) accumulator in single-row K-order + same `simd_sum` finish
   (row_exact_qmv.py:12-16).
2. **Parallelize only row-independent work.** GDN prework (conv+SiLU, L2 q/k,
   g/beta) for all S steps runs across simdgroups pre-barrier; only the
   recurrence and per-step snapshot emission stay serial
   (qwen35_gdn_prework.py:536-539, :664-705). Attention: projections/RoPE/
   cache-append/block-pool batched; each row runs its own score+SDPA
   (language.py:1983-1986).
3. **Match the library's SHAPE RULE, not the shape.** Where kernel selection
   changes with rows (qmv→qmm, SDPA one-pass→two-pass, partition ladders),
   the fix mirrors the *predicate*: `_vector_plan` transcribes MLX's plan rule
   and shrinks chunks until per-row plan equality
   (qwen35_verify_sdpa_split.py:96-175); `qmv_fast_layout` = MLX's real
   alignment rule, 512 at 4/5-bit **256 at 6/8-bit** — the guessed rule was a
   silent, device-independent exactness bug latent until probed (#4050,
   moe_verify_gather.py:191-198).

Subtlest finds: indexer FP32 block-score *product* becomes a different-sums
GEMM at ≥2 rows → ~1-in-11,000 top-k selection flips (greedy-visible,
language.py:1936-2035); greedy verify pick must be argmax over the SAME
log-prob transform as the serial sampler — bf16 `logits − logsumexp` merges
1-ulp-apart logits and picks the other id (#4050,
batch_generator.py:1604-1613); #4122: fused SiLU pinned `precise::exp` but the
served MLX build's fp32 sigmoid differs between released/nightly — fix is a
STARTUP JIT probe over every fp16/bf16 encoding (65536 each), fuse only on a
match, else refuse (qwen35_gdn_verify_fused.py:266-317, :350). **Rule:
exactness is relative to the serving build's transcendental variants, not to
"the right formula".** Gates: single-stream only (batched "has no one-row
baseline", batch_generator.py:2121 + narrowing in `3c5f1d41`); rows ≤8-9 per
component; HC fused writes ≤16; M3 repeatedly "untested".
Late-join handoff (#4031): batch rows sit at drained chain frontiers → 1-forward
handoff on the same cache instead of re-prefilling ~111K history (2-min stall).

## 2. TRANSFER VERDICT per trick (ds4 grounding)

Already equal-or-stronger in ds4 (ds4 *is* the reference — no MLX to
transcribe): per-row attention splits recomputed per row from own key count
(`qwen4_attn_row_splits`, qwen4.metal:2208, cap 64); MoE combine order
row-count-independent by construction (ascending slots, one thread/dim,
`kernel_qwen4_moe_reduce`, qwen4.metal:3319-3358) and already folds the FFN-HC
write; sampler identity: verify accept uses the SAME `sample_argmax` on FP32
rows as serial greedy (ds4.c:74510-74514) and the GPU draft argmax mirrors
tie-lowest-id/NaN rules verbatim (qwen4.metal:4610-4655); few-row matvec keeps
row-invariant lane walk/shuffle tree, byte-identical at any rows-per-threadgroup
(ds4_metal.m:5602-5621, pinned by tests/test_qwen4_kernels.c).

Inapplicable (Python/batched-server artifacts): qmv_fast predicate (#2),
late-join handoff (#12), one-row-window special case is trivial here (see gap e).

**Genuine gaps** (the actionable list):
- (a) **HC `combine_norm` is T=1-only** — the kernel IS oMLX's #4024
  write-inside-norm (qwen4.metal:4659-4716) but host refuses `n_tokens != 1`
  (ds4_metal.m:48627) and call gate `T == 1u && !g->mtp_R` (ds4.c:58800-58813).
  Extension needs: per-row `old_inj` (buffer layout currently indexes without a
  token dim, qwen4.metal:4684) + generalized alternating-buffer swap. Cheap.
- (b) **HC two-launch decode** (norm folded into down/inject, rows 1..6; beyond
  7 their own data says unfused wins — e15e5b53). Perf, no exactness cost if
  slice-sum order preserved ("part0+part1+part2+part3 in the old order").
- (c) **MoE verify-window fusion** — 4-6 dispatches at verify size
  (ds4.c:58726-58742, :58656-58668); oMLX's motivation (MLX's 50 MB commit
  policy binding 1 GB weights per launch) is weaker in ds4 (direct model-buffer
  binding), so reprice before porting; the rows-share-block DRAM trick already
  exists in spirit as ds4's expert-major prefill ordering (qwen4.metal:3395-3400).
- (d) **Snapshot depth**: ds4 has 2 mid-walk snapshot points
  (`snap_tok`/`snap2_tok`, qwen4.metal:698-699, :4554-4555) + snap0, restored
  by POINTER SWAP (`qwen4_graph_state_swap2`, ds4.c:59021-59040). Wider T needs
  N points. Per-step state recording costs N×3.1 MB×45 linear ≈ 0.6-1.1 GB at
  N=8 (omlx hit this wall, qwen35_gdn_verify_fused.py:8-10). **omlx's answer
  (option b): record the block's cheap GDN INPUT ROWS; commit m accepts by a
  small in-register re-walk of rows 0..m-1 folded into the NEXT verify launch —
  zero extra steady-state memory.** ds4's rows2 kernels already lean this way
  ("snapshot can keep swapping places with its state", qwen4.metal:550-553).
- (e) R=1 verify should route to the T=1 fused decode step (ds4 already has
  it, incl. combine_norm) — small host-side change (e15e5b53 last ¶ analog).
- (f) Per-(template-instance) fail-closed kernel registry (f9d6cc29) — ds4's
  kill switches are coarse per-process envs (`DS4_QWEN4_NO_FUSE` etc.); gap
  small today, real for any future templated wide-T kernel.

GDN seriality settled: the delta-rule scan Sₜ₊₁=f(Sₜ,row) is **fundamentally
serial** (both engines walk t=0..T-1 in one threadgroup's registers; omlx caps
rows at 9 for exactly this). ds4's `gdn_front` 16-threadgroup cap is a
**structural** choice that's fine at T≤8 — keep it; prefill width stays on
halo/blocked+gdn_prep (qwen4.metal:608-643, :660+). Do NOT fuse front at
prefill width for exactness reasons; launch-count collapse (front+prep+scan+out
→ ~3) at verify width is a pure perf option.

## 3. PRICED SKETCH: lifting `verify_rows_exact` beyond T==3 (design only)

Target T∈4..8 (matches omlx ceilings; PLAN-DSPARK-PERF.md:347-356 shows
per-position acceptance collapse past ~4 on the DeepSeek drafter — Qwen4 nextn
economics unmeasured, open question #1).

Change sites: `qwen4_graph_fused` gate → `T <= verify_exact_max`
(env `DS4_QWEN4_VERIFY_ROWS_EXACT`, read PER CYCLE mirroring
`qwen4_mtp_draft_rows`, ds4.c:58292-58297; default 3 = today bit-for-bit);
HC-mix and attention-sub loops generalize 3→(2+1) to ⌈T/2⌉ pairs (already the
shape of ds4.c:58322-58349/:58479-58505 — keep per-sub-batch `nba_sub` block
universe + `tile_max` gating, the exactness contract at :58486-58488);
`qwen4_graph_mtp_steps` T-cap 3→8 (ds4.c:59053); verify loop accept-chain
generalizes the accept1/accept2 ladder (ds4.c:74466-74515); snapshots per §2d
(recommended: rows-recorded + deferred-commit replay); matvec geometry tables
get a byte-identity pin per new row count (ds4_metal.m:5613-5635 discipline);
`decode_r4`/`m5_single` gates take `T <= exact_max`; `mm_min=64` /
`shared_dense=8` must NEVER be crossed by verify rows — those are exactly the
row-count kernel-selection hazards this exercise exists to avoid.

Fail-closed convention: every dispatcher returns 0 on unsupported shape →
falls back to current pre-split T≥4 behavior (working, non-exact), never to
wrong-but-silent.

**Verification route (engine stopped — SCHEDULE, do not run):**
`make mtp-verify-depth` / `dspark-verify-depth` ARE the autoregressive-identity
contracts and must pass before merge; `--local-golden-vectors`; new geometry
first-run of `tests/test_qwen4_kernels`; `metal_prefill_variant_bench`-style
A/B. **Runnable now (coexist per AGENTS.md):** `--mtp-slice` extension pinning
row-formula invariants for T=4..8 geometries, `--server` floor, static review
of byte-identity pins. T=1-only gaps (a)/(e) need the same stop-list for their
depth tests.

## Open questions
1. Qwen4 nextn-head marginal accept at depth 3/4+ — no file measures it;
   gates the whole wide-T business case (DSpark was closed net-negative WITH
   replay costs; snapshot-swap verify is cheaper).
2. Option-(b) replay-launch cost (rows re-walked once, in registers) unpriced.
3. Device portability: `DS4_QWEN4_DECODE_FUSIONS` default is device-name
   matched ("M3 Ultra", ds4_metal.m:48606-48608); row-geometry defaults mix
   M3/M5 — wide-T table needs per-GPU measurement or an explicit
   verified-else-fallback ladder (omlx's pattern).
4. Exact-sampling rewind (`dspark_exact_sampling` snap0, ds4.c:74478-74484)
   assumes 2 snapshot points; deferred-commit changes what "state at block
   start" means — design work + engine-stopped tests.
5. Whether standalone GPU test binaries (test_qwen4_kernels) contend with the
   server's Metal process lock — undocumented; verify at the next scheduled
   stop window.
