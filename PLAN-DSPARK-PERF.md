# DSpark Performance Lessons from oMLX/DFlash — Implementation Plan

Status: plan (2026-08-05). Goal: apply the architectural lessons learned from
auditing oMLX's embedded MTP (`omlx/patches/mlx_lm_mtp`) and external-drafter
DFlash (`dflash_mlx`) to ds4's DSpark speculative decode.

## Background / why this plan exists

Thermal-controlled A/B on M5 Max (both our fork and pure upstream `6747e77`):
GREEDY G/OFF = 0.87, sampling-verifier ON/OFF = 0.86-0.89. DSpark is net-negative.
Per-cycle profile (27 proposals): propose ≈ 9.7ms (3-stage chain 7.5ms dominant),
verify ≈ 18.8ms, main decode ≈ 28ms/token. GPU busy 90% (GPU-bound, not sync-bound).

oMLX/DFlash implement the *same* speculative idea but get it faster and know when
to stop. This plan ports those lessons to ds4.

## Lessons learned (from the audit)

1. **Single host sync per cycle** — verify + greedy acceptance computed in-graph
   (`mx.argmax` + `cumprod`), only token ids cross to host. ds4 does blocking
   `waitUntilCompleted` on the whole verify CB then full-row readbacks.
2. **In-graph stochastic acceptance** (Leviathan/Chen) — p/q ratios, residual
   samples, bonus draw all GPU-side, one sync. ds4's sampling verifier reads full
   129k-vocab rows to CPU and qsorts them (ds4.c:62169/62278, outside verify timing).
3. **Async propose overlap** — next draft dispatched `mx.async_eval` fire-and-forget,
   resolves inside the next cycle's sync. ds4 serializes propose → sync → verify → sync.
4. **Sharper draft sampler** (temp 0.6/top_p 0.95/top_k 20 for stochastic) —
   matched-temp drafts collapse acceptance to 10-20% on creative prose.
5. **Measure-then-stop scheduler** — park at depth 0 / hand off to plain decode when
   speculation loses (exit_margin, exit_streak); adaptive depth by measured cost.
6. **Tree verification** (DFlash) — draft multiple branches, verify in ONE target
   forward, maximizing accepted tokens per verify.

## Current ds4 architecture (mapped)

- Propose: `ds4_session_prepare_dspark_draft_impl` (ds4.c:60258) →
  `metal_graph_eval_dspark_stage_chain` (ds4.c:31672, 3 stages, one CB, one sync)
  → markov probe on CPU (`dspark_apply_markov_greedy_probe`) → confidence truncation.
- Verify (GREEDY): `ds4_session_eval_dspark_speculative_argmax` (ds4.c:62319) →
  `metal_graph_verify_suffix_tops` (ds4.c:34474) reads only row_tops ints; `end_commands`
  blocks (`ds4_gpu_finish_command_buffer` → `waitUntilCompleted`, ds4_metal.m:930).
- Verify (sampling): `ds4_session_eval_dspark_speculative_sample` (ds4.c:61947) →
  per-position full-row readback + CPU qsort (`ds4_spec_target_dist`, ds4.c:37886).
- Scheduler: `ds4_session_dspark_scheduler_note/should_skip` (ds4.c:48708+) — already
  measures saved_ms/acceptance and skips bursts; lacks park-and-exit.
- Metal overlap primitives ALREADY EXIST: `MTLSharedEvent` used for TP + selected-id
  overlap (ds4_metal.m:8245-8260, 8344-8351). Reusable for async propose.

## Implementation phases

### Phase 1 — Single-sync verifier + in-graph greedy acceptance (biggest, safest win)

**Goal:** kill the full-row CPU readbacks in the sampling path; make greedy verify
read only token ids.

**What:**
- Extend `metal_graph_verify_suffix_tops` so the acceptance match-count `m`
  (= argmax == draft, cumprod'd) is computed **in-graph** on GPU, returning
  just `m` + the boundary token id to host (like oMLX `_run_verify_cycle_chain`).
- Add a small Metal kernel: for each of the `top_rows` logits rows, argmax →
  compare vs draft token → prefix-cumprod → reduce to one int. One tiny readback.
- Reuse the existing `ds4_gpu_argmax_tensor` / `indexer_topk_tensor` (already in the
  fused-head path) — chain them with an equality+prefix kernel.

**Files:** ds4.c (`metal_graph_verify_suffix_tops_impl`, new kernel launch),
ds4_metal.m (new shader + encode).

**Expected:** verify wall-time drops (fewer/earlier syncs), and the sampling path
loses its hidden full-row readbacks. Do NOT expect acceptance to change.

### Phase 2 — Async propose overlap (hide the 9.7ms propose)

**Goal:** the 3-stage propose chain runs on the GPU while the host does verify
bookkeeping, resolving at the next sync — mirroring `mx.async_eval`.

**What:**
- Split `metal_graph_eval_dspark_stage_chain`'s CB so it commits async (no blocking
  wait) and its results are consumed by the verify CB via `MTLSharedEvent`
  (signal/wait), the pattern already proven for TP (ds4_metal.m:8344+).
