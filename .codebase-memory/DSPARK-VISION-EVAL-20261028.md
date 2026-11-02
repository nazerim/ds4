# DSPARK-VISION-EVAL — 2026-10-28: official Vision-Exp DSpark drafter, ingest + acceptance + speed

Purpose: step 0 of the DFlash2-vs-retrain-DSpark decision tree (see
SPLASH-INTEL-20261023.md, PLAN-DSPARK-PERF.md). Question: does the official
checkpoint-matched DSpark support model for DeepSeek-V4-Flash-Vision-Exp
clear break-even on our M5 Max, and where?

Operator context: DeepSeek V4 Flash (Vision-Exp) back in daily use; coding
agent runs cloud-side so the MBP engine was free for A/B testing. Engine was
STOPPED for the whole eval (process lock); restore step at the end.

## 1. Ingest: CLEAN, no FP8 blocker

- Pulled `gguf/DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf` via
  `./download_model.sh ds4f-vision-dspark` (5,989,114,528 B; repo
  antirez/deepseek-v4-gguf).
- Schema (tools/gguf_meta.py, new): v3, 81 tensors, 12 KV. dtypes F32×34,
  Q8_0×31, F16×7, Q5_K×6, Q4_K×3 — all core ds4 types. The "FP8 code/scale
  pending" note in download_model.sh belongs to **glm53-fp8**, not this file.
- Anatomy matches `dspark_weights_bind_optional()` exactly: 3× `mtp.<stage>`
  blocks (MLA-ish attn + q-sinks + routed/shared experts + HC attn/ffn),
  `mtp.main_proj/main_norm` (stage 0), `mtp.norm`, `mtp.hc_head_{base,fn,scale}`,
  `mtp.markov_head.markov_w1/w2`, `mtp.confidence_head.proj` (final stage).
- Metadata gates PASS: `general.architecture=deepseek4-dspark`,
  block_size=5, markov_rank=256, noise_token_id=128799,
  target_layer_ids=[40,41,42], stage_count=3;
  `deepseek4.checkpoint_variant=vision-exp` and
  `general.source.revision=e46e16bf6035c6f317eb2ac7458eb0362926d402` match the
  pin in `support_model_checkpoint_compatible()` AND our target GGUF's own
  revision — so the drafter is bound to this exact checkpoint (as expected:
  drafters are checkpoint-specific).
- Load run: `stages=3 block=5 ... missing=0 invalid=0 metadata_errors=0`;
  `DSpark target-hidden capture enabled: layers=40,41,42`.

## 2. Correctness semantics

Greedy DSpark output is **FP-equivalent, not byte-identical** to plain decode
(`direct verifier-state commits` batch the verify; float op order can flip
argmax ties, after which continuations diverge). Verifier `errors=0` in every
case measured. `make dspark-acceptance` default `REQUIRE_IDENTICAL=0` encodes
this; do NOT set it to 1 for this build (first battery attempt "failed" on
that gate at a 1-token tie-flip after ~50 identical tokens).

## 3. Speed A/B (temp 0, seed 12345, Metal, confidence = engine default 0.6
on Apple == the script's DSPARK_CONFIDENCE pin; scheduler default ON, window=4)

Fixture battery (`make dspark-acceptance`, tokens=256):

| case | base t/s | dspark t/s | delta | note |
|---|---|---|---|---|
| hello | 44.07 | 52.30 | **+18.7%** | accept 88.9%, avg_accept 1.60 |
| redis | 43.92 | 42.53 | -3.2% | scheduler skipped 19/23 cycles |
| math | 43.78 | 41.69 | -4.9% | proposed=0 (8-cycle probe rejected) |
| python_reverse | 44.58 | 45.64 | +2.4% | 93 cycles, mostly skipped after probe |
| c_add | n/a | n/a | — | quality guard: accepted_draft=3 < 8 → fixture exits 2 |

Traffic probe (tests/dspark_traffic_probe.sh, new; realistic opencode-like
prompts, tokens=512):

