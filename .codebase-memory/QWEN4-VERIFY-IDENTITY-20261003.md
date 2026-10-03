# Qwen4 verify-vs-serial identity violation — found 2026-10-03 (pre-existing)

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

## What is NOT the cause (all proven tonight)
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
