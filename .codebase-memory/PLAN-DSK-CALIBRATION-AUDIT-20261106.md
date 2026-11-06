# PLAN — DSpark recalibration audit, v2 (20261106; v1 SMASHED by review ses_e517bb225)

**Question unchanged:** confcal3 ranks (+2.25pp AUC) but the gate cut ~3%
live where the parity-rescore sim predicted 12.8-14.6% at >=32k (slice
label per ledger follow-up #10; other sanctioned ranges 9.5-14.9% / ~9%
whole-corpus - tau* must anchor on the right slice, not the narrowest).
Threshold problem (recalibrate -> one pre-registered retry window) or
distribution problem (epitaph)? Zero engine downtime.

## v1's fatal errors (why SMASH was right)
1. **Wrong tensor.** Capture ring `feat[]` = staged TARGET h[pos-1] taps
   (layers 40/41/42, fp8) - NOT the head input. The gate scores the
   DRAFTER final-stage hc_head rows (`metal_graph_dspark_confidence_hidden`
   = batch_ffn_cur) as [hidden 4096 | markov_w1(prev) 256] -> confidence_proj
   (q8_0 [4352,1]) -> sigmoid < .45 -> prefix_len (ds4.c:37031/37071/37163/
   37170, call chain via prepare_dspark_draft 82902->impl 82916->runtime
   precheck 82537). v1's "rescore the ring" would have scored a tensor the
   gate never reads; "ring vs live feed" discriminator was a category error.
2. **Misattributed proof.** "Rescore-through-bytes .8501 EXACT, 62/49281
   flips" = f32-vs-q8_0 blade-bytes fidelity on PARITY-derived rows -
   proves head-math through deployed bytes, not ring equivalence. Expected
   residual for this audit = ~.13% quantization-boundary flips, not
   "bit-exact".
3. **Impossible join.** Era stats are ONE aggregate/era (flush at session-
   free) - no per-cycle slice; `prop_confidence` is MILLISECONDS
   (propose_confidence_ms, CPU-fallback branch only), never a score -
   corrected reading of v5.2 follow-up #3; conclusion stands (no live
   scores readable).
4. **Closure boot mis-specified** - see Closure section (plain `start`
   flips the model fingerprint -> invalidates ALL disk-KV; not free).
5. 57826 has NO capture files (killed mid-era before session-free flush) -
   era-line disclosure only, out of all joins; PLAN-UPSTREAM line corrected
   same commit.

## v2 substrate: the parity dump IS the experiment
`ds4_td_parity_note` (ds4.c:78296) runs immediately post-impl, BEFORE
buffer reuse, storing **per cycle: engine's own post-gate dlen + props[] +
5x4096 conf_hidden rows (gate-time input)**. Recorded dlen==0 cycles absent
by construction (78312 return), and full-cut cycles also commit no train
record - both recovered as parity-vs-recs positional gaps / era `proposed`
delta, with denominator control (era prop/cycle dilutes over non-draft
cycles; sim's "cut% of gate-able proposals" is a different denominator -
the discriminator MUST condition on gate-able cycles only).
On-disk: /Volumes/FireCuda520/ds4-parity/20261103-s3/parity_pid{49377,
49527,49701,9717,...}.bin (+ matching train_pid*.recs) = B1/A2/B2 arms +
live merged collector era.
Tool pattern banked: kept_cycles(recs, parity) + dequant rescore
(trainer/dspark/conf3c.py, official3c.py; join by index+pos cross-assert -
trap: parity hdr.seed = props[0], NOT the committed seed, ds4.c:78333;
verify_cap clamp never binds at max_tokens=300; stochastic clamp never
fires at EXACT=1 - both noted).

## Method (v2)
**A. Predicted-vs-observed, per cycle (2h, no engine).** For each parity
cycle: PREDICTED gate = rescore the recorded conf rows through blade
head (dequant, threshold .45, prefix rule incl. ascending-rows-before-
break); OBSERVED = recorded dlen. Outputs: per-era and per-depth-bin
predicted cut% vs observed cut% over gate-able cycles; flip census should
match the known ~.13% quantization residual (if not -> tool bug first,
per sensor lesson).
**B. Discriminator.** Predicted ~= observed (~3%) -> the sim's 13% came
from the WRONG POPULATION (slab-provenance cycles vs live novel cycles):
pure distribution story -> go to C. Predicted >> observed systematically
(13% predicted, 3% observed on the SAME gate-able cycles) -> engine-side
scoring seam (buffer reuse timing/fuse_final_hidden) -> genuine ds4.c bug,
engine lane owns; audit pivots to a minimal repro from parity rows alone.
Uniform check: bin residuals by batch shape/streak/feature-norm (the rows
are IN the file - this was the point of the rewrite).
**C. tau* + counterfactual (1h).** Observed-decile calibration: sweep
threshold on the live score distribution -> coverage curve, kept-accept
delta, true-reject share, burn-saved at 90k. Baseline labeling corrected:
v5.2 speed = paired median 1.000 at n=2 (mean -0.02 marginal FAIL, stop-
guard parity 0.992 unpaired) - project against that, not a phantom
"measured 0.992".
**D. Go/no-go memo (30m).** Rule pre-registered before viewing C: retry
iff single fixable cause in B OR tau* projects >=10% cut with kept-accept
>= -0.3pp AND ratio gain >=1.5% - WITH power caveat (v5.2's -0.29pp was
<1sigma at n~1024; a -0.3pp gate is knife-edge, so the memo states MDE).
Else: epitaph paragraph + artifact disposition. Retry = ONE window,
protocol v4 rules, band gate, new pre-registration.

