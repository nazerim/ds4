# DSpark Performance — Reviewed Implementation Plan (from oMLX/DFlash audit)

Status: **reviewed & corrected** (2026-08-05). Supersedes the first draft's
assumptions where they failed scrutiny. Goal: apply the real, verified lessons
from oMLX's embedded MTP (`omlx/patches/mlx_lm_mtp`) and external-drafter DFlash
(`dflash_mlx`) to ds4's DSpark speculative decode.

---

## 0. Baseline measurements (verified, thermal-controlled)

M5 Max, High Power, 90s warmup, interleaved A/B, decode-only t/s from server logs.
Prompts: SWE-long agentic (8k/32k) + CacheTest chain (16k/32k/64k).

| build | OFF (t/s) | GREEDY (t/s) | G/OFF |
|-------|-----------|--------------|-------|
| pure upstream `6747e77` | 36.52 | 31.85 | **0.872** |
| our fork `6c07524` | 36.55 | 31.78 | **0.869** |
| sampling-verifier ON | ~34 | ~30 | **0.86-0.89** |

**Per-cycle profile (GREEDY, 27 proposals, conf 0.9):**

| component | ms each | % of decode wall |
|-----------|---------|------------------|
| propose (3-stage chain dominant) | 9.7 | — |
| verify (batched target layer pass) | 18.7 | — |
| propose+verify total | 28.5 | ~12% |
| main decode | 28.0/token | ~77% |

**Critical correction to the first draft:** the "GPU 90% busy" figure was
measured over the **whole session** (18.5s prefill + 6.3s decode). Prefill
saturates the GPU; decode does NOT. The decode-phase GPU utilization is
**unmeasured** — this is a gap, not an established fact. See Phase 0 TODO.

**Draft economics:** break-even needs `verify_ms / decode_ms_per_token` =
`18.7/28.0 = 0.67` accepted draft tokens per verify. Observed: conf 0.9 → 1.34
avg draft len but 73% are len-1; acceptance 56-88% on code text. Drafts are too
**short** (confidence truncation) and too **low-acceptance** (markov head quality).

---

## 1. Scrutiny of the first draft's assumptions

### A. "Verify + greedy acceptance in one GPU graph = big win" — **PARTIALLY WRONG**
- Scrutiny: ds4's `metal_graph_verify_suffix_tops` already reads back only
  `row_tops` ints (few bytes); `verify_read=0.026ms`. The fused-head path already
  computes argmax/top-k on GPU. So for **GREEDY**, there is no full-row readback
  problem — that assumption was wrong for the greedy path.
- What IS true: the **sampling** path reads full 129k-vocab rows to CPU and
  qsorts them (ds4.c:62169/62278), and those reads are **unmeasured** (outside
  `verify_timing`). This is the real readback cost, sampling-path only.
- **Corrected Phase 1:** single-sync in-graph acceptance is a *sampling-path*
  fix, NOT a greedy fix. Greedy verify is already top-k-readback-only.

### B. "Verify 18.8ms is the killer; reduce it" — **MISDIAGNOSED**
- Scrutiny: `verify_layer=506ms/27 = 18.7ms` runs the **full 91GB model layer
  pass over the draft rows** in batch. This is inherent to speculative decoding —
  you must evaluate the target on every draft row. oMLX's "1.15x per cycle"
  assumes a 2-token verify is ~free vs 1-token decode; on ds4/M5 the batched
  verify over N rows costs **0.67x of a single decode** but covers N rows.
- The verify cost is NOT reducible by sync tricks — it's real GPU layer work.
- **Corrected focus:** the win is NOT shrinking verify; it's raising **accepted
  tokens per verify** (longer, better-accepted drafts) so each 18.7ms verify
  returns ≥0.67 accepted tokens. This shifts the whole plan from "make the loop
  faster" to "make drafts better."

### C. "Async propose overlap hides 9.7ms" — **PARTIALLY VALID, SMALL**
- Scrutiny: propose (9.7ms) is also real GPU work on the same device. Async
  overlap hides the *host-side* gap (markov probe, bookkeeping) but NOT the GPU
  kernel time — both propose and verify contend for the same GPU. On an already
  saturated decode loop, expected saving is **~1-3ms/cycle, not 3-6ms**.
- Still worth doing (cheap, correct), but it is NOT the headline win.

