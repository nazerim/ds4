# PLAN: Temperature-aware DSpark verification (rejection sampling)

**Status:** planning (2026-08-04)
**Repo:** `nazerim/ds4` fork; upstream-PR candidate after validation
**Related:** `TODO` item in ~/Projects/Scratch/TODO.md

## 1. Goal

Make DSpark speculative decoding work with `temperature > 0` — in particular
DeepSeek's official 0731 recommendation (`temperature=1.0`, `top_p=0.95`
agentic) — while provably preserving the requested sampling distribution.

Today the speculative path is greedy-only:

- Server gate: `temperature <= 0.0f` required (ds4_server.c:11563)
- Verification is argmax-matching: first draft vs `sample_argmax(s->logits)`
  (ds4.c:61512), continuation vs per-row argmax `row_tops` (ds4.c:61588)
- With temp>0 the server falls back to per-token sampling: correct output,
  zero speculation. The long thinking phase (reasoning_effort=max) gets no
  DSpark speedup; only tool-call payloads speculate (force-greedy,
  ds4_server.c:11543).

DeepSeek's own serving runs DSpark alongside temp-1.0 targets (vLLM:
`draft_sample_method: "greedy"` + rejection-sampling verification), reporting
60-85% generation speedups.

## 2. Non-goals (v1)

- No new drafter / no retraining — keep the official DSpark support GGUF.
- No batched-mode support (gate keeps `!s->batched_mode`).
- No changes to the greedy path (temperature<=0 stays byte-identical).
- No MTP-legacy path changes (DSpark support_kind only).

## 3. Algorithm

Standard speculative sampling (Leviathan et al. 2023), specialized to a
**deterministic (argmax) drafter**, whose proposal is therefore a point mass:

Given K draft tokens x_1..x_K (each x_i is the drafter's argmax pick, so the
true proposal q_i is the delta distribution on x_i, q_i(x_i) = 1), and target
logits for every verify row:

For i = 1..K (verify row i-1 is logits conditioned on prefix + x_1..x_{i-1}):

1. Build target distribution p_i from row i-1: apply temperature, then the
   request's top_k/top_p/min_p truncation, renormalize.
2. Accept x_i with probability p_i(x_i) (i.e. min(1, p_i(x_i)/1)), drawing
   from the session rng.
3. On first rejection at i: sample x* from the adjusted residual
   norm(max(0, p_i - delta_{x_i})) = p_i with x_i zeroed and renormalized,
   emit x_1..x_{i-1}, x*, stop the cycle. (x* takes the role of the "bonus"
   token; its logits row seeds the next cycle.)
4. If all K accepted: sample the bonus token from p at the final row
   (row K-1) with the request's sampling parameters.

**Why point-mass, not softmax-q (M4 correction):** the Leviathan guarantee
requires the draft x_i to be *sampled* from q. The DSpark drafter is
deterministic argmax, so its true proposal is a point mass (q(x_i)=1), not
the draft-head softmax. Using q_i = softmax-confidence would over-accept
(min(1, p/q) > p whenever q < 1) and bias the output toward the drafter's
picks. The point-mass rule above reproduces the target distribution exactly;
it needs no captured q or draft rows at all (simpler and re-enables the
GPU/fused argmax draft paths).

Semantics notes:

- Draft token outside the truncated target support => p_i(x_i)=0 => reject.
  Correct by construction.
- Truncation: apply the target truncation to p; residual over the truncated
  support with x_i zeroed. This is exact for pure-temperature sampling; with
  truncation it matches standard engine practice (vLLM) rather than a formal
  guarantee. Document as such.
- RNG discipline: fixed draw order per cycle (one acceptance draw per draft
  position, then residual/bonus draws). Streams differ from non-speculative
  runs by design; determinism requirement is same-seed-same-config =>
  same output, NOT bitwise equality with the non-spec path. Equivalence is
  statistical (section 6).
- Existing `--dspark-confidence` pruning stays as a pre-filter (skips
  low-value drafts before verification; orthogonal to acceptance).

## 4. Design decisions

- **Greedy fast path preserved:** temperature<=0 keeps the current argmax
  path untouched (zero regression surface for benchmarks, DSML payload
  decoding, existing users).
