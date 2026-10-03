# Qwen4 verify-vs-serial identity violation — found 2026-10-03 (pre-existing)

> **STATUS 22:5x 2026-10-03 (operator-approved flip): DS4_QWEN4_VERIFY_
> SINGLE_TREE + MOE_GROUP_EXACT are the PRODUCTION DEFAULT (via
> ds4-server.sh QWEN_EXACT_VERIFY=1; battery 58.4s batched production /
> 62.4s single-session, 10/10 byte-identical spec==serial==golden in-tree;
> golden re-captured under ST at tests/test-vectors/qwen38-flashnext/
> local-golden.vec, pair-vs-standalone unit test skips honestly under the
> flag). v2 (VERIFY_PER_ROW) remains the legacy-stream oracle: it reproduces
> the historical plain-mv tree exactly for reconstructing past sessions
> (differs from ST world on exactly one known near-tie: prompt 7 @char 1555).
> QWEN_EXACT_VERIFY=0 restores the drifting dispatch for perf baselines.
> Full suite green BOTH ways: 26 OK default, 26 OK ST-pinned (2 skips).
> UPDATE 00:1x 2026-10-04: the exact umbrella also exports DS4_QWEN4_VERIFY_
> HC_PAIR=1 — ROOT-CAUSE-2(a)'s pair mixer returns as pair_rowe (weight
> reuse with the single-row chain spelled per row, bit-identical; battery
> 60.0s single-session / 57.3-57.7s batched, streams UNMOVED, goldens pass).
>
> --- Original evening status (kept as history): BOTH root causes closed. ROOT-CAUSE-1 (matvec
> mv_ext) fixed by the per-row intercepts; ROOT-CAUSE-2 (T=2 PAIR gate/mix
> kernel + chunk-level attention selection universe) localized with
> `--qwen4-rowcount-ab` and fixed under the SAME env
> `DS4_QWEN4_VERIFY_PER_ROW=1` (v2): 3-grid identity probes flips=0,
> rowcount-ab bit-identical (maxabs 0.0), server battery 10/10 byte-identical
> to serial. Cost: per-row v2 ~= serial wall (see ROOT-CAUSE-2 CLOSED).
> The original "leading hypotheses" text is kept as history; hypothesis 1
> (selection universe) + the split-window generalization of hypothesis 2
> were both right in substance; pos%4=3 was a near-tie coincidence, not the
> selection fingerprint.

Discovered while B3-tuning the depth policy (the lockout fix makes depth
transitions frequent, which amplified a latent gap into visible battery
divergence). Headline, fully deterministic (2 fresh processes per config,
run1==run2 inside every config):

| config (all single-session, temp 0, 10-prompt battery, 400 tok each) | vs serial |
|---|---|
| production default (no env; MTP on, old policy: deep ~0.5%) | **diverges: prompt 6 @char 938, prompt 7 @1379** |
| DS4_QWEN4_MTP_DEPTH=2 (forced shallow) | same two flips |
| DS4_QWEN4_MTP_DEPTH=3 (forced deep, split path) | prompt 7 @1520 (differs from serial AND from forced-2) |
| exact-grouped + depth-unlock (406/2000 deep) | 3@884, 6@938, 7@1251 |
| serial (DS4_MTP_SPEC_DISABLE=1) | reference (wall 70.3 s vs 55-57 spec — 22% slower, the speedup spec buys) |

Contract broken: `verify_rows_exact`'s comment ("the 3-row speculative
verify so every dispatch keeps the exact T <= 2 kernel paths") assumes
T<=2 IS the exact reference; these results say even T=2 verify != T=1
serial at some positions. The fork has never had a qwen verify-identity
test: `--mtp-verify-depth` is DSpark-sidecar-only (asserts
`mtp_draft_tokens > 2`), `--local-golden-vectors` was captured WITH spec
on (encodes whatever spec produced). This is the gap.

## Evidence files (tests/spec_economics/results/)
- `20261003_serial_1/2.json` — serial reference, stable
- `20261003_default_1/2.json` — production default, stable, same 2 flips
- `20261003_forced2.json`, `20261003_forced3_split.json`, `20261003_p3_unlock_engaged.json`