### D. "Sharper draft sampler (temp 0.6) raises acceptance" — **INAPPLICABLE AS WRITTEN**
- Scrutiny: **oMLX's drafter SAMPLES drafts** (`sampler(lp_2d)` in
  `_dspark_next_drafts`), so "sharper than target" means the draft distribution
  is temp-0.6/top-k-20. **ds4's drafter is DETERMINISTIC ARGMAX**
  (`dspark_argmax_f32` in `dspark_apply_markov_greedy_probe`, ds4.c:32443). There
  is no draft sampler to sharpen.
- The point-mass q=1.0 acceptance (ds4.c:62037/62180) is **correct** for a greedy
  drafter — not a bug. oMLX's Leviathan/Chen p/q needs a real q because it
  *samples* drafts.
- **Corrected Phase 3:** the real lever is **draft length + markov-head quality**,
  not a sharper sampler. Two concrete options:
  1. **Lower/remove confidence truncation** — draft the full 5-token block every
     cycle (oMLX drafts full `depth` always, no confidence head). Test conf 0.3 /
     scheduler-off at full block. We already measured len-5 at conf 0.3 but with
     47.6% acceptance → still net-negative. The confidence head is NOT the only
     problem; markov head acceptance is.
  2. **A sampling drafter** — IF we want Leviathan/Chen exactness at temp>0, we'd
     have to change the drafter to sample. That's a big change and only matters
     for temp>0 (which we've established is the least-used path). LOW priority.

### E. "GPU is 90% busy → GPU-bound, can't parallelize" — **UNVERIFIED**
- Scrutiny: the 90% figure mixes prefill (18.5s) and decode (6.3s). Decode-phase
  utilization is unknown. If decode is NOT saturated, async overlap and even
  dual-queue parallelism have MORE headroom than the first draft claimed.
- This directly undermines the first draft's "not worth trying multiple queues"
  conclusion. **Must measure decode-phase GPU utilization first** (Phase 0).

### F. "Park-and-exit scheduler" — **VALID, KEEP**
- Scrutiny: upstream scheduler (ds4.c:48708+) already skips bursts based on
  saved_ms/acceptance. Adding park-and-exit (hand off to plain decode after a
  losing streak, with warm re-entry) is correct, cheap, and matches oMLX's
  `EXIT_STREAK` / DFlash's `drop-to-reduced`. Confirmed worth doing.

### G. "Tree verification (DFlash)" — **VALID CONCEPT, HIGH EFFORT, DEFER**
- Scrutiny: DFlash's `FlatDDTree` verifies multiple draft branches in one target
  forward → more accepted tokens per verify. This is the ONE structural change
  that directly attacks assumption B (raise accepted-tokens-per-verify). But it's
  a new kernel + verifier contract. Defer until B-focused experiments (Phase 3)
  show whether acceptance is the binding constraint.

---

## 2. Corrected thesis

The first draft framed DSpark's loss as "loop overhead (readbacks, syncs, no
overlap)." The corrected thesis:

> **DSpark loses because accepted-tokens-per-verify is too low, not because the
> loop machinery is too slow.** Verify is 18.7ms of real GPU work; it needs ≥0.67
> accepted tokens per verify to break even. Confidence truncation shortens drafts
> and markov-head acceptance is 56-88%, so the return on each verify is below
> break-even. The loop-optimization lessons (single-sync, async overlap) are real
> but secondary; the decisive work is **raising accepted-tokens-per-verify**.

Consequences:
- Phase 1 (in-graph acceptance) = small, sampling-path-only cleanup, NOT the
  headline.
- Phase 2 (async overlap) = small host-gap win (~1-3ms), not 3-6ms.
- **Phase 3 is redefined** as the headline: draft-length + acceptance experiments,
  not "sharper sampler."
- Phase 0 (measure decode-phase GPU utilization) is a NEW required gate — it
  determines whether overlap/parallelism even has headroom.

---

## 3. Phases with concrete steps

### Phase 0 — Measure decode-phase GPU utilization (NEW, GATE)

**Why:** the 90% figure conflates prefill+decode. Whether async/parallelism can
help (and whether the loop is even the bottleneck) depends on decode-only GPU
utilization.

**Steps:**
- [ ] 0.1 Add decode-phase GPU-busy accounting to the existing
  `DS4_METAL_GPU_BUSY_PROFILE` path (ds4_metal.m `ds4_gpu_wait_command_buffer`)
  — track a `g_decode_busy_accum` only for command buffers committed during
  decode (not prefill), or add a phase marker.