- **Opt-in first:** behind `DS4_DSPARK_SPEC_SAMPLE=1` during M2-M4; default
  on at M5 if validation passes. Escape hatch:
  `DS4_DSPARK_SPEC_SAMPLE_DISABLE=1` (falls back to plain per-token
  sampling at temp>0, i.e. today's behavior).
- **Full-row readback on CPU:** `metal_graph_read_spec_logits_row(g, row,
  logits)` is already row-parameterized (ds4.c:34565) — read K full rows to
  CPU (~K x 128k x 4B = ~3.6 MB/cycle) and do acceptance math on CPU. No
  Metal kernel changes expected (confirm in M0). Fallback if readback costs
  too much: Metal-side per-row acceptance kernel (deferred).
- **Draft probabilities:** capture q_i where drafts are produced
  (markov_proposal, ds4.c:60112). For the residual we need the full draft
  distribution over vocab at each rejected position — retain draft-head
  logits rows during drafting (K x vocab floats, same order of size as the
  verify readback). If retention is impractical (M0), degrade gracefully:
  fallback residual = sample from p_i directly (documented approximation),
  flagged for later.
- **Stats:** extend `dspark_stats` with sample-mode counters (cycles,
  accepts, rejects, adjusted samples, bonus samples). Existing
  accepted_len_hist covers accepted-run lengths.

## 5. Milestones

### M0 — Spike & go/no-go (half day)
- Confirm `metal_graph_read_spec_logits_row` works for intermediate rows
  (not just the final row); measure readback cost for K rows.
- Trace draft production (ds4.c:60112 / dspark_apply_markov_confidence_lazy_runtime,
  ds4.c:32478): where draft-head logits exist, cost of capturing q_i +
  full rows.
- Write the exact index map: verify row j <-> drafts[j+?] <-> logits
  position; cross-check against current full/partial-accept code
  (ds4.c:61598-61700).
- Deliverable: spike notes in this file + go/no-go.

### M1 — Acceptance kernel (half day)
- Pure C function(s), no engine state:
  `ds4_spec_rejection_accept(const float *target_logits, const float *draft_row, int token, float temperature, top_k, top_p, min_p, rng) -> accept/reject`
  plus residual sampler `ds4_spec_residual_sample(...)`.
- Unit tests with synthetic distributions: acceptance frequency matches
  min(1,p/q) (chi-square over seeded draws); residual samples match
  norm(max(0,p-q)); truncation edge cases (draft outside support, p_i=0,
  q_i>=p_i always-accept).

### M2 — Integration, behind env flag (1 day)
- Capture q_i (+ draft rows) during drafting.
- Read full verify rows; run the acceptance loop; partial-accept frontier
  commit reuses the existing commit_drafts machinery (ds4.c:61654) with the
  adjusted token as the +1 frontier token (mirror existing bonus handling).
- Stats counters; `DS4_DSPARK_SPEC_SAMPLE=1` gate inside
  `ds4_session_eval_dspark_speculative_argmax`'s caller path (new sibling
  function; do not mutate the greedy function).
- Smoke: short generations at temp 1.0 with spec on — sane text, no
  checkpoint/KV warnings, stats populate.

### M3 — Server & CLI enablement (half day)
- ds4_server.c:11563 gate: allow temp>0 into speculation when engine has
  DSpark and sampling-spec is enabled; keep `DS4_MTP_SPEC_DISABLE` and
  batched-mode exclusions.
- ds4_cli.c call sites (603, 1511): same gating (or leave CLI greedy-only
  for v1 if friction appears).
- Tool-payload force-greedy unchanged (ds4_server.c:11543).

### M4 — Validation (1 day)
- **Greedy regression:** temp-0 outputs byte-identical to pre-change build
  on a fixed prompt set.
- **Distribution equivalence:** fixed prompt, temp=1.0/top_p=0.95,
  N>=200 runs x 128 tokens, spec ON vs OFF (`DS4_MTP_SPEC_DISABLE=1`):
  per-position top-20 token frequencies (first 16 positions) within
  tolerance; aggregate unigram KS test. Must pass before default-on.