| case | base t/s | dspark t/s | delta | accept_rate | avg_accept | 5-len accepts |
|---|---|---|---|---|---|---|
| pyrefactor | 45.31 | 52.29 | **+15.4%** | 87.6% | 0.87 | 30 |
| bashops | 44.10 | 48.47 | **+9.9%** | 88.5% | 0.91 | 19 |
| cstruct | 44.53 | 44.05 | -1.1% | 79.4% | 0.26 | 3 |
| docrewrite | 44.70 | 44.95 | +0.6% | 81.8% | 0.24 | 1 |
| mathq (nothink) | 44.91 | 55.05 | **+22.6%** | 89.0% | 1.22 | 19 |
| mathq (think) | 45.65 | 47.57 | +4.2% | 89.9% | 0.91 | 18 |
| toolcall | 44.60 | 37.81 | -15.2%* | 37.5% | 0.20 | 0 |

\* toolcall ran only 15 cycles (short DSML-ish output) — sample too small;
retest in flight (see §4). cstruct/c_add = C-syntax continuation is the
drafter's consistent weak spot (low proposal rate + near-zero acceptance),
while Python/bash/math narrative is its strong suit.

vs the August 0731 audit (PLAN-DSPARK-TEMP-SPEC §upstream comparison):
GREEDY/OFF = 0.87 across the board. The Vision-Exp drafter is categorically
better on our traffic: positive double digits on 4/7 cases, weak cases
protected to ≈-1..-5% by the adaptive scheduler.

## 4. Follow-up results (complete)

- **toolcall @1024** (was the -15% micro-sample): 45.35 → 44.10 = **-2.7%**.
  accept_rate 68.2%, avg_accept 0.19, scheduler bypassed 269/307 cycles —
  DSML/tool-call emission is genuinely disfavored by the drafter, damage
  bounded by the scheduler. Outputs differ (FP ties).
- **long-ctx A/B, ~51k tokens cold (--ctx 98304, 256 decode)**:
  prefill 573.96 (base) vs 567.48 (dspark) t/s — unaffected.
  decode **35.07 → 36.21 = +3.3%**. accept 85.7% but avg_accept 0.36,
  scheduler_skips 144/183 — win persists at production ctx but compresses
  hard vs short-ctx (+15..22%). (Note: first attempt failed only because the
  follow-up script dropped `--mtp-model` — fixed in-script.)
- **Engine-health bonus (mainline speed question)**: single-session CLI
  reference at this binary = 44-45 t/s short-ctx, ~35 t/s @51k, cold prefill
  570-574 t/s @51k (675-735 band at 2048 per DS4FORK.md:1203). Matches the
  Oct-23 smoke (44.6) → **no regression from the blade KV move**; the
  "slower than remembered" feeling is (a) real traffic living in the
  128-384k band (21-27 t/s there per live server logs, bandwidth-decay curve)
  and (b) multi-session interleaving pulling chunk averages down.

## 5. Verdict + production recipe

**Step-0 answer: the official Vision-Exp drafter CLEARS break-even on code/
prose/math traffic (+10..23% short-mid ctx, +3.3% at 51k) and is protected to
≈-1..-5% elsewhere by the default-on adaptive scheduler.** Categorically
better than the 0731 drafter audited in August (0.87x across the board). The
markov-head-collapse ceiling is NOT reconfirmed for this checkpoint-matched
head: it sustains full-block (5-token) accepts regularly (pyrefactor: 30,
mathq: 19 of 269/166 cycles).

NOT enabled in production autonomously, because it is an operator-policy call
on two axes:
1. greedy determinism changes (FP-tie divergence from existing decode —
   stored KV anchors stay valid, but new outputs won't match old plain-decode
   bit-for-bit), and
2. the dominant agentic pattern (tool-call/DSML) is the drafter's weakest
   case (-2.7%).

Enable (one command, script key-map registered 2026-10-28):
  `./ds4-server.sh restart-vision-dspark`
  (DSPARK_KEYS now includes "vision" -> the support GGUF; guard logic passes
   because spec_used != the 0731 default.)