- [ ] 0.2 Instrument propose/verify host-gap separately: measure `now_sec()`
  around the markov probe and around the verify `end_commands` wait, per cycle.
- [ ] 0.3 Re-run the thermal-controlled GREEDY A/B; report decode-phase GPU
  busy % + host-gap ms.
- [ ] 0.4 **Decision gate:** if decode-phase GPU busy ≥90%, the loop is GPU-bound
  → overlap helps little; focus on Phase 3 (acceptance). If <75%, overlap and
  dual-queue have real headroom → prioritize Phase 2 and reconsider dual-queue.

**Exit criteria:** a number, not a guess: `decode_gpu_busy%` + `host_gap_ms/cycle`.

---

### Phase 1 — In-graph acceptance for the SAMPLING path only (small cleanup)

**Goal:** kill the sampling-path full-row CPU readbacks (ds4.c:62169/62278) by
computing acceptance in-graph. NOT for the greedy path (already top-k-only).

**Steps:**
- [ ] 1.1 Add a Metal kernel: per logits row, `p = softmax row`; extract
  `p[draft]`; compare vs stored q (or point-mass); output accept bit + residual
  index for the reject position. One small readback of token ids.
- [ ] 1.2 Rewire `ds4_session_eval_dspark_speculative_sample` to call it instead
  of the per-position `metal_graph_read_spec_logits_row` + `ds4_spec_target_dist`
  loop.
- [ ] 1.3 Keep `ds4_spec_target_dist` for the len-1 first-token check (cheap,
  already-CPU row).
- [ ] 1.4 Re-run M4.2 distribution equivalence + M1 kernel tests.
- [ ] 1.5 Measure: does sampling-path decode t/s improve (remove hidden readbacks)?

**Expected:** removes the unmeasured readback cost; correctness-neutral (must re-run
M4.2). Does NOT fix the base 0.87.

---

### Phase 2 — Async propose overlap (small host-gap win)

**Goal:** dispatch the next 3-stage draft chain so the host markov probe overlaps
the current verify's GPU execution. Expect ~1-3ms/cycle, gated by Phase 0.3.

**Steps:**
- [ ] 2.1 Split `metal_graph_eval_dspark_stage_chain` encode so `end_commands`
  does not block host bookkeeping; gate consumption via existing `MTLSharedEvent`
  pattern (ds4_metal.m:8344+).
- [ ] 2.2 Move the CPU markov probe (`dspark_apply_markov_*`) after the verify
  sync (it needs draft logits anyway), or run it on a completion handler.
- [ ] 2.3 Verify CB ordering with the scheduler + `dspark_capture` state.
- [ ] 2.4 Re-run GREEDY A/B. **Only proceed if Phase 0.3 showed <90% decode busy.**
- [ ] 2.5 If Phase 0.3 showed ≥90% busy, DEPRIORITIZE: skip to Phase 3.

**Expected:** 1-3ms/cycle saved, only if decode-phase has idle GPU.

---

### Phase 3 — Raise accepted-tokens-per-verify (HEADLINE, redefined)

**Goal:** make each 18.7ms verify return ≥0.67 accepted tokens. Attack BOTH
draft length (confidence truncation) and draft acceptance (markov quality).
NOT "sharper sampler" — that was inapplicable (drafter is greedy argmax).

**Sub-phase 3a — Draft length: remove/reduce confidence truncation**
- [ ] 3a.1 Experiment matrix: conf ∈ {0.9, 0.6, 0.3, 0.0}, scheduler on/off, on
  SWE-long 8k/32k. Measure avg accepted/cycle vs break-even 0.67.
- [ ] 3a.2 If full-block drafts (len-5) still fail break-even due to acceptance,
  confirm it's acceptance, not length (record len-hist + acceptance per position).
- [ ] 3a.3 Consider drafting the full block unconditionally when confidence head
  is untrusted (env `DS4_DSPARK_NO_CONFIDENCE`), letting acceptance decide.

**Sub-phase 3b — Markov-head acceptance (the real lever)**
- [ ] 3b.1 Measure per-position acceptance of the markov head on code text
  (position 1,2,3,4,5) via `DS4_DSPARK_SPEC_LOG`/stats `accepted_len_hist`.