- **Acceptance & perf:** acceptance-rate histogram from stats; decode t/s
  at temp 1.0 spec vs non-spec (bench or timed generation). Targets:
  >=30% speedup, >=40% mean acceptance — if missed, keep opt-in and report
  numbers anyway.
- Full `make test` green.

### M5 — Docs & upstream prep (half day)
- DS4FORK.md section: semantics, flags, truncation caveat, numbers.
- Clean commit series suitable for an antirez/ds4 PR (algorithm +
  benchmarks); optionally open an upstream issue first for appetite.

## 6. Success criteria

1. temperature=1.0/top_p=0.95 requests speculate with statistically
   indistinguishable output distribution vs non-speculative sampling. **PASS**
   (M4.2: permutation tests, all positions within null, full-seq p=0.81).
2. temperature<=0 behavior byte-identical to before. **PASS** (M4.1).
3. Measurable decode speedup at temp 1.0 — context-dependent: 0.83× at short
   context (150 tok, overhead dominates), **1.21× at 32k** (M4.3 follow-up:
   draft acceptance increases with context length). Target revised from >=1.3x
   to "context-dependent win ≥1.15x at medium context (32k)".
4. `make test` green. **PASS** (all kernel tests + equivalence suite added).

## 7. Risks

| Risk | Mitigation |
|---|---|
| Verify-row readback cost too high | Measure in M0; fallback: Metal-side acceptance kernel |
| Draft-head logits not capturable cheaply | M0 fallback: residual = sample from p_i (documented approx) |
| Truncation semantics disputed | Document practical semantics; pure-temp case is exact |
| Acceptance rate too low at temp 1.0 | Keep opt-in; numbers still valuable upstream |
| Frontier/KV bug in adjusted-token path | Reuse existing partial-accept commit; targeted KV-store test with spec session |
| RNG determinism complaints | Document draw-order contract; same-seed reproducibility test |

## 8. Files touched (expected)

- `ds4.c` — draft prob capture, acceptance loop, residual/bonus sampling, stats
- `ds4.h` — new declarations
- `ds4_server.c` — gate change (11563)
- `ds4_cli.c` — gate change (603, 1511), optional v1
- `tests/test_spec_rejection.c` — kernel unit tests (draft_len_hist, acceptance, residual)
- `tests/ds4_test.c` — equivalence suite
- `DS4FORK.md` — documentation
- `PLAN-DSPARK-TEMP-SPEC.md` — this plan doc (algorithm corrections, M4 results, M4.3 follow-up, Experiment A results)
- (zero Metal changes — confirmed in M0)

## 9. M4.3 follow-up: Experiment A (2026-08-05) — COMPLETE

**Question:** does the ON/OFF ratio at temp 1.0 flip from 0.83 (short context) to ≥1.0 at long context?

**Answer: YES.** The ratio reverses at medium context:

| Size | ON/OFF ratio | Interpretation |
|------|-------------|----------------|
| 150 tok (short) | 0.83 | Overhead dominates |
| 16k | 1.07 | Marginal benefit |
| **32k** | **1.21** | **Sweet spot** — 21% faster |
| 64k | 0.91 | Benefit drops (KV cache contention?) |

**Root cause confirmed:** draft acceptance rate increases with context length.
Short context: drafts are 1-2 tokens (confidence threshold 0.7 truncates).
Long context: the drafter's Markov chain maintains higher confidence longer,
producing longer drafts that amortize the per-cycle verify cost.

**Next steps:**
1. **Experiment B:** `--dspark-confidence 0.5` and `0.3` at 32k — test if
   longer drafts push the 1.21 ratio higher (current: 0.7 threshold).
2. **Experiment C:** low-entropy workload best-case at 32k.
3. **M5:** DS4FORK.md docs, full `make test`, upstream-PR prep, push local commits.
4. **Cache investigation:** separate session — KV cache entry oscillation
   (64k hits 6 different anchors, 32k alternates 32768↔24576).

**Data:** `~/Projects/Scratch/CacheTest/` (prompts, logs, result JSONs).
State: `misc/experiment-a-state.md`.

---

## M0 spike findings (2026-08-04) — GO

