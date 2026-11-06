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

## AUDIT RESULTS (steps A/B; CORRECTED per interpretation review ses_e4fb7acdc = CONFIRM_WITH_EDITS; original RESULT text superseded)
Files: /tmp/dska.json + /tmp/dska2.json (also copied to
labels3c/dska*_20261106.json); script trainer/dspark/audit_recal_20261106.py.

1. PLUMBING: INNOCENT (justification corrected). M1 (offline re-score of
   KEPT rows below .45): B1 1/2847, B2 0/1011, A2 0/1027, day16
   38/119923 (=3.2e-4). Blade arms re-scored with the SAME deployed bytes
   the era ran, so expected quantization noise is ~0 (observed 0 and one
   tie at p=0.4499); official arms' one-sided expectation from the
   documented 1.26e-3 flip census is ~6.3e-4 - observed 3.2e-4 is BELOW
   it. M1 is one-sided (capped walk cannot show over-cut) but every
   coverage-INFLATING seam must pass through a kept row: residual hidden
   inflation <=~0.03% vs the 10pp gap - ~300x too small. Scope: "engine
   decisions == offline recompute" holds for KEPT-row threshold crossings,
   which is all coverage can see. Substrate caveats: dropped-row scores
   are off-record (parity stores only d<dl rows; rows past dlen are a
   stale static buffer - ds4.c dump-comment 'zero-padded' is WRONG, a
   real dump bug logged for engine lane); ppos!=pos cycles skipped
   silently (no mismatch counter; era cross-check A2 1027 joined rows vs
   1048 era proposals corroborates ~98% coverage of the joined subset).

2. POPULATION: INNOCENT - stronger than first claimed; the earlier
   'DENOMINATOR ILLUSION' mechanism is REFUTED (dilution <=2%: 98% of era
   proposals already sit in gate-able cycles). Corrected mechanism:
   blade-on-official-kept-rows drops 6.0% (A2) / 9.8% (day16) of ROWS =
   after PREFIX conversion (mean tail loss 1.92 rows/cut-cycle) a
   PROPOSAL-LEVEL cut of 13.0% at 32k+ (day16: 16.4%) - landing INSIDE
   the sim's 12.8-14.6% band (which was itself proposal-ratio units, not
   rows). The sim transfers to live traffic at face value. The era's
   REALIZED 3.1% is a different quantity: the gate's self-consistent
   equilibrium on its OWN traffic (blade cuts early -> 26 cycles move out
   of the gateable pool 270->244, survivors lengthen 3.604->3.958 at
   32k+, prop/cycle absorbs +0.8% cycle-count inflation). v5.1's 4.8%
   was likely ALL inflation (raw totals 1741/1740 not logged in the
   ledger - caveat flagged; v5.2 pattern reproduces independently).

3. SPEED LINK: FALSIFIED - conclusion holds, two old supports replaced.
   (a) v5.2's median 1.000 constitutes realized coverage ~3.1%, not the
   6-10% counterfactual - one datapoint, but the ceiling math below is
   what closes it. (b) NOT "verifier runs the block regardless" (false:
   ds4.c:86100 draft_n post-gate, width-adaptive verify): the true
   mechanism is that EXACT pays one full-model forward per CYCLE, so
   per-row cuts at dlen<=5 save ~nothing - observed savings slope <=
   measurement floor (24 rows moved -> ratio 1.000; bound ~<=0.08% era
   wall per 10 rows => tau*-scale 180 rows => gain <=~2%, at floor).
   (c) LOSS leg: 37-38% of cut rows are would-accepts (62-63% true-reject
   figure is val-corpus; live accept rates 75-94% mean cut rows are
   MORE likely accepted - 'quality-neutral' deleted, it is backwards in
   the live regime): +26-34 cycles (+4-5.5%) projected => net wall-time
   -4% to -10% at tau* coverage. CONCLUSION: coverage is the wrong lever
   REGARDLESS of threshold; the per-cycle fixed forward is the target
   (verify-side economics / block sizing - upstream #1179/#1149
   territory). No tau* sweep artifact exists; '~7pp more coverage' reads
   as forward projection only.

Epitaph (final, corrected): the head transfers EXACTLY as calibrated
(13% -> 13.0% same-units); realized gate coverage cannot buy wall-time
(fixed per-cycle forward, slope at measurement floor) and costs cycles
(4-10% inflation) - the campaign's death was arithmetic, not a bug,
not drift, and not the head. Line closed; no retry window; engine-lane
carry-forwards: parity-dump stale-row bug (one-line: read all 5 slots or
zero the tail) + dump-comment correction.

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