- [ ] 3b.2 If acceptance collapses after position 1-2, the markov head is the
  ceiling — no scheduler/sync change fixes it. Evaluate:
  - (i) **Tree verification** (promote Phase 5): draft top-2 at the collapse
    position, verify both branches in one pass → recovers accepted tokens.
  - (ii) Accept the ceiling: keep DSpark opt-in greedy-only, document.
- [ ] 3b.3 Re-run M4.2 after ANY draft-distribution change (exactness contract).

**Sub-phase 3c — Sampling drafter (ONLY if temp>0 path matters)**
- [ ] 3c.1 Assess: is temp>0 speculation worth supporting at all? Evidence so far
  says greedy-only is upstream's stance; temp>0 is our opt-in addition. If greedy
  can't break even, temp>0 won't either.
- [ ] 3c.2 DEFER unless a greedy-draft win (3a/3b) is found first.

**Exit criteria:** `avg_accepted/cycle ≥ 0.67` AND `G/OFF > 1.0` on SWE-long 8k,
reproducible across 3 interleaved rounds.

---

### Phase 4 — Park-and-exit scheduler (cheap safety net)

**Goal:** self-disable speculation when it loses, like oMLX `EXIT_STREAK` / DFlash
`drop-to-reduced`.

**Steps:**
- [ ] 4.1 Add park-and-exit state to `ds4_session_dspark_scheduler_note`
  (ds4.c:48725+): after N consecutive unprofitable verdicts (saved_ms<0), hand the
  session off to plain decode (bypass `_speculative_argmax/_sample`).
- [ ] 4.2 Warm re-entry: periodic single propose after park (like `_DepthController`
  probes) to re-measure.
- [ ] 4.3 Verify KV-disk checkpointing (`/tmp/ds4-kv`) state is consistent after
  hand-off.
- [ ] 4.4 Re-run A/B: does park-and-exit remove the 0.87 tail?

**Expected:** makes the feature self-disabling; tail converges toward 1.0 on runs
where speculation loses. Correctness-neutral (plain decode fallback).

---

### Phase 5 — Tree verification (defer; promote if 3b shows acceptance ceiling)

**Goal:** verify multiple draft branches in ONE target forward (DFlash `FlatDDTree`).

**Steps (when promoted):**
- [ ] 5.1 Draft top-2 branches from markov head at the acceptance-collapse position.
- [ ] 5.2 Build flat tree with attention masking; one batched target verify.
- [ ] 5.3 Emit longest accepted path; rollback semantics like DSpark frontier.
- [ ] 5.4 Re-run M4.2 (exactness: rejected branches must not leak).

**Expected:** accepted-tokens-per-verify ~2x at the collapse position. High effort;
**only** if Phase 3b.2 shows acceptance is the binding constraint.

---

## 4. Priority / sequencing (revised)

| Phase | Effort | Expected gain | Evidence | Do first? |
|-------|--------|---------------|----------|-----------|
| **0. Decode-phase GPU util** | Low | decision data | unverified 90% | **YES — gate** |
| **3. Raise accepted/verify** | Med | **can flip sign** | break-even 0.67 vs ~0.5-1.3 | **YES — headline** |
| 4. Park-and-exit | Low | remove 0.87 tail | valid | YES (cheap, any time) |
| 1. In-graph acceptance | Med | sampling-path cleanup | greedy already top-k | YES (small) |
| 2. Async overlap | Med | 1-3ms if idle GPU | gated by P0 | conditional |
| 5. Tree verify | High | ~2x accepted/verify | concept valid | AFTER 3b |

**Order:** 0 → 3 → 4 → 1 → (2 only if P0<90%) → 5 (if 3b confirms ceiling).
Rationale: Phase 0 tells us where the real bottleneck is. Phase 3 attacks the
verified break-even gap (accepted/verify). Phase 4 is the cheap safety net that
stops the bleeding regardless. Phases 1-2 are polish that only matter once the
headline (3) shows the loop can win.

---

## 5. TODO checklist (executable)

### Phase 0 — decode-phase GPU utilization
- [ ] 0.1 decode-phase GPU-busy accounting (ds4_metal.m)
- [ ] 0.2 per-cycle host-gap instrumentation (markov probe, verify wait)
- [ ] 0.3 thermal A/B re-run with decode-phase metric
- [ ] 0.4 DECISION: GPU-bound (≥90%) → focus Phase 3; idle (<75%) → unlock Phase 2

