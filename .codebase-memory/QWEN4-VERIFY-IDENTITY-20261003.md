# Qwen4 verify-vs-serial identity violation — found 2026-10-03 (pre-existing)

> **STATUS 17:40 2026-10-03: ROOT-CAUSED + FIXED (env-gated `DS4_QWEN4_VERIFY_
> PER_ROW=1`), session-level probes flips=0; one server-path residual open —
> see ROOT CAUSE section below. The original "leading hypotheses" text is kept
> as history; hypotheses 2 was correct in substance (split-window redistribution
> generalizes to the matvec tree itself), hypotheses 1 partially (pos%4=3 was a
> near-tie coincidence, not the selection universe).

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
- Server battery (QWEN_BATCH_SESSION=0, temp 0): serial 70.3-73.8 s |
  drift-default 55.4 s | per-row-correct 65.4 s. Per-row spec is still
  ~12% faster than serial and now provably identical to it at session level.
- Kernel-level rowcount sweep was built (then removed: its own harness hit an
  arena/dangling-scope crash; findings above preserved in this doc; the
  engine probe is the durable regression).

### RESIDUAL (open, narrow): server-completions battery prompt 3 @884 still
differs per-row-vs-serial even though the session-level probe (same prompt,
think-high included, 512 tok) reports flips=0. So the remaining server-path
difference is NOT matvec row-count coupling - candidates: the server's
spec skip/rollback boundaries (DSML think-close transitions where spec
toggles mid-stream, `glm_mtp_rollback`-analog state, completions vs chat
prompt shaping). Needs a server-level trace bisect in a fresh session.

### Default posture (17:40)
Env OFF (default binary == drift-status-quo, golden vectors unchanged) while
the residual is scoped. Recommended after residual lands: flip per-row ON
by default + re-capture goldens (they encode the drifting stream). Plain
T=1 decode unaffected either way.

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