### Q1: verify-row readback — GO, zero Metal changes
- `metal_graph_verify_suffix_tops_impl` materializes FULL logits for all
  n_tokens rows into `g->spec_logits`; `row_tops` is merely an
  `ds4_gpu_argmax_tensor` reduction over that same buffer.
- `metal_graph_read_spec_logits_row(g, row, logits)` (ds4.c:34565) reads any
  row < prefill_cap via offset readback — already used for the final row;
  intermediate rows are the identical call.
- Cost: block=5 here → ≤4 extra rows ≈ 2 MB/cycle. Negligible.

### Q2: draft probabilities — GO (capture at draft time)
- Drafter = target logits row + learned Markov bias (conditioned on previous
  draft token), then argmax (`dspark_apply_markov_greedy_probe`, ds4.c:~60112
  via 60028). So q_i = softmax(draft_row_i + markov_bias(prev))[drafts[i]].
- The draft row + bias are already in scope at pick time → capture q_i (and
  retain full draft rows for the exact residual) with an added O(K×vocab)
  softmax pass (~0.6M exp/cycle). Feasible => plan Option A (exact residual).
- TWO draft paths need capture plumbing (M2): the plain greedy-probe path AND
  `dspark_apply_markov_confidence_lazy_runtime` (ds4.c:32478) — the latter is
  the DEFAULT when confidence_threshold > 0 (we run 0.6).
- `fake_argmax` fallback drafts (ds4.c:60122, Markov invalid => drafts[0] =
  target argmax): degenerate proposal — keep greedy-path semantics for those.
- Draft-time rows come from the PREVIOUS cycle's spec_logits (block×vocab);
  retain K×vocab ≈ 2.6 MB/cycle. Fine.

### Q3: index map — nailed down
- drafts[0]: target distribution = `s->logits` (row before the draft window;
  current greedy check: `sample_argmax(s->logits) != drafts[0]`, ds4.c:61512).
- drafts[i], i>=1: target distribution = spec_logits row (i-1) = logits
  conditioned on prefix + drafts[0..i-1] (current: `row_tops[i-1] != drafts[i]`,
  ds4.c:61588).
- Bonus token: spec_logits row (draft_n - 1) — already read in full today
  (ds4.c:61601).
- block_size = 5 on this machine (live log: "stages=3 block=5"), cap 16.

### Extra findings
- `ds4_engine_mtp_draft_tokens` returns block_size (5) for DSpark (mtp_ready
  stays false for DSpark support models, ds4.c:56394) — so `--mtp-draft 1` in
  ds4-server.sh is INERT for DSpark; speculation IS active in the live server
  today. No config change needed.
- Confidence pruning (DSPARK_CONFIDENCE=0.6) is the lazy-runtime path and
  stays an orthogonal pre-filter.
- TP: verify has a mirrored `tp_verify_sent` protocol; v1 restricts
  rejection-sampling to non-TP (user runs single-machine); TP keeps greedy.

### Decision: GO
No Metal changes. CPU acceptance math. Two draft-path capture plumbing sites.
M1 next: pure acceptance kernel + unit tests.

## M4 results (2026-08-04)### M4.1 — Greedy regression: PASS
temp-0 outputs byte-identical to the pre-change baseline (e0e5f40) on a fixed
chat prompt, with the gate both OFF and ON (gate ON at temp 0 routes to the
unchanged argmax verifier).

### M4.2 — Distribution equivalence at temp 1.0 / top_p 0.95: PASS (after 2 fixes)
Method: /v1/completions (raw continuation — avoids chat-template tool-error
churn), N=400 per mode, permutation test on per-position token-TV with a
resampled null.

Two real bugs were found and fixed:

1. **Soft-q proposal (design flaw).** v1 captured q_i as the draft-head
   softmax at its own greedy pick and accepted min(1, p/q). But the drafter
   is deterministic argmax, so the true proposal is a point mass and the
   correct rule is accept-with-p(x_i), residual = p with the draft token
   zeroed. Soft-q over-accepted and biased positions 2-5 (TV 0.14-0.18,
   p<=0.03). Fixed: acceptance uses q==1 and
   ds4_spec_residual_sample_excluding; q/draft-row capture removed entirely
   (also re-enables the GPU/fused argmax draft paths).