## Leading hypotheses (to arbitrate with the identity probe)
1. **Sparse selection universe**: battery positions > sparse_pos(=(k_blocks+1)*4-1 ≈ 35)
   make EVERY verify row a SELECT row. Dense vs sparse is per-sub-batch; the
   sub-batch universe `nba_sub` gives row 0 more candidate blocks than its
   own serial pass whenever the chunk closes a block ((pos+2)%4==0 vs
   (pos+1)%4==0). If `kernel_qwen4_idx_score`'s per-row `visible` mask is
   not applied identically for the *pool* vs *select* stages (e.g. the
   final incomplete block tail is keyed at chunk granularity), row 0's
   top-k can differ from serial's. FIRST-FLIP positions should cluster at
   (pos+2)%4==0 — the probe must print pos % 4 of the flip.
2. `qwen4_attn_split_keys` redistribution at verify sizes — ruled out for
   battery (positions < 8192 => n_splits=1), but true at long agent
   contexts (128k sessions exist in production!): row 0 of a T=2 chunk gets
   `keys_per_split` from ceil((pos+2)/ceil((pos+2)/sk)) vs serial's own —
   different FP windows at ANY pos ≥ 8192. This ALSO matters for production
   long-context even if hypothesis 1 explains the battery.
3. Draft-regen chain steps polluting predictor cache rows read before
   rewrite (the code comments argue they're always rewritten first).

## ROOT CAUSE FOUND (16:00-17:40, day shift) + fix implemented

**The small-batch matvec kernels are not row-count invariant.** Every dense
projection + the logits head at verify chunk widths T=2/3 dispatches through
the `mul_mv_ext_*` batched kernels whose lane->K split (`nxpsg`, pair
grouping) differs from the plain T=1 matvec kernel's reduction tree ->
**~82% of output elements differ by 1 ULP between a T=2 chunk dispatch and
T=1** (measured across F32/F16/Q8, out-dims 640/2560; logits head is Q8_0,
out 248320). Rare near-tie argmaxes then flip => speculative stream drifts
from serial (battery prompts 6@938, 7@1379 stable, and prompt 3@884 in other
depth sequences). The `verify_rows_exact` machinery only protected
attention (2/1 sub-batches) and HC gate/mix (2-row pair kernel + 1-row) -
the matvec stage was never covered, and no test compared row 0 across T.

### Fix (implemented, env-gated): DS4_QWEN4_VERIFY_PER_ROW=1
`ds4_gpu_verify_per_row()` + per-row intercepts in `ds4_metal.m` hosts
matmul_f32_tensor / matmul_q8_0_tensor_impl / matmul_f16_tensor_impl: 2..8
row chunks (in_dim%128==0) are dispatched as T independent T=1 view
dispatches - every verify row then runs the exact serial tree.
Cost = re-read weights per row (logits Q8 head 1.3 GiB x extra rows/step).

### Evidence with the fix on
- NEW engine-level regression `./ds4_test --qwen4-verify-identity`
  (spec cycle vs serial replay of the same stream; DS4_TEST_VERIFY_PROMPT_FILE,
  DS4_TEST_VERIFY_THINK knobs; prompts now banked under
  tests/spec_economics/prompts/): **flips=0, worst_gap=0.0000** on prompts
  03/06/07, think-none AND think-high, including deep cycles (max_chunk=3).
  (18:30 correction: this grid was lucky - the probe now sweeps
  adaptive/depth2/depth3 and prompt-03 forced grids flip deterministically;
  see ROOT-CAUSE-2.)
- Server battery (QWEN_BATCH_SESSION=0, temp 0): serial 70.3-73.8 s |
  drift-default 55.4 s | per-row-correct 65.4 s. Per-row spec is still
  ~12% faster than serial and now provably identical to it at session level.
- Kernel-level rowcount sweep was built (then removed: its own harness hit an
  arena/dangling-scope crash; findings above preserved in this doc; the
  engine probe is the durable regression).

### RESIDUAL: BISECTED 17:2x-18:2x (window 3) - it was never a server bug:
ROOT-CAUSE-2 = the fused multi-row forward itself, plus a session-reuse
policy twist. Both proven with `tests/spec_economics/order_probe.py`
(arbitrary prompt sequences vs the live server) + the upgraded
`--qwen4-verify-identity` (now sweeps adaptive/depth2/depth3 grids and
prints the flip's full-precision logit pair):

1. **Server prompt-3@884 = slot-reuse depth-grid, not DSML/rollback.**
   Solo prompt-3 on a fresh engine: per-row spec output is BYTE-IDENTICAL to
   serial (1565 chars, same sha). The battery (prompt-3 = 4th request)
   diverges from serial @884; re-running the SAME prompt twice on a clean
   engine flips on request #2 exactly (and #3==#2). Cycle traces explain it:
   req#1 (cold session counters) engaged deep(T=3) at pos 158/161/163;
   req#2 (warm `qwen4_depth_window/streak/engaged` — session-scoped and
   NOT reset across server slot reuse) ran zero deep cycles; committed
   streams agree through pos 292, then cycle 293 row-0 (T=2 in BOTH runs)
   commits 364 vs 1331. The adaptive-policy counters are per-session and
   the server reuses sessions across requests (rewind/replay) -> the depth
   grid is request-history dependent. (Follow-up hygiene question, NOT yet
   acted: reset the depth counters at request boundary for determinism.)
2. **ROOT-CAUSE-2: per-row matvec is NOT sufficient - the fused T>=2 trunk
   forward drifts elsewhere.** Probe prompt-03 per-row: adaptive flips=0,
   but **forced depth2 AND forced depth3 both flip at abs 251 (%4=3, gap
   4.58e-05: committed 799 vs replay argmax 9167 - a near-tie)** with
   IDENTICAL logits -> the flip is deterministic and independent of the
   2/1-row sub-batch split (T=3 reproduces the T=2 numbers exactly).
   Adaptive "flips=0" is grid luck (which near-ties land as verify rows),
   NOT proof of identity - the day-shift flips=0 claim must be read that
   way. Knob matrix: robust to DS4_METAL_MV_EXT_NSG, DS4_QWEN4_GDN_NSG,
   NO_GDN_R4, NO_Q4K_MID, MOE_GROUP_EXACT, SHARED_DENSE_MIN=1,
   METAL_DISABLE_{HC,KV,QKV_NORM}_FUSION, QWEN4_NO_MTP_BATCH, and
   DISABLE_HC_FUSION is inert here (decode fusions default ON only on M3
   Ultra - `ds4_gpu_qwen4_decode_fusions_enabled`, ds4_metal.m:48711).
   DS4_QWEN4_NO_FUSE=1 gives flips=0 but is VACUOUS (max_chunk=1: every
   cycle degrades to 1-row eval, speculation dead, wall == serial).
   => the residual ~1-ULP coupling lives in the multi-row dispatches of
   qwen4_graph_forward_tokens that are NOT mv_ext matmul: attention rows
   kernel, GDN scan, PLE conv/gate, HC combine/pair-mix, MoE slot-stage,
   rope/scores — needs the kernel-level rowcount sweep rebuilt (the
   day-shift harness died on the arena crash; start per-stage A/B against
   the serial tree at T=2 with the real verify buffers).
3. **Cross-config corollary (benign):** per-row forced-3 vs per-row adaptive
   differ at p6@938/p7@1379 = legitimate policy trajectories (both serial-
   consistent streams, different tokens committed); NOT identity violations.
   Per-row battery == serial on 8/10 prompts (0,1,2,4,5,6,8,9); 3 and 7 flip.

### Default posture UPDATED (18:30): per-row stays OFF.
It removes the matvec drift family (p6@938 class fixed) at +18-30% battery
wall, but does not achieve identity (ROOT-CAUSE-2). Flipping the default now
buys most of the cost without the guarantee; the fix target is now the
non-matvec row-count coupling (queue #1 below), then GROUP_EXACT/fused-window
work re-validated against the 3-grid probe, THEN the default decision
(golden re-capture `--local-golden-capture` only after identity truly holds).

### Default posture (17:40)
Env OFF (default binary == drift-status-quo, golden vectors unchanged) while
the residual is scoped. Recommended after residual lands: flip per-row ON
by default + re-capture goldens (they encode the drifting stream). Plain
T=1 decode unaffected either way.

### ROOT-CAUSE-2 CLOSED 19:1x (window 3 continued): localized + fixed (env-gated v2)
New engine harness `./ds4_test --qwen4-rowcount-ab` (DS4_SERVER_TEST-compiled
per-stage row-0 hashing inside `qwen4_graph_forward_tokens`: R/blk/mixed/xn/lo
at 4 stage points per trunk layer + final mixer + logits row 0 = 20/layer;
fused T=2 forward vs sequential T=1 forwards from identical synced prefixes;
test-only build, production binaries unaffected):

- Localization (prompt 03, per-row v1, forced depth-2): first divergence
  layer 0 slot 2 = post-hc_attn_mix `mixed` - xn/lo hashes IDENTICAL, so the
  T=2 **PAIR gate/mix kernel != two T=1 dispatches** (the "exact T<=2 kernel
  paths" assumption was never bit-true against T=1). With
  `DS4_QWEN4_NO_HC_PAIR=1`: layer 0-2 (GDN) clean, first diff moves to the
  FIRST FULL-ATTENTION layer (interval-4 il=3) at the attention `blk` = the
  chunk-level `n_blocks_after` selection universe / dense-split: a row whose
  own serial step has not yet closed a 4-token block still scores/attends
  blocks only a later chunk row completes (original hypothesis 1, confirmed
  at last).
- Fix v2 (same env `DS4_QWEN4_VERIFY_PER_ROW=1`, ds4.c): under the flag and
  2<=T<=8, (a) `qwen4_graph_hc_mix` dispatches gate/mix per row (T=1 kernel),
  (b) `qwen4_graph_attention_tail` runs the attention core per row with the
  row's own universe ((pos+1)/4) and key count. matvec intercept unchanged.
- Gate results (all under the flag ON):
  - rowcount-ab: 8 pairs, 962/962 stage hashes identical per pair, row0/row1
    logit maxabs = EXACTLY 0.000e+00, argmax IDENTICAL both rows.
  - --qwen4-verify-identity 3-grid sweep: flips=0 on prompts 03/06/07,
    adaptive + depth2 + depth3, think-none (03) and think-high (03).
  - control (flag OFF, forced depth2, prompt 03): the pair-kernel flip
    remains - the gate only passes with the fix on, as designed.
  - SERVER: [3,3] repeat on one clean engine: BOTH requests now BYTE-IDENTICAL
    to the serial reference (the @884 reuse artifact is gone - it was the
    pair-kernel/selection drift + history-dependent depth grid combining).
  - battery per-row v2 adaptive x2 (pr2_adaptive_1/2): run-to-run identical,
    and **10/10 prompts byte-identical to the banked serial battery** -
    spec==serial end-to-end on the completions path. Acceptance 1.733
    tok/cycle; deep engagement 17/2123 as before.
- Cost (honest): per-row v2 battery 71.6/77.0 s == serial 69.6/73.8 s
  (within noise; +29% vs drift-default 55.4). Full per-row verify pays
  ~1.7x per committed token like serial by construction (each row re-reads
  trunk weights + its own attention pass) - spec's throughput gain evaporates
  while correctness holds. forced-3 v2: 83.3 s, 2.196 tok/cycle, P(a2|a1)
  70.2% (n=1176).
- Consequence for the DEFAULT decision (task 2 rewritten): per-row v2
  delivers the identity guarantee but NOT a faster stream than serial;
  drift-default remains fastest. The only path that keeps BOTH is fused
  ROW-INVARIANT kernels (T=2 dispatch with per-row serial reduction trees -
  i.e. B0/lane-2's blueprint now carries a hard correctness requirement:
  the pair/gate-mix kernels, the mv_ext family, and the selection universe
  must be invariant-by-construction + tested by --qwen4-rowcount-ab).
  Flip recommendation: keep OFF; adopt v2 as the correctness/audit mode.
- Queue: (i) golden re-capture only becomes meaningful with a row-invariant
  fused path or with per-row v2 default (goldens currently encode drift);
  (ii) `DS4_QWEN4_NO_HC_PAIR=1` remains a useful standalone A/B;
  (iii) session depth-counter carry-over at slot reuse (grid determinism)
  is now harmless for identity but still makes the depth grid
  request-history dependent - reset decision open (queue #2).

### NOT the cause (retracted earlier misattributions, all proven)
- grouped MoE kernels (bit-exact replay vs slot twins, all layers,
  production inputs: mid/down/chain diffs = 0)
- shared-dense vs shared-slot (that was a REAL second inexactness, now
  avoided via GROUP_EXACT's shared-slot routing)
- the attention rows-kernel idea (B0, reverted)
- any of tonight's shipped commits: default binary reproduces
  baseline-of-day byte-for-byte (`/tmp/btt_adab.json` == dfl runs)

## Follow-ups
1. Build `--qwen4-verify-identity` probe (ds4_test, model test, engine
   stopped): same engine, two sessions — spec-cycle loop (direct
   `ds4_session_qwen4_spec_cycle`) vs pure `ds4_session_eval` walk;
   compare per-position argmax token, print first mismatch with pos, pos%4,
   accept flag, top-2 logits on both sides. Then localize stage (select vs
   splits vs regen) per hypothesis.
2. Until root-caused: do NOT flip MOE_GROUP_EXACT default, do NOT ship
   DEPTH_UNLOCK (both already env-gated off), and treat the depth-policy
   tuning (B3) as BLOCKED on this fix.
3. Once identity holds: re-run the whole parity battery matrix (tooling
   ready) and only then re-open the policy engagement tuning.