Effort: ~half agent-day. First real experiment = B1-era rows (49377.bin):
predicted cut% on the arm's OWN cycles where the engine observed 4.8/3.1%
- hours after starting, the campaign's central question has a number.

## Collection closure (v2, corrected)
YES after day-16 seal tonight; corrected mechanics:
1. Seal day-16 (era disclosures incl. 9717 merged-collector from 08:46:29;
   57826 = boot-era disclosure, NO records - corrected).
2. Reboot capture-off but behavior-identical:
   `DSPARK_EXACT=1 DS4_REWIND_RESUME=1 ./ds4-server.sh restart-vision-dspark`
   (plain `start` = no --dspark AND model-fingerprint flip that invalidates
   every disk-KV file -> mass re-prefill, per script's CRITICAL comment
   ds4-server.sh:154-159; plain start-qwen = different model/lane).
   Without DSPARK_EXACT it silently defaults 0 (:282) and REWIND_RESUME off
   - changing decode vs the whole collect era. This line IS the fix.
3. Re-enable for any window = boot `start-collect` explicitly (chain
   pattern); nothing in the audit or V4 watchpoints needs standing capture;
   sentinel/rewind stats unaffected.
4. Post-seal: delete /tmp/ab52; blade sealed dirs immutable + backed up.

## AUDIT RESULTS (step A/B done 17:55, substrate = sealed day-16 eras)
Files: /tmp/dska.json (era-matched heads), /tmp/dska2.json (blade-on-
official-rows); script trainer/dspark/audit_recal_20261106.py.

1. PLUMBING: INNOCENT. M1 (offline re-score of KEPT rows falls <.45):
   B2-blade 0/1011; B1 1/2847; day16-official 38/119923 - all ~3e-4 =
   the quantization-boundary noise class, matching the documented
   62/49281 flip rate. Engine-gate decisions == offline recompute on the
   identical stored tensors. No ds4.c seam. Parity substrate validated
   (ppos==pos join across 37k+ cycles).

2. POPULATION: NOT the villain either. Head-vs-official coverage on live
   rows: blade-on-A2-rows 6.0%, blade-on-day16-rows 9.8% of
   official-kept rows would drop - SAME ORDER as the sim's 12.8-14.6%.
   The "coverage collapse" story was partly a DENOMINATOR ILLUSION:
   sim counted gate-able proposal ROWS; era stats diluted over ALL
   cycles' proposals/cycle (1.70->1.65=3%). On live data the head does
   roughly what calibration said it would.

3. THE REAL FALSIFIED ASSUMPTION: coverage -> wall-time. With genuine
   ~6-10% row coverage at 86-97k ctx, v5.2 STILL measured paired t0
   median 1.000 (mean -0.02 marginal FAIL). Dropping prefix-truncated
   proposal rows does not shrink verify cost under EXACT sampling -
   the verifier runs the block regardless; cut rows are the ones that
   would have been rejected anyway (quality-neutral, 62% true-rejects
   per the earlier sim). Depth is the lever the gate cannot pull: mean
   kept/cycle 3.21 (day16 official, 32k+) vs 3.60 A2@32k - accept
   tails are long; verify-tail cost, not proposal count, dominates.

CONCLUSION: conf-line epitaph UPGRADES from "head transfers poorly" to
"head transfers FINE; the speed model it served was wrong." Threshold
re-registration would buy ~7pp more coverage and ~0 wall-time - NOT a
retry worth a window. Future DSpark speed work should target the verify
side (position-dependent early-exit / marginal-cost block sizing -
adjacent to upstream's active Metal verify-speedup PRs #1179/#1149),
not gate coverage. No new pre-registration needed: closing stays.

Post-conditions met: zero engine time used; all evidence files sealed;
script committed (see next); no blade changes.

## DISPOSITIONS (closed off 20261106 ~18:00)
- Blade artifacts (conf_s3cal_20261105.npz 5.6GB, run_confcal3/,
  run_officialreplay_s3cal/, support_AB_confcal3.gguf, slab prev):
  KEEP flagged closed-line - zero storage pressure, sole consumer is a
  hypothetical verify-side-economics reopen (different objective than
  ranking). Purge only if blade space ever binds.
- PR #1163 + #785: re-verified MERGEABLE upstream post-merge - deliberately
  NOT touched (no re-push churn that resets review state; next action =
  wait on antirez). #1020/#1022: KV-lane refresh handoff stands
  (4-file conflict map in PLAN-UPSTREAM execution log).
- Standing collection: CLOSED (day-16 sealed d54d059). Re-enable = per-
  window start-collect, documented in DS4FORK + PLAN docs.