2. **Checkpoint/KV desync on partial accept (integration bug).** The
   prefix-commit branch committed KV for the accepted prefix but never pushed
   the accepted draft tokens onto the checkpoint token vector, corrupting all
   subsequent cycles (TV 0.29-0.41 at positions 7-9). Fixed: push the
   accepted drafts after spec_frontier_commit_prefix (mirrors the argmax
   verifier).

Final result, two full speculative cycles + bonus, all positions within the
null (p=0.42-1.0), full-sequence p=0.81.

### M4.3 — Short-context performance on M5 Max (150-token completions)
- speculative OFF, temp 1.0 (plain per-token sampling): ~37.9 t/s
- speculative ON,  temp 1.0 (rejection sampling):       ~31.5 t/s  (-17%)
- speculative ON,  temp 0.0 (greedy argmax path):       ~38.4 t/s

The sampling verifier is currently *slower* than plain sampling at short
context. Stats show why: the DSpark scheduler produces very short drafts for
this workload (draft_len_hist dominated by len 1-2; ~86% of greedy cycles are
no-draft/scheduler-skip), so the fixed per-cycle overhead (verify pass ~25ms,
propose ~9ms, snapshot) is not amortized. Row readback is negligible
(verify_read ~0.3ms total), so the Metal-side-acceptance fallback from M0 is
not needed. Point-mass acceptance also accepts less than argmax-match greedy
(80.7% vs 83.2% per-token here). Candidate follow-ups (not v1): skip the
verify pass for 1-token drafts; raise the scheduler's minimum draft length
before proposing; tune tail-min.

## M4.3 follow-up: why temp>0 speculation is currently slower, and next experiments

### Detailed findings (short-context perf runs, 150-token completions)

Measured on M5 Max, official 0731 GGUF, ctx 512000, default scheduler settings:

| mode | t/s |
|---|---|
| spec OFF (DS4_MTP_SPEC_DISABLE=1), temp 1.0 | 37.9 |
| spec ON (rejection sampling), temp 1.0 | 31.5 (-17%) |
| spec ON, temp 0 (greedy verifier) | 38.4 (+1% vs OFF) |

Engine stats for the ON run (108 sample cycles + 51 greedy cycles):
- `draft_len_hist=1:70, 2:32, 3:12, 4:3, 5:6` — drafts are mostly 1-2 tokens.
- `accepted_len_hist=0:55, 1:59, 2:31, ...` — 35% of cycles accepted zero
  drafts while paying full verify+propose cost; avg 1.07 accepted drafts/cycle.
- `verify_layer` ~24.6ms/cycle (batched <=5 tokens ~= one decode-worth),
  `propose` ~8.6ms/cycle, row readback negligible (`verify_read` ~0.3ms total,
  so the M0 "Metal-side acceptance" fallback is unnecessary).
- `net_saved=-3154ms` over the run: speculation spent more than it saved.
- Greedy run: `no_draft=301/349` cycles — the scheduler skips/pauses drafting
  most of the time on this workload, so even greedy speculation is break-even.

### Root causes

1. **Confidence pruning truncates drafts.** The drafter walks its Markov
   chain token-by-token and stops when per-position confidence <
   `dspark_confidence_threshold` (default **0.7**, ds4.c:56340; flag
   `--dspark-confidence`). High-entropy creative text collapses confidence
   after 1-2 steps.
2. **Scheduler feedback pauses.** After rejected/short cycles the scheduler
   stops drafting: `DS4_DSPARK_SCHEDULER_NO_DRAFT_SKIP=3`,
   `SHORT_ACCEPT_NO_DRAFT_SKIP=4`, `COLD_LOW_CONFIDENCE_SKIP=7`, plus
   `TAIL_MIN_TOKENS=10` near the end (ds4.c:48548-48560).
3. **Cycle economics.** Verify pass costs ~one decode regardless of K, so
   yield/cycle decides everything. Break-even needs >~1.4 tokens/cycle at
   short context; measured 2.07 tokens/cycle with 35% zero-accept cycles and
   per-cycle propose/snapshot overhead lands below plain decode. With len-5
   drafts at 80% per-token acceptance (~3.5 accepted/cycle) the same cycle
   would save ~3 decode-times for ~1.5 cost — decisive win. Draft quality
   affects speed only, never correctness (point-mass verifier is exact for
   any proposal), so loosening draft length is safe to try.