### Phase 3 — accepted-tokens-per-verify (headline)
- [ ] 3a.1 conf/scheduler matrix on SWE-long 8k/32k
- [ ] 3a.2 record len-hist + per-position acceptance
- [ ] 3a.3 full-block draft experiment (`DS4_DSPARK_NO_CONFIDENCE` or conf 0)
- [ ] 3b.1 per-position markov acceptance on code
- [ ] 3b.2 decide: tree-verify promotion vs accept ceiling
- [ ] 3b.3 re-run M4.2 after any draft-distribution change
- [ ] 3c.1 assess whether temp>0 path is worth supporting
- [ ] **GATE:** avg_accepted/cycle ≥ 0.67 AND G/OFF > 1.0

### Phase 4 — park-and-exit scheduler
- [ ] 4.1 park-and-exit state machine (ds4.c:48725+)
- [ ] 4.2 warm re-entry probes
- [ ] 4.3 KV-disk cache consistency after hand-off
- [ ] 4.4 re-run A/B; confirm tail → 1.0

### Phase 1 — in-graph acceptance (sampling path)
- [ ] 1.1 Metal accept/residual kernel
- [ ] 1.2 rewire `ds4_session_eval_dspark_speculative_sample`
- [ ] 1.3 keep len-1 CPU first-token check
- [ ] 1.4 M4.2 + M1 kernel tests
- [ ] 1.5 measure sampling-path decode t/s

### Phase 2 — async propose overlap (conditional)
- [ ] 2.1 async stage-chain CB split (MTLSharedEvent)
- [ ] 2.2 markov probe after verify sync
- [ ] 2.3 CB ordering vs scheduler/capture
- [ ] 2.4 re-run GREEDY A/B
- [ ] 2.5 deprioritize if P0 showed ≥90% busy

### Phase 5 — tree verification (conditional)
- [ ] 5.1 top-2 branch drafts at collapse position
- [ ] 5.2 flat-tree attention-masked verify kernel
- [ ] 5.3 longest-path emit + rollback
- [ ] 5.4 M4.2 exactness re-run

---

## 6. Measurement protocol (unchanged)

- High Power (`pmset -a lowpowermode 0`, `powermode 2`), 90s warmup, interleaved
  OFF/ON rounds, decode-only t/s.
- Prompts: `/tmp/swe_long/*_8k.txt`, `*_32k.txt` + CacheTest `prompt_{16,32,64}k.txt`.
- M4.2: `/tmp/m42_harness.py` + `/tmp/m42_compare.py` (N=200/mode).
- M1: `tests/test_spec_rejection.c`.
- A/B harness: `/tmp/exp-swe-ab-greedy.sh` (GREEDY/OFF), `/tmp/exp-ab.sh` (ON/OFF).
- Per-cycle stats: `DS4_DSPARK_STATS=1` + `DS4_DSPARK_PROP_PROFILE=1` +
  `DS4_METAL_GPU_BUSY_PROFILE=1` (with the Phase-0 decode-phase fix).

---

## 7. Risks

- **Phase 0.1** touches the hot `wait_command_buffer` path — guard the extra
  accounting behind the existing env gate.
- **Phase 3a** (full-block drafts) changes draft length → MUST re-run M4.2
  (exactness contract) and confirm the GREEDY identity contract (temp-0 output
  identical to plain decode).
- **Phase 3b** if acceptance is a hard ceiling, tree-verify (Phase 5) is the only
  structural fix; be honest that the ceiling may be unbeatable on this drafter.
- **Phase 4** park/exit must not corrupt KV-disk checkpoints (`/tmp/ds4-kv`);
  validate cache state after hand-off.
- **Phase 1/2** CB reordering could break `verify_fused_head`; keep
  `DS4_DSPARK_VERIFY_SPLIT_HEAD` as the escape hatch.

---

## 8. Definition of done

- [ ] Phase 0 complete: decode-phase GPU utilization measured; bottleneck identified.
- [ ] Phase 3 gate passed: avg_accepted/cycle ≥ 0.67 AND G/OFF > 1.0 on SWE-long 8k,
  3 interleaved rounds.
- [ ] OR the ceiling is documented with data: per-position acceptance shows the
  markov head cannot exceed break-even, and the decision is recorded.
- [ ] M4.2 + M1 + GREEDY-identity all pass at every draft-behavior change.
- [ ] All benchmark logs + prompts archived in `misc/experiment-a-state.md`.
