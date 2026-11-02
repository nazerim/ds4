# NIGHT-RUN ledger (all engine-free; ds4.c untouched by every run)

## Run #3 (01:15 ->): training loop v1 — h-signal ablation
Question: do the captured taps carry draftable signal BEYOND bigrams?
Head: z = (w1[seed] + x@A) @ w2.T, A zero-init (starts at v0 FT), conf on a
separate trunk; post-training eval runs WITH and WITHOUT A — delta_top1 is
the measured tap-information content. Full 6-epoch x 195k run writes
TRAIN_V1.json + v1_head.pt. Resource answer to user: **no engine, no
window, ~10 GB slab on 128 GB — everything left is engine-free.**
Window queue unchanged: f16 measurement, parity dumps, revision-pin escape,
bounding re-verify with tools.

### Run #3 RESULT (v1, joint): THE TAPS ARE LOADED
| variant | top1 | top3 |
|---|---|---|
| official markov | .120 | .182 |
| v0 bigram FT | .179 | .280 |
| **v1 with_h** | **.312** | **.414** |
| v1 no_h (A zeroed post-train) | .140 | .205 |
- delta_top1_from_h = **+0.172**; boundary stratum top1 **.813** (n=134,
  CI .74-.87) — h nearly free-predicts tool-call template continuation.
- Conf AUCs unchanged (.733/.783) — trunk separation behaved.
- Caveats logged: (1) no_h .140 < v0 .179 => joint co-adaptation muddies
  attribution -> two-phase run (3 ep A-only with markov frozen, then joint)
  launched; (2) CE init at 23.2 (official markov logits unnormalized) rose
  in epochs 4-5 -> overfit signal on train set while holdout still improved
  => add logit temperature init + early stop / weight decay sweep; (3) rank
  256 chosen to match markov width — sweep 512 next.
- Interpretation: a LINEAR rank-256 read of 3-tap h almost doubles next-
  token top1 over tuned bigrams => the mirror's input carries rich draft
  signal; a trained transformer body should extract more, not less. This is
  the strongest offline evidence yet that Stage-3 is worth building out.
- Artifacts: TRAIN_V1.json (joint run; run #1 archived to scratch),
  v1_head.pt. Attribution run overwrites both on finish (run1 kept).


## Run #2 (00:40-01:10): first real training signal — details below

Read this + PLAN-TRAINER-20261101.md when resuming. Engine untouched all
night (PID 2559 armed; 15-min heartbeat traffic captured normally — 300+
records by report time). Independent code-review pass (ses_e6d2a1c8) ran
mid-night: markov orientation was VERIFIED correct four ways against ds4.c
(logits[cand]=dot(w1[prev],w2[cand]); official holdout 0.120 = ~16,000x
chance), the q2_k port had 3 stacked bugs (offsets 80:82/82:84 were read at
4:6; decimal codebook literals unparsed; ksigns broadcast crash) — all
fixed, retested, re-run. 12/12 tests green.

## 1. Markov fine-tune (MARKOV_RESULTS.json, trainer/)
Held-out = frozen day-3 bigrams (7,493 pairs). Official head = the GGUF's
own w1/w2, no training.

| train pairs | top1 | top3 |
|---|---|---|
| 0 (official) | .120 | .182 |
| 12,197 | .123 | .190 |
| 24,395 | .125 | .191 |
| 48,791 | .133 | .200 |
| 97,582 | .152 | .234 |
| 195,165 (2ep) | .174 | .253 |
| 195,165 (3ep FT) | **.179 (+49% rel)** | **.280 (+54% rel)** |

Stratified FT result: boundary-class top1 .474->.519 (n=135, wide CI —
treat as neutral-to-positive), plain .114->.173 (n=7,358, solid).
Protocol: each curve point re-inits from official (fair), seed=0,
2 epochs/point (knee is jointly a size+epochs statement).

**Conclusions:** (a) adaptation to our traffic is real and large in
relative terms; (b) curve is **still rising at 195k — no knee**; data
continues to pay; next probe point ~500k-1M pairs; (c) absolute top-1 of a
pure markov bigram is NOT p1 — engine adds this as bias on drafted logits
(ds4.c:36867) — deployment gain shows up as acceptance delta in batteries.

## 2. Confidence-signal probe (CONFIDENCE_PROBE.json)
MLP h[pos-1] -> P(commit>=1 / >=3), holdout = day3+active (caveat printed:
grows between runs), tie-safe AUC, stratum guard (boundary n=65 omitted):
AUC >=1: **.740**, >=3: **.778** (plain stratum .735).

**Conclusions:** captured fp8 features carry strong acceptance signal ->
the confidence-BCE objective is feasible; probe conditioning (frontier h
only) is WEAKER than the runtime gate (which sees drafted-row hidden
states), so .78 is a FLOOR for frontier-time gates, not a ceiling.
Feature audit: 203k captured h-vectors are informative, distinct, and
predict future commit depth — the capture pipeline's value is proven.

## 3. Expert dequant (dspark/gguf_dq.py + tests/test_gguf_dq.py)
q2_k + iq2_xxs ported from engine formulas; **bit-exact vs scalar
transcriptions on real tensor bytes** (golden tests pin the first blocks of
ffn_down_exps/ffn_gate_exps). Expert-0 materialized all 9 tensors (2.98s,
stats healthy: std ~.05, absmax .26-.39). Full materialization = ~4.4 min
CPU, ~65 GB — available, but experts stay FROZEN for the v1 fine-tune.

## 4. Corpus/baselines refreshed
- BASELINE.json now covers all 4 banked days: 202,598 cycles,
  density 0.224 cycles/token; day-4 128k+ band p_ge3 = .482 (THINKING-heavy
  overnight traffic) vs day-2 .879 (DSML-heavy) -> depth alone doesn't kill
  acceptance; REGIME does. Useful stratification for gate reporting.
- day-4 structural coverage: 2.15% records touch a boundary (vs 0.8% day1+2
  combined); overnight heartbeat work is boundary-dense. toolcall-own mean
  gap 4.98 = full-block drafting at boundaries continues at depth (177 of
  them at 128k+).
- Live soak: active/pid2559 growing cleanly; capture + heartbeat compatible.

## 5. Review-driven fixes applied (all tested)
C1 q2_k offsets | C2 decimal/hex table parsing | M1 ksigns broadcast |
M2 probe docstring honesty (floor not ceiling) | M3 NaN/JSON strictness +
stratum guard | M4 __main__ guards everywhere | markov: merged scaling+FT
output (curve now PERSISTED, was stdout-only), seed=0 determinism, frozen
day-3 holdout (removed live 'active' from train-run holdout), removed false
"regularized" comment, Wilson CI on boundary metric | probe: tie-safe
average-rank AUC, detach() warnings. INFO kept: e4m3 0x7F->+480 sentinel
documented in data_contract (zero occurrences measured).

## 6. Next actions
1. Continue soak toward ~1M bigram pairs; re-run markov_ft at 500k to find
   the knee (one command, engine-free).
2. TRAINING LOOP v1 (engine-free build): markov + confidence jointly on
   captured features (the real objective shapes from the plan); the bigram
   CE here was v0 proxy — v1 conditions stage CE on h as well.
3. Window queue (unchanged, engine-side): parity dump knob, f16 capture
   measurement, revision-pin escape, deep-ctx-with-tools bounding re-verify.
4. Morning hygiene unchanged: validate before banking; graceful stop only.