Knobs: DSPARK_CONFIDENCE (engine default 0.6 on Metal == script pin),
DS4_DSPARK_SCHEDULER (on), DS4_DSPARK_SCHEDULER_WINDOW, DS4_DSPARK_STATS=1.
Revert: `./ds4-server.sh restart`.

Decision-tree carry-forward: DSpark-format retraining (our-distribution data
on C-continuations + DSML/tool-call tokens) or DFlash2-style lattice drafting
both remain open for squeezing the agentic gap; capture-pipeline pkg (plan §1)
unshared-cost note stands. But step 0's thesis — "measure the official head
before training anything" — is now resolved with data.

Fixture caveat for future runs: `dspark-acceptance` exits 2 on the `c_add`
quality guard (accepted_draft=3 < 8) — that floor was tuned to 0731-era
drafters on a 24-cycle micro prompt; not a defect signal for this drafter.

## 6. temp=1.0 battery (tests/dspark_temp_battery.sh) — THE PRODUCTION-RELEVANT RUN

Everything in §3-4 was greedy (temp=0). Real agent traffic samples at temp>0,
which routes through a DIFFERENT DSpark path (ds4.c:88503-88530). Ran 6 prompts
x {baseline, dspark-opportunistic, dspark-exact} @ temp 1.0, top-p 0.95,
seed 12345, 512 tokens (log/dspark-temp1.0-*.tsv):

| case | base | opp Δ | exact Δ |
|---|---|---|---|
| pyrefactor | 43.84 | **+18.5%** | +6.6% |
| mathq | 43.46 | **+16.1%** | +2.3% |
| bashops | 44.13 | +6.5% | -3.5% |
| toolcall | 42.20 | +1.4% | -0.5% |
| docrewrite | 44.13 | -1.9% | -5.2% |
| cstruct | 43.45 | -3.4% | -1.8% |
| **MEAN** | | **+6.2%** | **-0.4%** |

