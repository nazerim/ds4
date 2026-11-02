# NIGHT-RUN-3-RESULTS (2026-11-01 01:15 -> ~04:00) — h-conditioned draft surrogate: the taps are loaded

Read with NIGHT-RUN-2-RESULTS.md (markov/confidence v0 results) and
PLAN-TRAINER-20261101.md (architecture groundwork). Engine untouched all
night; PID 2559 armed, heartbeat soak normal. Resource constraint answered
at start: NO engine, NO window, ~10 GB slab on 128 GB RAM.

## The question
Do the captured hidden states (3 taps x 4096 at h[pos-1], fp8) carry
next-token signal BEYOND bigram statistics — i.e., is the mirror's input
worth a full transformer training effort? v1 surrogate answers with a
linear-model ablation: z = (w1[seed] + x@A) @ w2.T * exp(log_s)
(A zero-init => model starts exactly at the v0 markov; delta attributable).

## Results table (frozen day-3 holdout, n=7,492 pairs)
| model | top1 | top3 | boundary top1 |
|---|---|---|---|
| official markov (no training) | .120 | .182 | .474 |
| v0 bigram FT (night #2) | .179 | .280 | .519 |
| v1 joint, unscaled init | .312 | .414 | .813 |
| v1 two-phase attribution, unscaled | .296 | .398 | .761 |
| v1 + learnable logit scale (wd .01) | .348 | .486 | .813 |
| v1 + scale, low wd (.0005) | .356 | .484 | .828 |
| **v1 + scale, lr 1e-4 (best 6ep)** | **.3675** | **.5019** | .806 |
| v1 12ep lr 1e-4 (long12) | .3932 peak (ep10) / .388 final | .527 | .836 |
| v1 16ep lr 3e-5 (deep16) | .3955 at ep15 — unsaturated | .531 | .828 |
| **v1 32ep lr 3e-5 + cosine (cos32)** | **.4014** (plateau, peak .4028 @27) | **.5334** | .828 |

**FINAL HEADLINE: next-token top1 .120 -> .4014 = 3.35x official markov,
2.24x tuned-bigram v0, from a LINEAR read of the taps. delta_top1_from_h
= +.273 (markov branch .129 alone after co-training). Confidence AUCs
.735/.785 — stable through 32 epochs (separate trunk held). Boundary class
.828.**

## VERDICT: JOINT PLATEAU REACHED at 195k pairs — next variable is DATA
The 32ep cosine run converged cleanly (last 10 epochs within ±.2pp): the
SCHEDULE is exhausted at this corpus size. Two readings of the ~.401
plateau, and they're distinguishable by experiment:
- DATA-bound: more pairs -> higher plateau -> scaling probe at 2x corpus
  settles it (soak will supply ~400-500k within days; or 2-day banking).
- MODEL-CLASS-bound: a linear ctx cannot read the taps further — the
  transformer mirror is where the remaining signal lives (strictly more
  expressive; this .401 is its guaranteed floor, not its ceiling).
Either way the mirror/parity path (engine window) is the critical-path
next step, and the soak continues unconditionally.

## Findings
1. **Tap information content PROVEN offline.** A rank-256 linear read of
   h[pos-1] nearly doubles what the full trained markov branch achieves by
   itself. The 3-stage transformer body has strictly MORE to work with than
   this probe sees — the mirror build is justified by data, not hope.
2. **Init math mattered more than hyperparameters**: unscaled official
   markov logits (CE ~23-24 at step 0) suppressed the first two runs by
   ~.05-.06 top1 vs the learnable-scale (init exp(log .2) -> CE ~7-10)
   versions. Documented; use scale-init in all future surrogate work.
3. **Attribution**: with markov frozen (phase-1) the h-pathway alone hits
   .296-.356; no_h eval on co-adapted weights drops to ~.13 — joint
   training sacrifices the pure-bigram branch for total strength. Clean
   h-contribution ≈ +.21 to +.24 top1.
4. **Boundary class**: with_h boundary top1 .81 (n=134, CI ~.73-.88) — h
   nearly free-predicts tool-call template continuations. Consistent with
   the corpus finding (Vision-Exp head already full-blocks at boundaries);
   retraining focus stays on plain content and confidence calibration.
5. **Optimization-bound vs data-bound — RESOLVED**: cos32 plateaued at
   .401 ± .002 (last 10 epochs) — schedule exhausted; the ~.40 plateau is
   either the DATA ceiling at 195k pairs or the LINEAR-CLASS ceiling.
   Discriminate by retraining cos32 protocol on 2x corpus when banked.

## Artifacts
- trainer/TRAIN_V1_{scale,lowwd,lowlr}.json (+ _long12/_deep16 pending;
  holdout curves inside every file)
- trainer/v1_head*.pt (weights incl. learned log_s)
- trainer/TRAIN_V1_attr.json (attribution run, pre-scale-fix, restored
  from its log after a smoke clobber — smoke now writes _smoke only)
- scratch logs: train_v1_{full,attr,sweep,long}.log
- reproduce (cos32 exactly): cd trainer && uv run python -m dspark.train_v1
  --phase1 4 --epochs 32 --lr 3e-5 --cosine --out-tag X
  (~50 min on MPS; artifacts now embed full cli[] incl. --cosine/--trunk)

## Independent review of the PRODUCER (train_v1, ses_e6d2a1c8 continued)
Verdict: **.4014 and delta .273 CAN be TRUSTED for the mirror go/no-go —
no re-run required.** Highlights:
- Headline INDEPENDENTLY REPRODUCED by reviewer re-evaluating
  v1_head_cos32.pt on frozen day-3 to 4 decimals with own pipeline.
- phase1-frozen logic proven consistent (AdamW lr=0 = bit-exact freeze incl
  decay); saved weights corroborate (|dw1|/|w1| = 0.7%).
- Boundary batch-masking indexing: exact (replicated on non-divisible test).
- Data: train/holdout file-disjoint confirmed; v1.2-era wrong-lineage labels
  EMPIRICALLY impossible in kept records (chain=100% all days; day-2/3/4
  inbreaks=0; day-1 suspect windows are n_label==0 -> filtered).
- v0 reference RE-MEASURED on day-3-only (.1789/.2799) -> multipliers clean.
- Fixes applied post-review: full CLI logged in protocol (incl --cosine,
  --trunk), auc None no longer written as bogus 0.0, weights saved CPU-
  detached, misleading smoke leftover quarantined (TRAIN_V1_smokeleftover_
  QUARANTINE.json — do not quote), "~10GB" claim corrected to peak ~2x
  (still fine: 2x corpus ~45GB peak vs 128GB; preallocate slab if ~1M pairs).
- Known selection optimism (6 configs on same holdout): ~+.5-1pp — hence
  CONFIRMATION IN FLIGHT: swapval (train d1+d3+d4, holdout d2 = 82k pairs,
  SE ~.4pp) + mlp32 (nonlinear trunk ceiling probe). Results appended below
  when done.

## What this changes for Stage 3 (decision inputs)
- Fine-tune targets confirmed feasible from captured data alone: markov
  (done), confidence BCE (AUC floor established), and NOW the hidden-state
  pathway — the next capability step is the full mirror forward (needs the
  parity dump = engine window) to convert surrogate-signal into real
  draft-acceptance gains.
- Ship-gate translation: offline delta of this magnitude in a LINEAR model
  suggests the acceptance gap between retrained and official heads is
  addressable — but p1/p3 truth remains the engine batteries (window).
- Data: keep soaking; knee not yet visible (v1 curves not peaked, bigram
  curve still rising at 195k). Next scaling probe at ~400-500k pairs.

## Hygiene / safety
- ds4.c and the running engine: UNTOUCHED (every job CPU/MPS-side; blade
  reads only). Collection unaffected; validate before any banking.
- Nothing committed (repo rule); all new files under trainer/ (untracked)
  + docs.


## CONFIRM RUNS + CONTROLLED BOUNDING (03:00-04:15, window granted)

### swapval (holdout day-2, 82k pairs) -> .5923 top1 / delta_from_h .4396
### mlp32 (nonlinear trunk, original split) -> .3572 — WORSE than linear .4014
Resolutions:
- swapval's huge number is SPLIT DIFFICULTY, not progress: day-2 is a
  9%-unique-bigram corpus (termbench template saturation) vs day-3's 51%.
  Cross-day content overlap is real (day2∩day4 bigrams = 29% of day-2) but
  it IS the deployment distribution. RULE: never compare across splits;
  plateau claims are same-split only. cos32's .4014 stands.
- mlp32 underperforming linear (.357 < .401) at equal protocol => model
  class is NOT the binding constraint for the surrogate => the .40 plateau
  is DATA (or intrinsic entropy) bound. Next data point: rerun cos32-lin at
  ~2x corpus when day-6/7 bank. (Mirror still justified — it does strictly
  more than this surrogate; but expectations for the SURROGATE line are
  data-limited.)

### Controlled bounding re-verify (dspark ON vs OFF, tools payload, prod config)
tests/dspark_bounding_pass.py now carries tools[]. New toolsplain arm on
dspark-off server vs toolsprod on prod — 12 cells:
  MEAN e2e +6.4% | decode-median +6.7% | t0 +6.5% / t1 +6.3%
  engaged share by cell 15-86% correlating with gains (mathq +15/+26% at
  69% eng; bashops +7% at 86%; cstruct -1.5/-1.8% at 43/15% — the ONLY
  negative cell = the surviving C-continuation weak class).
Supersedes Oct-29's "+3.6%" (which compared two spec-off arms = noise):
production config on realistic tool traffic is worth ~+6.5% e2e. Engine
restored armed (PID 6116) after arms; day-5 + bounding soak backed up
(day-5 to ~/Backups/ds4-train-20261101/).

### Engine-attached experiments completed in the 05:50-07:0x window
- FULL REGRESSION SUITE on final tree: `ds4 tests: ok` (V4-context suites
  pass; qwen-context skipped by design). Tree is production-proven.
- FP8-JITTER A/B (DS4_DSPARK_FP8_JITTER=1, greedy, 4 prompts x 400 tok):
  accept-rate delta MEAN +0.4pp with mixed signs (pyrefactor 91.1->88.9,
  bashops 87.6->89.4, mathq 82.7->82.4, docrewrite 68.8->71.0); gen-t/s
  softness = content-path divergence, not draft degradation.
  VERDICT: fp8 capture format is fidelity-neutral for acceptance ->
  f16 switch PERMANENTLY DESCOPED; capture module + jitter harness now in
  ds4-server binary (relinked 07:0x).
- Corpus at re-arm: 211,006 banked (day1-6, day6 = 6,215 recs + backups
  complete); /tmp/ds4-kv cruft removed; blade 850 GB free.
- (Prior window retained: revision-pin escape tested both directions,
  dspark-acceptance 4/4 output_match, day-5 banked — see queue above.)
- PROPOSED-COMMITS.md drafted (7 groups; full-suite green on exact tree).

## PARITY DUMP SHIPPED (window 2, ~08:30): DS4_TD_PARITY=<dir>

### Scaling VERDICT: DATA-BOUND (cos32x15, 304,095 pairs = 1.55x)
top1 **.4369 / top3 .5769** (vs 203k-point .4010/.5364) — plateau moved
+3.6pp (+9% rel) at a clean new convergence (last 8 epochs .4334-.4375,
cosine complete -> real plateau, not mid-flight). delta_from_h **.3025**,
boundary .851, AUC .743/.787. Extrapolation from (195k,.401)/(312k,.437):
~+3.5pp per 1.5x step -> ~.45 at 2x, ~.46-.47 near 1M pairs unless it bends.
mlp32's regression re-reads as optimization difficulty of the trunk at this
scale, not an information ceiling. CONSEQUENCE: soaking continues to buy
measurable ceiling lift; the transformer mirror starts from a moving floor,
which is the ideal situation (both paths pay).

### Scaling point: cos32full (203,704 pairs, +4.4% corpus)
top1 .4010 / top3 .5364 / AUC .742/.786 / boundary .843 — vs 195k-pair
cos32 (.4014/.5334/.733/.783/.806): **zero top-1 lift at this margin**
(top3 +.003, boundary/AUC nudges within noise). Reading: +4% was always
too small a step to move a log-linear curve (>1pp expected only at ≥2x) —
INCONCLUSIVE on data vs class, but stacked with mlp32 (< linear on the same
schedule) it tilts toward "near linear-class ceiling on one-position
prediction". Decisive run queued (engine-free, ~1h): repeat cos32full
protocol at 2x corpus when soak reaches ~400k pairs.
Per prepared cycle (cap DS4_TD_PARITY_MAX, default 4096): pos, proposal-head,
CPU-mirror proposal tokens (s->dspark_draft_tokens, true engine output incl.
confidence truncation: observed dlen 1-2 at gate .45 with matching pos-gaps),
stages, + 5x4096 f32 conf-input rows (metal_graph_dspark_confidence_hidden).
Fixed stride 81,952 B. First consumer task (mirror stage-0/1 parity):
reproduce (a) proposal token ids and (b) which rows clear logit-vs-0.45
purely offline from {captured h (train dump), seed chain x markov_w1 state,
extracted confidence_proj}. Engine-side deliverable DONE; mirror forward is
the remaining half. Smoke: 200-token CLI run -> 38 cycles, proposals decode
against pos-gap chain, rows finite (|h| mean .18).
Note: parity is independent env-gate; inert (getenv once) in production.