- On the CPU side, move the markov probe to run after the verify sync (it needs
  the draft logits anyway) — or compute it async via a completion handler.
- The next cycle's propose is dispatched immediately after the current verify's
  encode, overlapping the verify's GPU execution.

**Files:** ds4_metal.m (shared-event gating for dspark CBs), ds4.c
(stage-chain encode split + scheduler/verify handshake).

**Expected:** propose wall-time partially hidden behind verify → maybe 3-6ms/cycle
saved. Largest single-cycle win available; requires care with CB ordering.

### Phase 3 — Sharper draft sampler for stochastic acceptance

**Goal:** raise acceptance on creative text from the current 56-88% toward 90%+.

**What:**
- For the sampling verifier path only: sample drafts with a **sharper**
  distribution (temp ~0.6/top_p 0.95/top_k ~20) instead of the target sampler,
  and feed the actual q (draft logprobs) into `ds4_spec_accept_token`
  (currently hardcoded `1.0f` point-mass at ds4.c:62037/62180).
- Requires capturing draft-logprobs at propose time (the `capture_q`/`capture_rows`
  plumbing already exists but is currently NULL'd for point-mass — ds4.c:60300).

**Files:** ds4.c (`ds4_session_prepare_dspark_draft_impl` capture path,
`ds4_session_eval_dspark_speculative_sample` accept/residual).

**Expected:** acceptance up (this is exactly oMLX's fix for creative prose). Keeps
distribution exact via Leviathan/Chen. This is the **only phase that can flip the
sign** of the economics by itself.

### Phase 4 — Measure-then-stop scheduler (park + exit)

**Goal:** stop paying propose/verify overhead when speculation is a net loss;
mimic oMLX's `park at depth 0` / hand-off to plain decode.

**What:**
- Extend the existing scheduler (ds4.c:48708+) with a **park-and-exit** state:
  after N consecutive "unprofitable" verdicts (saved_ms < 0 over a window, like
  oMLX EXIT_STREAK=16), drop the session to plain decode entirely (bypass
  `ds4_session_eval_speculative_argmax` / `_sample`).
- Keep a warm re-entry probe (periodic single propose) like `_DepthController`.

**Files:** ds4.c (scheduler state machine), ds4_server.c (per-request gating hook).

**Expected:** eliminates the ~0.87 tail on runs where speculation loses; makes the
feature self-disabling rather than "always slightly slower".

### Phase 5 — Tree verification (biggest effort, highest ceiling) — OPTIONAL/FOLLOW-UP

**Goal:** verify multiple draft branches in ONE target forward (DFlash `FlatDDTree`).

**What:** draft 2-3 parallel branches from the markov head, build a flat tree
(attention-masked), verify all branches in one batched target pass, emit the
longest accepted path. This is a substantial new kernel + verifier contract.

**Files:** new Metal tree kernel + `dspark` tree-draft in ds4.c.

**Expected:** accepted-tokens-per-verify ~2x, but high complexity. Defer until
Phases 1-3 are measured.

## Priority / sequencing

| Phase | Effort | Expected gain | Do first? |
|-------|--------|---------------|-----------|
| 1. Single-sync in-graph accept | Med | removes hidden readbacks; correctness-neutral | YES (foundation) |
| 3. Sharper draft sampler | Low-Med | **raises acceptance → can flip sign** | YES (highest ROI) |
| 2. Async propose overlap | Med-High | hides ~3-6ms/cycle | YES |
| 4. Park-and-exit scheduler | Low | removes the 0.87 tail | YES (cheap) |
| 5. Tree verify | High | best ceiling | AFTER 1-4 measured |

**Suggested order:** 1 → 3 → 2 → 4 (build correctness-neutral foundation first,
then the acceptance lever, then the overlap win, then the safety net). Re-measure
with the thermal-controlled interleaved harness (`/tmp/exp-swe-ab-greedy.sh`,
`/tmp/exp-ab.sh`) after each phase.

## Measurement protocol (unchanged from recalibration)

- High Power (`pmset -a lowpowermode 0`, `powermode 2`), 90s thermal warmup,
  interleaved OFF/ON rounds, decode-only t/s from server logs.
- SWE-long 8k/32k prompts (`/tmp/swe_long/*.txt`) + CacheTest sizes for parity.
- M4.2 distribution equivalence (`/tmp/m42_harness.py` + `compare`) after Phase 3
  (sampler change) and Phase 1 (verifier change).
- M1 kernel tests (`tests/test_spec_rejection.c`) after Phases 1 & 3.

## Risks

- Phase 1/2 CB reordering could break the fused-head path (`verify_fused_head`);
  keep `DS4_DSPARK_VERIFY_SPLIT_HEAD` as an escape hatch.
- Phase 3 changes draft distribution — must re-run M4.2 (exactness is the contract).
- Phase 4 park/exit must not interact badly with KV-disk checkpointing
  (`/tmp/ds4-kv`); verify cache state after hand-off.
- All phases must preserve the upstream GREEDY identity contract (temp-0 output
  identical to plain decode) — the current `test_mtp_generate_identity` analogue.
