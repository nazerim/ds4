# PLAN — DSpark recalibration audit (20261106)

**Question:** the 3c campaign proved confcal3 ranks well offline (+2.25pp AUC)
but its gate fired at ~3% live where the rescore-through-bytes simulation
predicted 12.8-14.6% at >=32k. Is that a *threshold* problem (recalibrate ->
retry) or a *feature-distribution* problem (epitaph)? This audit decides,
with zero engine downtime.

**T0 (done 09:40, schema ground truth):** capture records (`ds4_td_rec`,
ds4.c:78038-78070) = {pos, seed, streak, n_feat, flags, labels[], feat[]
(captured target-hidden ring)} - **no head score stored**; era stats
`prop_confidence=0.000` is never populated. So live scores cannot be read -
they must be *reconstructed*, and the gap measured as **predicted vs
observed gate behavior**, not score-vs-score. Bonus fact: scores are
deterministic functions of captured feats through the blade head
(bit-exact round-trip verified in the 3c deploy audit) - so per-cycle
predicted decisions ARE computable today.

## Method
**A. Substrate join (2h, no engine).** Day-13..15 sealed + day-16 after
tonight's seal. For each recorded cycle: rescore through
`support_AB_confcal3.gguf` (dequant path already imported as the tool
pattern) -> per-cycle gate decision at .45 -> predicted cut% per depth bin.
Observed cut% per era from era stats lines (B-era prop/cycle vs A-baseline
ratios; v5.1: 4.8% @ 8-23k, v5.2: 3.1% @ 86-97k, coverage keep 96.9%).
Key = conv/pos/cycle identity; replay-window traffic (B1/B2 eras, 57826,
9717-onward) is *the* live-labeled subset.

**B. Discrepancy profile (2h).** Where predicted-cut cycles and
observed-not-cut diverge, bin by: depth, conv type (tool vs prose), batch
shape (single-row verify vs fused), streak length, feature norm drift vs
training slab (feat[] distributions, day-13 vs now). Two discriminating
outcomes:
 - shift concentrates in batched/fused-row cycles -> head *inputs* differ
   between capture-ring and live consumption (engine-side scoring seam -
   fixable, engine-lane bug);
 - shift uniform across bins, scores simply higher on fresh traffic ->
   genuine distribution over-prediction -> threshold re-registration
   plausible (tau* = observed-deciles target), ranking survives
   (verify AUC on live-space scores per bin before believing it).

**C. Counterfactual + tau* (1h).** Replay recorded proposal streams at
swept thresholds: coverage curve (tau -> cut%), kept-accept delta, true-
reject share of cuts, estimated verify-burn saved at 90k calibrated
against v5.2's measured 0.992 ratio at 3.1% cut. Output: tau* candidate +
predicted live cut/ratio.

**D. Go/no-go memo (30m, rule pre-registered BEFORE seeing C):** retry
only if (i) B shows a fixable single cause, (ii) C projects >=10% cut at
kept-accept >= -0.3pp at tau*, (iii) predicted ratio gain >=1.5%. Else:
final epitaph paragraph, artifacts disposition list. Any retry = ONE
pre-registered A/B window, protocol v4 rules, band gate reused.

**Optional instrumented micro-window** (only if B is inconclusive): one
dev boot with a debug knob populating per-proposal score logging (engine
edit ~20 lines, dev-capture redirected) -> direct live score histogram.
Deferred by design - A-C may not need it.

Effort: ~half agent-day analysis, engine untouched. Trainer lives at
`trainer/dspark/conf3c.py` (not dspark/). Data deps: day-16 seal must
land first for its slice; day-13..15 sufficient for v1.

## Risk register
- Rescore-through-blade reproduces offline scores bit-exact but if the
  engine's live feature feed differs structurally (not just numerically)
  from the capture ring, A underestimates B's cause - mitigated by bin
  profile, exposed by the optional knob.
- Join keys across eras are session-local; replay-conv ids already
  documented in the 3c era notes cover the labeled subset.
- No new training, no confcal4, no threshold touching outside D's
  pre-registration.

## Collection closure (answers "can we close data collection?")
YES - after tonight's day-16 seal:
1. Seal day-16 (runbook + era disclosures: 57826 merged-code ~4min,
   9717 merged-collector from 08:46:29 - both in PLAN-UPSTREAM log).
2. Next restart uses the NON-collect default boot (`./ds4-server.sh
   start-qwen` / `start` - snapshot recipe minus TRAIN_DUMP); collector
   no longer the 24h standing mode. active/ drains to sealed; blade dirs
   immutable + backed up (backups/ precedent holds).
3. Re-enable recipe on record: any future window boots
   `start-collect` explicitly (the 3c chain pattern already does this per
   era) - standing collection has zero exclusive consumers; the audit
   reads only sealed days; V4-format watchpoints read engine logs, not
   captures.
4. Keep: sentinel + rewind stats (engine health != collection); delete
   /tmp/ab52 post-seal as planned.
Net: disk pressure off, era-hygiene surface stops growing, all doors
still open, nothing in the audit needs future standing capture.
