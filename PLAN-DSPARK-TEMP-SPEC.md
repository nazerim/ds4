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
   indistinguishable output distribution vs non-speculative sampling.
2. temperature<=0 behavior byte-identical to before.
3. Measurable decode speedup at temp 1.0 (target >=1.3x); acceptance
   stats visible in dspark_stats.
4. `make test` green; equivalence suite added to tests/ds4_test.c.

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
- `tests/ds4_test.c` — kernel unit tests + equivalence suite
- `DS4FORK.md` — documentation
- (hopefully zero Metal changes — confirm in M0)

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

## M4 results (2026-08-04)

### M4.1 — Greedy regression: PASS
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

### M4.3 — Performance on M5 Max (150-token completions)
- speculative OFF, temp 1.0 (plain per-token sampling): ~37.9 t/s
- speculative ON,  temp 1.0 (rejection sampling):       ~31.5 t/s  (-17%)
- speculative ON,  temp 0.0 (greedy argmax path):       ~38.4 t/s

The sampling verifier is currently *slower* than plain sampling at temp>0.
Stats show why: the DSpark scheduler already produces very short drafts for
this workload (draft_len_hist dominated by len 1-2; ~86% of greedy cycles are
no-draft/scheduler-skip), so the fixed per-cycle overhead (verify pass ~25ms,
propose ~9ms, snapshot) is not amortized. Row readback is negligible
(verify_read ~0.3ms total), so the Metal-side-acceptance fallback from M0 is
not needed. Point-mass acceptance also accepts less than argmax-match greedy
(80.7% vs 83.2% per-token here). Candidate follow-ups (not v1): skip the
verify pass for 1-token drafts; raise the scheduler's minimum draft length
before proposing; tune tail-min.