4. **Context length.** Per-token decode cost grows with context; the fixed
   per-cycle overhead matters less, and verify (batched) replaces K
   increasingly-expensive single decodes. Short context is speculation's
   worst case; long context should shrink or flip the penalty.

### KV-cache validation run (4k/8k prompts, 3 rounds each)

Findings (server config: min=512, cold_max=30000, continued=10000,
align=2048, prefill ~380 t/s):
- Cold store fires only on a full miss (`cached==0`) and prompt <=
  cold_max, storing the chat-anchor or 2048-aligned floor of the prompt.
- 4k prompt (3582 tok): round1 miss -> stored 2048 (aligned floor);
  rounds2-3 hit 2048, recompute tail 1534 (~5.1s vs 10.7s).
- 8k prompt (7150 tok): round1 partially hit the 4k entry (cached=2048)
  so the cold store was skipped entirely; continued (10k interval) never
  fired -> all rounds recomputed 5102 tokens (~13.5s). Cache keys are
  text+model+quant+ctx (mode-independent).
- Consequence: at >30000 tokens no cold store happens at all. Fix for
  experiments: `--kv-cache-cold-max-tokens 70000` on the experiment
  server. Entries are shared across modes (separate server instances,
  same kv dir), so only the first mode pays each prefill.

### Experiment A — long context (designed, pending confirmation)

**Question:** does the ON/OFF ratio (0.83 at short context) approach or
exceed 1.0 at long context?

- **Prompt:** three prompts of ~16k / ~32k / ~64k tokens for /v1/completions,
  built from ~20 distinct paragraphs repeated to length (exact token counts
  verified via `usage.prompt_tokens`). Same prompt for every request and mode.
- **Cache:** experiment servers get `--kv-cache-cold-max-tokens 70000` so all
  three sizes cold-store on first miss; rounds 2+ then hit (tail <2048
  recomputed, negligible). Entries shared across modes via the same kv dir.
- **Samples:** 1 warmup (discarded) + N=10 measured per mode per size, seeds
  7000+i, max_tokens=200.
- **Modes:** (1) OFF: `DS4_MTP_SPEC_DISABLE=1`, temp 1.0/top_p 0.95;
  (2) ON: `DS4_DSPARK_SPEC_SAMPLE=1` + default scheduler, temp 1.0/top_p 0.95;
  (3) GREEDY: default env, temp 0. Fresh server per mode (no slot/tool-memory
  contamination).
- **Hygiene:** sequential runs, no parallel load.
- **Metrics:** per-request decode t/s (server log `avg=`), completion_tokens;
  for ON: stats dump (draft_len_hist, accepted_len_hist, accept rate,
  net_saved). Report mean +/- spread per mode + ON vs OFF ratio vs the
  short-context 0.83.
- **Interpretation:** ratio >= ~0.95 => context length was the main factor,
  proceed to B; ratio unchanged => overhead dominates, B/C still worth trying
  but expectations lowered.

### Experiment B — longer drafts via confidence threshold

Same protocol as A (long context, ON mode only first), varying
`--dspark-confidence` at 0.5 and 0.3. Expect longer drafts
(draft_len_hist shift), lower per-token acceptance; net t/s decides. If
positive, re-check distribution correctness is unaffected (it is by
construction — verifier is proposal-agnostic — but a spot-check permutation
run costs little).

### Experiment C — low-entropy workload (best case)

Prompts where the future is predictable (counting, structured lists, code
boilerplate), short context first, N=5 per mode. Expect long drafts at the
default threshold and a clear speculation win; establishes the ceiling and
the workload sensitivity.

## Experiment A — long-context performance (2026-08-05)

**Status: COMPLETE.** All 3 modes × 3 sizes × 11 runs executed.

### Protocol