Two mechanisms, and the key finding:
- **Opportunistic (default, DSPARK_EXACT=0)**: `sample_cycles=0` on every run —
  at temp>0 it samples only the FIRST token, then commits draft tokens
  GREEDY-ARGMAX-MATCH style (`ds4_session_eval_speculative_argmax`, comment at
  ds4.c:88508 "commit the following DFlash tokens while they match the target's
  greedy path"). Fast → +16-18% on code/math, MEAN +6.2%. BUT this is
  distribution-BIASED toward greedy at temp>0 — NOT lossless. Lossless only at
  temp=0.
- **Exact (DSPARK_EXACT=1)**: the distribution-faithful p/q rejection path;
  confidence auto-lifts to 0.8 at temp>0 (ds4.c:79608) so it proposes fewer/
  higher-confidence drafts (accept reads 97-100%). MEAN -0.4% — SLOWER than
  baseline on 4/6. Confirms the Aug PLAN-DSPARK-TEMP-SPEC verdict ("temp-aware
  verifier correctness-clean but performance-negative") STILL holds on this
  stronger drafter: the readback+softmax fidelity tax eats the whole speculation
  win.

Net: the temp=1 win you'd actually ship (opportunistic +6.2%) costs sampling
diversity — at temp=1 it makes output greedy-leaning. Only trust it at temp=0
(genuine lossless, and the strongest wins there anyway: +15-22%).

## 7. FINAL verdict

DSpark on Vision-Exp is a clear, SAFE win at temp=0 (greedy) for code/math/prose
(+10-22% short-mid ctx, +3.3% @51k), protected elsewhere by the scheduler. At
temp>0 the fast mode stays positive (+6.2% mean) but is greedy-biased (not
lossless); the lossless mode (exact) is net-negative. So:

- temp=0 workloads: enable freely — one command, `restart-vision-dspark`.
- temp>0 agentic traffic: enabling ships a subtle greedy-bias in sampled output.
  That's a quality call for the operator, not an autonomous flip. Engine left as
  found (plain Vision-Exp, blade KV) at end of eval; verified serving, errors=0.
  **[SUPERSEDED by §8 — server-scale paired test: 46/48 outputs byte-identical
  at temp 1.0; the greedy-bias concern did NOT reproduce. Final verdict: KEEP.]**
- This closes the step-0 question from SPLASH-INTEL-20261023.md. DSpark-format
  RETRAINING (better markov/confidence head on our code+DSML data) remains the
  highest-value next lever; DFlash2-lattice is the stretch goal. Capture-
  pipeline (plan §1) is the shared first cost for both.

## 8. PHASE 2 RESULT — temp=1.0 fidelity battery (2026-10-28, server-scale)

6 prompts x 8 samples, temp 1.0 top-p 0.95, seeds 4200+i, max 512 tokens,
armB (dspark conf0.6) fired against live server PID 59290, armA (plain) after
restart on 61659. Scripts: phase2_battery.py / phase2_metrics.py (scratch).

**Headline: zero fidelity cost detected at server scale.**
- Cross-arm byte-identity: **46/48 samples identical** (reasoning+content+tool_calls).
  The 2 divergences (agentic1 s1/s5) start at token ~177/~390 of 512 — late.
- Within-arm diversity metrics all equal across arms: unique-rate 1.000/1.000,
  pairwise first-divergence 4/4 toks, self-BLEU-proxy .703/.703, distinct-2
  .483/.483, tool-call rate .042/.042.
- tg median: plain 36.58 vs dspark 37.17 (+1.6% — battery is thinking-prose
  heavy = the drafter-bypass band; DSML boost 45v34 doesn't apply here).

Interpretation: the CLI-era "opportunistic = greedy-biased at temp>0" concern
does NOT reproduce on production-shaped output — this model's temp-1 sampling
distribution is peaked enough that greedy-match-accepted drafts coincide with
the sampler's draws. Divergence risk scales with output length/off-argmax
draws (the 2 late divergences were the longest agentic completions).

**Pre-registered rule outcome: KEEP dspark for temp>0 traffic** — diversity
collapse: none measured; validity/finish-mix: identical; tg: neutral-positive.
Caveat kept honest: 48 samples / 512-tok cap is a bounded probe; re-check if
future traffic runs 4k+ sampled turns.

## 9. Phase 2.5 (in flight): 128k/256k head+continuation, REAL opencode session
Extractor: phase25_extract.py <- opencode.db ses_043eb69e (3930 msgs).
req_head ~129k toks (419 msgs), req_full ~256k (878 msgs), prefix-identical.
Plain arm on 61659; dspark arm to follow on restart. Measures: cold 128k
prefill t/s, cached_tokens reuse on the +128k continuation, decode t/s at
128k/256k, anchor-ladder behavior.

## 9. Phase 2.5 RESULT — 160k/323k REAL opencode session, plain vs dspark

Prompts: opencode.db ses_043eb69e reconstructed; req_head=160,142 tok,
req_full=323,172 tok, prefix-identical (head is exact prefix of full).
Both arms: temp 0, 256-token tails. Engine PIDs: plain 61659, dspark 62004
(conf 0.6). Files: phase25/result_{plain,dspark}.json.

| metric | plain | dspark | delta |
|---|---|---|---|
| head cold prefill+gen wall | 397.0 s | 418.4 s | +5.4% — attributed to cold-engine-window first request (dspark head started ~1 min after restart: Metal residency/warmup); full-run walls differ by only +2.7% |
| full prompt reuse | cached **147,456** (9x16384 grid rung) -> 175,716 re-prefill | IDENTICAL cached/rerun | drafter is transparent to anchor ladder |
| decode 100-165k | med 25.0 (n=6) | med 24.5 (n=6) | -2%, noise band |
| decode 165-323k | med 18.4 (n=6) | med 18.4 (n=6, spread 16.5-18.9) | **0% — NEUTRAL** |

Verdict (final piece of the enable-line picture):
- DSpark at deep production context (165-323k) is SPEED-NEUTRAL: no regression
  (safety: can leave enabled fleet-wide), but no gain either — the scheduler
  declines to propose where verify cost vs acceptance doesn't pay.
- Combined with §6/§8: +10-22% under ~64k (greedy) / ~+6% sampled, DSML
  segments +27-33%, neutral >=~128k, neutral fidelity at temp 1.0 (46/48
  byte-identical). KEEP dspark as the standing config; expectation is:
  big win on short/tool-call phases, invisible at long context.
- Head-level reuse finding (IMPORTANT, corrected): the dspark head did NOT
  just run "conventionally cold" — **`model_fp` differs between plain
  (5096181743673580336) and dspark (11038508403456853112) windows**: the KV
  disk-cache fingerprint includes the speculation toggle. Disk anchors saved
  by one mode are invisible to the other, so EVERY plain<->dspark switch
  forces a one-time cold ladder rebuild per conversation, and each mode
  accumulates its own parallel ladder set (kv dir 328->403 GiB during this
  eval's toggling). Within a mode, head->full reuse is perfect (dspark full
  hit its own rebuilt 147,456 anchor, chain=1 load=471ms).
  Operational rule: pick ONE mode as standing config and stay in it; flip day
  is a rebuild tax day. Retired-mode stores age out via the budget eviction.

## 10. PHASE 3 BOUNDING PASS — done 2026-10-29, ADOPTED conf 0.45 + window 12

Server-mode arm comparison, 6 traffic probes x {temp 0, temp 1.0}, 512 toks,
same seeds, back-to-back arms (runner: tests/dspark_bounding_pass.py):

| band | conf0.6/win4 | conf0.45/win12 | delta |
|---|---|---|---|
| temp 0 | 41.65 e2e | 42.17 | +1.2% (4/6 probes) |
| temp 1.0 | 39.32 | 40.75 | **+3.6% (6/6 probes)** |

Key findings:
1. **model_fp is INVARIANT to confidence/window** (50961817… plain /
   11038508… any-dspark): tuning these knobs costs ZERO ladder rebuilds.
   Only the dspark on/off toggle flips fp. Operational: tune freely, toggle
   rarely.
2. temp>0 gains most (mechanism-consistent: 0.45 admits mid-entropy sampled
   patches; window 12 cuts probe flapping tax).
3. No probe regressed >0.2%; fidelity mechanics unchanged (verifier-gated).
   Optional re-check: §8 byte-identity spot-check under 0.45 next quiet day.

Pinned in ds4-server.sh (defaults now conf 0.45, sched window 12 exported on
the dspark launch path). Engine PID 69951 running the adopted config; proxy
live. Tuning chapter CLOSED — remaining headroom is head-quality (capture
pipeline: PLAN-CAPTURE-PIPELINE-20261029.md stage 0-1 next) and deep-ctx
architecture (neutral >=128k by design), not knobs.

## Files touched this eval
- gguf/DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf (new, downloaded)
- ds4-server.sh (DSPARK_KEYS: + "vision" entry -> enables start/restart-vision-dspark;
  plus the earlier KV-blade retarget + mount guard)
- tools/gguf_meta.py (new: GGUF v3 metadata/tensor dumper)
- tests/dspark_traffic_probe.sh (new)
- tests/dspark_followups.sh (new)
- tests/dspark_temp_battery.sh (new: temp>0 opportunistic-vs-exact battery)
- tests/dspark_bounding_pass.py (new: Phase-3 server-mode arm runner)
- .codebase-memory/PLAN-CAPTURE-PIPELINE-20261029.md (new: retraining plan)
- ds4-server.sh: conf 0.45 + sched-window 12 pinned (Phase-3 adopted)
- log/dspark-acceptance-vision{,2}.log, log/dspark-traffic-*.tsv,
  log/dspark-followups-*.log, log/download-vision-dspark.log