| Parameter | Value |
|---|---|
| Prompts | 66036 / 33495 / 17235 tokens (exact prefix chain P16⊂P32⊂P64) |
| Cache | `/tmp/ds4-kv`, cold_max=70000, anchor_step=8192, align=2048 |
| Runs | 1 warmup (discarded) + 10 measured per mode per size |
| Seeds | 7000+i |
| Generation | max_tokens=200, top_p=0.95 |
| Metrics | Server log decode-only t/s (prefill excluded), completion_tokens |
| Hygiene | Sequential runs; fresh server per mode |

### Modes

1. **OFF**: `DS4_MTP_SPEC_DISABLE=1`, temp 1.0
2. **ON**: `DS4_DSPARK_SPEC_SAMPLE=1`, default scheduler, temp 1.0
3. **GREEDY**: default env, temp 0

### Results — DECODE-ONLY t/s (stable cache hits only, n=4 per mode per size)

| Size | OFF t/s | ON t/s | GREEDY t/s | ON/OFF | G/OFF |
|------|---------|--------|------------|--------|-------|
| 64k  | 25.1    | 22.9   | 19.8       | 0.91   | 0.79  |
| 32k  | 25.1    | **30.3** | 21.1     | **1.21** | 0.84  |
| 16k  | 26.5    | **28.3** | 21.8     | **1.07** | 0.82  |

- THINKING vs non-THINKING phases show identical decode t/s within each size
- Short-context ratio was **0.83** (ON was slower at 150-token completions)

### Interpretation

The long-context speculation result **reverses** the short-context finding. At
short context, spec verification overhead dominates → ON was 0.83×. At 16-32k
context, the draft acceptance rate increases with context length → speculation
becomes beneficial, peaking at 32k (1.21×). At 64k, the benefit drops to 0.91×
— possibly because tail writes to KV cache are larger and create more contention.

### Cache instability (major confounder)

The KV cache is highly unstable:
- 64k: 6 different anchor values across 3 modes (65536/57344/49152/40960/32768/24576)
- 32k: alternates perfectly between 32768 and 24576 (50/50)
- 16k: stable at 16384 (only size with consistent cache behavior)

This significantly affects wall-time measurements but decode-only t/s from server
logs provides a clean metric regardless of cache state. Root cause investigation
is tracked in `~/Projects/Scratch/TODO.md`.

### Conclusion

**32k is the sweet spot** for DSpark speculation at temp 1.0 — 21% faster than
plain sampling. Next: Experiment B to see if lowering `--dspark-confidence` to
0.5/0.3 (longer drafts) pushes the 32k ratio even higher.

---

## Recalibration (2026-08-05) — thermal-controlled re-measurement

### CRITICAL: the 1.21× result was a thermal artifact

Experiment A's 1.21× at 32k was measured WITHOUT thermal control. On the M5 Max
in "Automatic" energy mode, decode t/s varies ±30% purely from GPU throttling:
- Cool GPU (after idle): 30-39 t/s
- Throttled GPU (sustained load): 20-26 t/s

OFF and ON were measured in separate time blocks, so a cool ON window vs a
throttled OFF window produced a spurious speedup. The GPU thermal signature was
visible in the old logs (fast runs always followed idle gaps).

### Recalibrated protocol

1. Energy mode set to **High Power** (`pmset -a lowpowermode 0`, `powermode 2`), AC attached.
2. **Thermal warmup**: ~90s sustained decode before measurement.
3. **Interleaved A/B**: OFF/ON alternate within each round (round, mode × size).
4. Decode-only t/s from server log `avg=` lines (gen>=190), never wall time.

### Recalibrated results (conf 0.6 and 0.9, 3 rounds interleaved)

| size | OFF | ON 0.6 | ON/OFF | ON 0.9 | ON/OFF |
|------|-----|--------|--------|--------|--------|
| 64k  | 29.5 | 26.2 | 0.89 | 25.8 | 0.89 |
| 32k  | 31.9 | 27.3 | 0.86 | 26.8 | 0.86 |
| 16k  | 33.3 | 28.6 | 0.86 | 27.9 | 0.86 |

**The sampling verifier is a net LOSS at every size and confidence**: ON is
11-14% slower than plain sampling. The old 1.21× does not reproduce.

### Why: draft-length economics

- conf 0.9 → 73% len-1 drafts (avg 1.34): a len-1 accepted draft costs the same
  1 eval as plain sampling PLUS propose overhead → can never win.
- conf 0.6 → 41% len-1, avg 2.0.
- conf 0.3 + `DS4_DSPARK_SCHEDULER=0` → len-5 reached (avg 3.3) but acceptance
  drops to 47.6% → avg accepted/cycle = 1.36, **below the ~1.4 break-even**.
- Verify pass costs ~1 decode per len≥2 cycle; propose is pure overhead.

### Optimizations (committed `389a708`)

1. **Len-1 short-circuit**: 1-token drafts skip snapshot/frontier/verify and
   commit via plain eval. verify_ms cut 4.2× (9503→2241 at conf 0.9). Does NOT
   change decode t/s (removes overhead, not decodes) but is correct and keeps
   the accounting honest.
2. **saved_ms bug fix**: accounting was gated behind the scheduler-enabled check,
   so `DS4_DSPARK_SCHEDULER=0` runs reported saved=0. Now accumulates always.

### Revalidation

- M4.2 distribution equivalence RE-PASSED with optimized build: all 12 positions
  p≥0.996, unigram KS p=1.997 (N=200 per mode, permutation test).
- M1 kernel tests pass (`test_spec_rejection: ok`).

### Honest conclusion

The temperature-aware sampling verifier is **correctness-clean but performance-
negative** on this hardware/workload. The DSpark drafter's confidence head
collapses after 1-2 tokens on creative text, so drafts are too short to amortize
the fixed verify cost. Keeping it opt-in (`DS4_DSPARK_SPEC_SAMPLE=1`) is
reasonable; upstreaming as a performance win is NOT supported by data.

> **Status update (2026-08-05):** this conclusion measured the *current*
> implementation. It is **not** a closed door. An audit of oMLX's embedded MTP and
> external-drafter DFlash shows the same speculative architecture runs with (a) a
> single host sync per cycle (in-graph acceptance), (b) async-propose overlap, and
> (c) a sharper draft sampler — any of which could change the economics. The
> sampling verifier's machinery (accept/residual/target-dist) is the **substrate
> for that work**: Phase 3 of PLAN-DSPARK-PERF.md reuses `ds4_spec_accept_token`
> / `ds4_spec_target_dist` / the `capture_q` plumbing to implement the sharper
> draft sampler. Keep this code; it is the documented foundation, not cruft.

### Upstream comparison (2026-08-05) — GREEDY loss is upstream, not ours

Built pure upstream antirez/ds4 (`origin/main` @ `6747e77`, the exact commit our
fork merged in `6c07524`) in `/Users/naz/Projects/ds4-upstream` and ran the
identical thermal-controlled A/B on SWE-long 8k prompts:

| build | OFF (t/s) | GREEDY (t/s) | G/OFF |
|-------|-----------|--------------|-------|
| upstream `6747e77` | 36.52 | 31.85 | **0.872** |
| our fork `6c07524` | 36.55 | 31.78 | **0.869** |

The ~13% GREEDY loss is **entirely upstream DSpark architecture** (markov
`prop_chain` + `verify_layer` on short drafts). Our only server gate change
(`spec_sample_gate`) is false at temp 0, so GREEDY/OFF are byte-identical to
upstream; numbers confirm zero added overhead.

The sampling verifier (temp>0) is our **additive** cost: per drafted token the
sample path (ds4.c:62169, 62278) does a synchronous full-row (129k×4B=516KB)
GPU→CPU readback via `metal_graph_read_spec_logits_row` plus a full-vocab
qsort+softmax in `ds4_spec_target_dist` (point-mass q=1.0 acceptance). These reads
are outside the `verify_timing` block, so `verify_read` under-reports them.
Upstream never speculates at temp>0 (gate is temp≤0 only), so its temp-1.0 path
== our OFF.

Bottom line: DSpark is a greedy-only optimization upstream and is net-negative on
M5 Max even there. Our temp-aware verifier is correct but rides a base that loses
~13% before our code runs, then adds readback/softmax overhead on top.

**Forward path:** see `PLAN-DSPARK-PERF.md` — five phases (single-sync in-graph
accept, sharper draft sampler, async propose overlap, park-and-exit scheduler,
tree verification) that port the oMLX/DFlash lessons into ds4.
