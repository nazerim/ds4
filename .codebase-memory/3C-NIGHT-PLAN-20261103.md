# 3C-NIGHT-PLAN-20261103 — overnight execution plan + live progress ledger
Serial queue (remote model server = single agent slot; local compute free to run
underneath). Cold-start context: 3C-HANDOFF-20261103.md (facts/plan), this file
is THE progress tracker — update the checkboxes + PROGRESS LOG at every slot
boundary and commit.

Window: 22:03 Nov-03 -> ~07:15 Nov-04. Engine 60838 up, collecting, NO live
traffic overnight (user asleep). Model server remote; all compute local.

## ENGINE POLICY (user-authorized Nov-03 22:00)
- L0 default: all jobs <= 8 GB RSS, engine untouched.
- L1: free < 10% persistent -> pause heavy phase, resume on headroom.
- L2: L1 starves the critical path during slots 2/1b/4 -> FULL BANK RUNBOOK
  (stop -> td_validate -> seal 20261104-day14 -> td_manifest --note -> rsync
  backup -> loops --audit --manual --out regen -> leave down), then run
  unshackled (expert stacks resident f16 ~13 GB). Bonuses: pid60838 cycles join
  the conf pool; stop only when log quiet (no `chat` line in last ~3 min).
- RESTORE 07:15 HARD DEADLINE (unconditional, even if all gates red):
  ./ds4-server.sh restart-collect -> verify TWO ways (close-out review
  MAJOR 2: `exact: 1` exists ONLY in the script's TERMINAL banner, echo
  ~416 - it NEVER appears in log/ds4.log, a log grep is unsatisfiable):
  (1) log/ds4.log line 1 shows 'DSpark support model detected:
  gguf/DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf (stages=3 …
  invalid=0…)'; (2) ps -o command of the NEW pid shows '--dspark
  --mtp-exact-sampling --dspark-confidence 0.45'. Capture banners are
  lazy - do NOT probe.

## STANDING GUARANTEES
1. Engine up + collect-armed by 07:15, or restore attempt = first morning line.
2. Every slot boundary: commit (code + manifests + run JSONs). Worst-case night
   still yields: probe decision, label builder, calibrated eval harness, torch port.
3. Official support GGUF untouched (repack proofs on COPY only); day-3 holdout
   frozen; NO A/B execution (doc only); no probes against the live server.
4. Corpus/parity dumps blade-local, never committed. Secrets never echoed.

## THE QUEUE  (gate RED = stop that lane, honor preemption order)
>>> REVIEW RULE (user, 22:45): EVERY step ends with an INDEPENDENT
>>> fresh-context review (subagent, read-only) of its artifacts BEFORE the
>>> step is marked done and its outputs are consumed by the next step.
>>> Verdicts land in PROGRESS LOG. Reviews: S0 script+data+decisions;
>>> S1a/S1b builder code + slab spot-audit; S2.5 metric math vs milestone-1;
>>> S3 run config + report reading; S2 port 1:1-ness + gradcheck honesty;
>>> S4 config; S5 byte-identity proof; S6 protocol soundness.
Preemption under time slip: 4 early start -> 1b -> NEVER 0/2.5/3/2.

^- [x] S0 22:05-22:50 LABEL-SEMANTICS PROBE — DONE 23:1x (probe+REWORK-fixed;
      D-S0 FINAL in DECISIONS below; S1a/S3 consume it) (3C-HANDOFF §6 a/b/c)
      Q(a) props[dlen-1] = last-accepted (H1: dlen=accept-count) or reject slot
          (H2: dlen=accept+1)? Test props[d]==t[pos+d+1] per slot on joined
          exact-era cycles.
      Q(b) dlen histogram + per-slot accept rates -> BCE label definition.
      Q(c) seed-batch-era chains: labels real tokens (chain-continuity +
          props[0]==labels[1] at accepted cycles) -> markov-label usable.
      GATE: written decision in §DECISIONS below; script = dspark/probe_labels3c.py
- [x] S1a 22:50-23:55 CONF-ERA LABELS — DONE ~23:5x (review PASS_WITH_MINORS,
      minors fixed+slab rebuilt identical) conf slab day-13/pid53508 only.
- [x] S2.5 23:55-00:40 ACCEPT-PROXY EVAL HARNESS (dspark/eval3c.py) — DONE.
      GATE REDEFINED (defect found): original "reproduce .7418/.7933 +- .005"
      is REPLAYABLE-BUT-NOT-GATE-VALID, not unreproducible. Those are TRAINED
      V1Head conf AUCs on STALE streak labels (train_v1.py:255); the weights
      DO exist (trainer/v1_head_cos32clean2.pt) and day-3 persists, so they
      are replayable - but they measure cycle-level streak>=1/3, a target
      D-S0-1/5 disqualified, so they cannot gate a per-slot-accept head.
      Official-head replay from parity IS impossible: head proj is (1,4352)
      =[h4096|markov256], parity stores h only. New gate (PASS): (a) auc()
      == confidence_probe.auc bit-exact incl ties [4/4], (b) trainer-seen
      unmasked-row rates .802/.744/.728/.735 re-derived + uncond chain
      .802/.744/.568/.521 reconciled from same k_lab, (c) gate-stats +
      positional-prior baseline (pooled .5457) = S3's real bar.
- [x] S3 00:40-02:45 CONF-ONLY CALIBRATION RUN — DONE ~00:4x (confcal1 ->
      review caught bias non-deployable -> confcal1b refit, deploy-faithful).
      GATE (S2.5-reviewed): held-out per-slot AUC must BEAT positional-prior
      baseline (per-slot .5 / pooled .5457 from eval3c) AND be calibrated
      (ECE small); milestone-1 .7418/.7933 = aspirational ceiling note ONLY
      (a different, streak-label metric - NOT the gate). Split day-13 cycles
      train/val (e.g. 90/10 by conv, conv-disjoint) - day-3 stays frozen for
      the S4 joint run only. (Agent builds S5 repack tool during run.)
- [ ] S2  02:45-05:15 TORCH PORT mirror_torch.py (1:1 of Trunk.cycle, handoff
      §7/§8/§8b/§9) + verify legs:
      (a) torch vs numpy conf-row cos ~1.000 same cycles
      (b) Gate A through torch reproduces slot0 cos .9898
      (c) gradcheck: sinkhorn mixers + router
      Memory: L2 fires here if chunked dequant thrashes.
- [x] S1b 05:15-06:45 FULL-POOL MARKOV SLAB — DONE ~02:3x (built early,
      review CLOSED PASS_WITH_MINORS). markov_s1b_20261104.npz: 20 pid files, all sealed
      days ex-day-3; recs-only path (parity NOT needed for next-token
      labels); 724,297 recs read, 459,365 rows, 457,486 valid (1,879
      nl<2 sentinels next=-1, mask by mk_valid), 264,932 quarantine-
      excluded (ledger initially said 266,952 - reviewer corrected);
      provenance cols mk_file/mk_ridx; spot audit 6/6 vs raw.
- [x] S4  06:45-07:15 JOINT RUN START — ONLY if S2 all green AND S3 green.
      Checkpointed/epoch, hard 07:15 stop, morning = partial epoch curve vs .4529.
      COMPLETE ~04:2x (started early ~04:0x; S4 does NOT need the trunk:
      anchor trainer train_v1 = recs taps + markov bilinear; S2 numeric
      gates green; the gradable-path fixes under re-verify do not gate
      S4). Run s4full1: train_v1 anchor recipe VERBATIM (phase1 4, 32 ep,
      lr 3e-5 cosine, wd .01, trunk lin, rank 256, conf_w .2) on pool =
      all sealed days ex day-3 (457,371 pairs, quarantine 20261103) vs
      frozen day-3 holdout. New flags: --codes8 (uint8 e4m3 slab 5.23 GB
      vs 20.9 GB f32, BIT-IDENTICAL decode proven on day-5 full; L0
      coexist with engine) + --ckpt (atomic per-epoch PARTIALs; unused -
      run finished). RESULT (review-closed): plateau e24-31 top1 .4652 vs
      anchor .4524 (+1.28pp), top3 .6110 vs .5944 (+1.66pp plateau-vs-
      plateau); PAIRED vs anchor head on same 7,492 holdout: +1.31pp,
      CI [+0.80,+1.82], sign p=5.3e-7 = beyond holdout-sampling noise;
      seed replication via s4full2. See SLOTS:RESULTS S4 +
      trainer/TRAIN_V1_s4full1.json.
- [x] S5  (inside S3/S4 compute windows) REPACK TOOL on COPY of support gguf:
      extract -> re-insert -> file sha256-identical proof; layout ds4.c:6113.
      DONE ~00:5x tools/repack_dspark_tensor.py: roundtrip sha256 identity
      PASS + in-place q8_0->q8_0 swap of confcal1b head proven on copy;
      independent review CLOSED PASS_WITH_MINORS ~01:1x (minors fixed,
      q8_0 goldens pinned).
- [x] S6  (gap filler) AB-PROTOCOL doc + bench script (no execution) —
      v1 REWORKED ~04:4x after independent review (BLOCKER: DSPARK_MODEL
      lever dead; port 8001; byte-offset windowing; stats env mandatory;
      probe gate; tool prompt; tok/s directional). v3 in
      .codebase-memory/AB-PROTOCOL-3C-20261104.md. RE-REVIEW CLOSED
      ~06:5x PASS_WITH_MINORS: all REWORK blockers confirmed genuinely
      closed (lever chain -> DSPARK_PATHS[1] line 204, repo-root, banner
      guard, rollback); ONE new MAJOR found + FIXED (bench hist parse vs
      REAL 'k:count' pair format, 17 bins - E2E-tested vs engine-shaped
      line); doc updated to arm B = confcal2 (sha 5c0286dd..) + coverage
      redefined as DSpark engagement + conservative-gate watch item.
      MUST-encode checklist from S5 review: all 6 items present in v3
      (copy-based swap + dual shas; boot-bind restart rule; deployable
      form (now confcal2 engine form); sigmoid>=.45 semantics; judge on
      accept-LEN hist + engagement not AUC; q8_0 goldens green).
- [x] R   07:15 ENGINE RESTORE (see policy) + night report section below.
      DONE 10:07 (ahead-of-schedule operator signal via stall report):
      swap-thrash stall -> restart-collect -> PID 73479 verified NEW-WAY
      (log line 1 + ps flags + terminal banner). Full record in SLOTS:R.

## SLOTS: RESULTS (fill live)
- S1a: /Volumes/FireCuda520/ds4-train/labels3c/conf_s1a_20261103.npz
  30,205 cycles / 100,474 slot rows (f16 conf rows [:dlen] only) / meta +
  masked labels; kept math 30,609-6(dlen1)-398(nl<2)-0(quar); index-join
  pos_mismatch 0; cases a/b/c = 17,937/8,566/3,702; masked-BCE positive
  rates .802/.744/.568/.521 (D-S0-4 FINAL). Reviewer full-array byte-exact
  recompute + 3-cycle spot audit PASS; f16 storage ruling: keep.
- S0:
- S1a:
- S2.5:
- S3: run_confcal1b/ (bias-free, deploy-faithful; SUPERSEDES confcal1) =
  linear(4096)+ZERO markov half, NO bias (engine conf = pure matvec,
  validator pins [4352,1] DENSE); hashed conv split (hash%10==0 val,
  3482 val cycles / 8992 val rows); pooled val AUC .8453 vs IN-RUN
  val-population baseline .5448; per-slot .883/.841/.808/.772;
  ECE .0109; gate region [.35,.55] n=1538 pred .4666 obs .4824.
  Ceiling note .7418/.7933 exceeded but different metric. CAVEAT (review):
  within-day single-pid (day-13/53508) conv-disjoint generalization ONLY -
  cross-day/cross-era drift unproven. confcal1_val_scores.npy covers ALL
  rows - consumers must mask (conv-hash val AND label!=-1).
- S2: trainer/dspark/mirror_torch.py — torch twin of Trunk.cycle (zero-copy
  views, shared expert cache, dtype-generic diff ops, NumpyGateAdapter).
  Review CLOSED PASS_WITH_MINORS ~03:3x: numerical fidelity bit-exact
  (30/30 cos bit-identical to reviewer's rerun; Gate A identical 6 dp;
  TIE-CONVENTION-RULING zero new risk). MAJOR (gradable path) FIXED per
  reviewer ruling: router logits.detach() before .numpy(); fp8_kv_quantize
  functional cat + explicit detach of quantized nope half (rope half keeps
  grad - engine-faithful asymmetry); ALL in-place row writes functional
  (_cycle, _ffn stack); ring append detached; gradcheck ctx (no more
  global no-grad trap); _xpcache FIFO-bound + share-don't-train policy.
  POST-FIX RE-VERIFY: gradable smoke PASS (T3 q_a grad 1.51, T4 head_fn/
  main_proj/s1_q_b all nonzero, fwd cos .9999997, peak RSS 6,993 MB <=L0);
  selfcheck PASS (gradcheck 8/8, 30-row cos min .99999988); GATE A rerun
  IDENTICAL at 6 dp to frozen baseline. Reports /tmp/{s2_gradable_smoke,
  mirror_torch_selfcheck2,gateA_torch_20261104b}.json. S2 DONE.
- S1b: /Volumes/FireCuda520/ds4-train/labels3c/markov_s1b_20261104.npz —
  review CLOSED PASS_WITH_MINORS ~02:3x: conf-rebuild BYTE-IDENTICAL (S1a
  review stands); full audit all 459,365 rows vs raw (0 mismatches, 0 dup
  cycles, quarantine per-file exact, total 264,932); sentinels 1,846 nl0 +
  33 nl1; S4 rules: mask mk_valid==1; split keys (mk_file, conv) with conv
  via mk_ridx->idx.jsonl join (bare conv ids COLLIDE across pids); 422
  rows (0.09%) cross-file conversation-continuity residue accepted+noted.
- S4: s4full1 DONE ~04:2x (32 epochs, 48 s/epoch avg, /tmp/s4full1.log);
  review CLOSED PASS_WITH_MINORS ~04:5x. CLAIMABLE (reviewer's exact
  phrasing): "On the frozen day-3 holdout (7,492 pairs), s4full1 beats
  the anchor HEAD by +1.31pp top1 (paired: 238W/140L, bootstrap 95% CI
  [+0.80,+1.82]pp, exact sign p=5.3e-7); plateau-window means (e>=24,
  protocol rule) .4652 vs .4524 = +1.28pp top1, .6110 vs .5944 = +1.66pp
  top3; new max delta_from_h .3275." Reviewer reproduced EVERY number
  (plateau from anchor's OWN json = .452412/.594400 - my earlier
  "+1.60pp top3" mixed plateau-vs-FINAL bases, corrected here; paired
  W/L 238/140 exact; CI [+.0080,+.0183]; codes8 bit-identity extended to
  day-8+day-12; pool 427,162+30,209=457,371 exact).
  NOT claimable: boundary-strata superiority (.8582 [.789,.907] vs
  anchor .8657 [.798,.913], n=134 = noise). "beyond SEED noise" WAS
  unclaimable at s4full1 (paired CI bounds holdout-sampling noise only)
  -> NOW EARNED via s4full2 replication (see SEED REPLICATION below +
  review S7 item 4: "two independent seed runs both beat the .4524
  anchor, statistically indistinguishable from each other (-0.13pp,
  p=0.28), run-to-run noise ~0.1pp << 1.3pp lift").
  LEAK-BOUND: day3<->train exact-tuple leak 6 rows (.08%); differential
  vs anchor <= 8 day-13 context rows = <=.107pp absurd-worst-case ->
  does not threaten the delta. Protocol note: cos32clean3 is NOT a
  recipe control (no --cosine/--phase1/lr3e-4 -> its no_h .1819 + 23%
  markov drift are artifacts; s4full1 no_h .1384 vs clean2 .1344 same
  band; markov drift 1.5%/9.5% = clean2 class). Quarantine-1103 mtime
  footnote: clean3 ran the 77-span version; final-file scan reproduces
  the arithmetic exactly.
  DEPLOY: s4full1 = milestone evidence ONLY (head form != engine head;
  engine keeps OFFICIAL w1/w2; disk-KV/model_fp implications); deployed
  conf path stays confcal1b; S5 repack sha 3365e6b4.. unchanged, arm-B
  artifact needs no regeneration.
  SEED REPLICATION (s4full2, --seed 1, ~05:55): plateau top1 .4646
  [.4636,.4657] / top3 .6103; paired vs anchor +1.17pp CI [+0.67,+1.71]
  W/L 238/150 sign p=9.2e-6 -> SECOND run beats anchor too; s4full2 vs
  s4full1 -0.13pp CI [-0.35,+0.08] p=0.28 = run-to-run band ~0.1pp <<
  effect -> review MINOR 2 (seed variance) CLOSED. Both plateau bands
  sit fully above anchor band [.4513,.4529]. Claimable line upgraded:
  "two independent seeds (0,1) both beat the anchor head on the frozen
  day-3 holdout; deltas +1.31/+1.17pp, CIs exclude 0; seed spread ~0.1pp".
- S3b: OFFICIAL-HEAD REPLAY (dspark/official3c.py, run_officialreplay1/):
  engine-faithful replay of the official conf head on the S1a slab
  ([h_d | markov_w1(prev_tok_d)], prev_0=seed, prev_d=props[d-1], code
  ds4.c:37033-37064/80320; keep-pass cross-asserted vs slab meta all
  30,205 cycles). ENGINE PAIRING CONFIRMED: H1 chain .8243 > H2 seed-only
  .7946 (deep slots). VAL-SPLIT (same hashed 8,992 rows): official .8360
  (slots .8775/.8352/.8114/.7671) vs confcal1b .8453 (.8829/.8409/.8077/
  .7722) vs baseline .5448 -> confcal1b BEATS official pooled (+.0093;
  wins slots 0/1/3, loses slot2 -.004). Deployment-relevant: official
  gate-.45 coverage 1.000 (cuts NOTHING in exact-era pool) vs confcal1b
  .9083; 825 val rows (9.2%) disagree at the gate. "Beat the official
  head" = honest but modest AUC delta + the head actually engages at .45.
  Official replay scores saved (official_h1_scores.npy, all rows) - S4
  comparison baseline.
- S4b: CONFCAL2 JOINT CONF HEAD (~05:2x, run_confcal2/): conf3c.py
  --markov mode = linear(4352) over the FULL engine feature
  [h_d | markov_w1[prev_d]] with S3b-confirmed pairing (prev_0=seed,
  prev_d=props[d-1]); markov_w1 = OFFICIAL f32 (== bit-identical dequant
  of deployed q8_0, S5) -> features match the engine EXACTLY; no bias;
  rebuilt keep-pass cross-asserted vs slab meta before training. VAL
  (identical hashed split, 8,992 rows): pooled AUC .8523 vs official
  replay .8360 (+1.63pp) and confcal1b .8453 (+0.70pp); per-slot
  .8841/.8493/.8245/.7812 BEATS OFFICIAL ON EVERY SLOT (.8775/.8352/
  .8114/.7671); baseline .5448; ECE .0115; coverage .45-gate .8883
  (11.2% of rows cut); gate-region n=1611 pred .4606 obs .4854
  (under-prediction now 2.5pp - watch item for the AB gate). Export:
  w_full_1x4352 (1,4352) b=0, markov half ALL 256 nonzero (norm .64);
  saved scores == engine-form recompute to 5.3e-5 (f16 feature storage).
  Review CLOSED ~06:5x PASS (all claims reproduced: independent pairing
  re-derivation from raw parity bytes incl. hdr.seed=props[0] trap
  observed; repack proof on the arm-B build 4,563 changed bytes in the
  4,624 B window, dequant max|delta| 6.37e-4, AUC through DEQUANT bytes
  .8523 with 6/8992 gate flips - quote deployed eval from dequant; CIs
  cover val-sampling noise only, single-pid single-day slab -> cross-era
  claim is provisional-pending-live). AB-GATE-RULING: ARM B = confcal2,
  blade file /Volumes/FireCuda520/ds4-train/repack_test/
  support_AB_confcal2.gguf sha 5c0286dd..701013 (reviewer-built,
  deterministic repack confirmed bit-identical to /tmp build); fallback
  confcal1b sha 3365e6b4..72adc6cf; official 0807a67f.. unchanged
  re-verified after proof runs.
- S5: tools/repack_dspark_tensor.py (in-place size-preserving q8_0->q8_0 /
  f32->f32 swap on COPY; refuses --out==--in). Proofs on official support
  gguf (sha 0807a67f..., NEVER written): (1) roundtrip read+rewrite orig
  bytes -> output sha256 == input PASS; (2) walker offset cross-check:
  conf tensor abs 4,077,251,392 (rel 4,077,245,696 + data_start 5,696),
  dequant BIT-IDENTICAL vs mtp_nonexpert_f32.npz; (3) confcal1b repack:
  4,566 changed bytes ALL inside 4,624 B window, sizes equal, dequant
  max|delta| 6.8e-4, markov half exactly zero; (4) functional: val rows
  rescored through repacked bytes AUC .8453 == .8453, gate-.45 flips 2/
  8992 (.02%), max|score delta| .0027 -> q8_0 requant calibration-silent,
  no f32 offset-rewrite needed. Artifact: /Volumes/FireCuda520/ds4-train/
  repack_test/support_confcal1b.gguf (blade-local, NOT committed).
- S6: AB-PROTOCOL v3 CLOSED (review PASS_WITH_MINORS ~06:5x). Arms: A =
  official (boot via pristine script), B = confcal2 (patched-script copy,
  DSPARK_PATHS[1] -> support_AB_confcal2.gguf sha 5c0286dd..701013);
  order A->B->A->B, 2nd cycle scored; P0 probe gate (>=1 stats line,
  cycles>0, tool prompt) BEFORE scoring; hist parse fixed vs real
  k:count format (17 bins); decision rule v2 + coverage = DSpark
  ENGAGEMENT definition + keep-.45 conservative-gate watch. bench script
  E2E-parse-tested offline; NEVER executed against live engine tonight.
- R: CLOSED 10:07 - operator-reported stall (swap thrash: 282s request,
  avg 1.10 t/s vs chunk 33, swap 2.9 GB used; stack showed clean
  __accept-wait, NOT hung) -> restart-collect. Old pid force-killed
  (in-flight traffic); first boot raced kernel teardown of 60838 and
  died on stale /tmp/ds4.lock flock; second start CLEAN (PID 73479).
  Verified NEW-WAY: log line 1 'DSpark support model detected: …Vision-
  Exp… missing=0 invalid=0' + ps flags --dspark --mtp-exact-sampling
  --dspark-confidence (0.45) + terminal banner 'support: …, confidence:
  0.45, exact: 1'. NO probes (operator directive - active jobs
  reconnect themselves: 117k-ctx session re-prefilling ~400 t/s within
  90s, kv cache stored lines flowing). Evidence: /tmp/ds4_stall_
  evidence_20261104.log + /tmp/ds4_stall_sample_20261104.txt. Note for
  the runbook: stop-with-inflight-traffic -> expect force-kill + stale-
  lock race; if 'failed to start', just start-collect once more after
  the old pid fully dies.

## DECISIONS
### D-S0 (FINAL 23:15 - amended per independent review; probe repro 241/241)
1. CONF BCE LABELS - THREE-CASE CAP RULE (consumers use THIS text):
   w = min(dlen, n_label-1). Scan d < w for first mismatch k_lab.
   a) mismatch at k_lab < w: label 1 for d<k_lab, 0 at k_lab, MASK d>k_lab.
   b) no mismatch, w == dlen: all 1 on [0,dlen); MASK d>=dlen; NO 0 label.
   c) no mismatch, w < dlen: all 1 on [0,w); MASK w..dlen-1; NEVER label 0.
   EXCLUDE dlen==1 cycles (fake_argmax: props[0] = target's own argmax, not
   a draft - ds4.c:80533-80537). Oracle deterministic: replacement resamples
   with the draft token zeroed (ds4.c:84344-84354) => within the evaluated
   window label-match <=> accept, first-mismatch <=> first reject. Caveat:
   rare eval clamps (max_tokens/ctx/EOS ~84402-84432) shrink the window
   AFTER the parity write; masking covers most, tolerate rest. NEVER
   gap-derived accept-runs: A5 invalidates gap-as-oracle in exact era
   (k_lab=0 -> gap 2 in 74%, gap 1 once file-wide; seed era agrees but is
   irrelevant for conf).
2. DLEN SEMANTICS: conf-prefix-truncated window (ds4.c:80493-80513); props
   ZERO beyond dlen (76189-76191, 100% audited - A3's 0.0% is the zero-fill,
   not reject physics); CONF ROWS beyond dlen are STALE NONZERO - mask by
   dlen everywhere. Acceptance empirically dlen-correlated (slot0 match
   .707@dlen2 -> .903@dlen5): build NO dlen-agnostic priors.
3. MARKOV LABELS era-AGNOSTIC: writer dumps checkpoint.v[pos+k] = committed
   stream (ds4.c:76298-76311, lineage-rewind guard); seed==labels[0]=1.0,
   chain 100% both eras. Era rule (pid>=53508) for CONF labels only.
4. EXACT-ERA PER-SLOT RATES (S2.5 RECONCILED - one dataset, TWO stats, do
   not mix): (i) TRAINER-SEEN BCE row rate = P(commit | UNMASKED
   (BCE-scored) row at slot d) = .802/.744/.728/.735 (dens
   30205/24217/14059/7254) <- use THIS for class weights / bias init, it is
   what gradients actually see (deep slots exist only in long windows ->
   over-represent acceptance); (ii) UNCONDITIONAL accept-chain
   P(a>=d+1 | a>=d) = .802/.744/.568/.521 (pool-wide, from k_lab, run
   length a = k_lab if >=0 else w) <- headline accept-quality stat. Eval
   harness selftest derives+asserts BOTH from the slab. Stored-row positive
   density .802/.597/.494/.430; uncond chain P(>=d) .802/.597/.339/.176.
   Historical: reviewer's S0-review .465/.238 set was an off-by-one
   artifact (superseded in S1a review); marginals .807/.641/.556/.512
   contaminated-by-design. Seed-era .978/.955/.934/.920 = opportunistic era,
   conf labels NOT used.
5. ENGINE QUIRK keep (mechanism verified): full-window commit = accepts+1
   (87-90% at kl=4); reject replays replacement + successor sample =
   accepts+2 (74% at kl=0) - verify2-always alternative falsified (it would
   predict constant +2; full-match cohorts commit +1). Chain positions and
   per-slot labels unaffected; mirror gate inputs unaffected (control 100%).
PLUS from review: S1a joins by INDEX 1:1 (recs<->parity same record index,
same ok-gate ds4.c:80681-80683, n_par==n_recs exactly) + pos assertion, NOT
pos-unique. Probe A4 gap_accept column formulaic at d=0 (annotated in code).

## PROGRESS LOG
- 22:03 plan committed; night run starts.
- 22:1x S0 probes: day-13/pid53508 (exact) + day-12/pid50920 (seed-batch)
  via new dspark/probe_labels3c.py; A1-A5 legs; engine code read confirms
  point-accept. Report .codebase-memory/probe3c_s0_20261103.json.
- 22:55 S0 decisions frozen -> INDEPENDENT REVIEW before S1a.
- 23:1x S0 INDEPENDENT REVIEW (fresh ctx): VERDICT REWORK on decision text
  only (probe correct, 241/241 repro). Blockers: cap-case mislabel (would
  inject 12-72% false negatives), marginal-vs-joint prior confusion,
  dlen==1 fake_argmax exclusion, conf-rows-stale-beyond-dlen mask,
  index-1:1 join for S1a. All folded into D-S0 FINAL above. S0 DONE.
- 23:4x S1a INDEPENDENT REVIEW (same reviewer, continued session):
  PASS_WITH_MINORS. Builder stats CORRECT; reviewer's own S0-review priors
  (.465/.238) ruled an off-by-one artifact (cap-match cohorts) - D-S0-4
  amended to masked-BCE rates .802/.744/.568/.521 (stored density
  .802/.597/.494/.430; uncond chain .802/.597/.339/.176). Full-array
  byte-exact recompute + spot audits PASS; f16 keep. Minors fixed in
  labels3c.py (multi-pair cyc offset, hard-fail join, bucket fix, era
  guard pid>=53508 with --allow-any-era); slab rebuilt identical. S1a DONE.
- 23:5x S2.5 INDEPENDENT REVIEW: PASS_WITH_MINORS + one builder factual
  error caught: "weights/scores never persisted" FALSE -
  trainer/v1_head_cos32clean2.pt EXISTS (my compound zsh glob misled me).
  Milestone .7418/.7933 = replayable but NOT gate-valid (streak-label
  target, D-S0-disqualified). S3 queue gate FIXED before S3 (stale
  >=.7418 line replaced: beat positional-prior baseline + calibration).
  eval3c minors fixed: unmasked-row wording, --expect/--expect-chain
  parameters (no frozen day-13 constants), chain leg ASSERTED, case-c
  run-length uses w, dead var, baseline annotation. Re-selftest PASS with
  both expect lists; 33/33. S2.5 DONE (review closed).
- 00:3x S3 INDEPENDENT REVIEW (confcal1): PASS_WITH_MINORS. Split provably
  conv-disjoint (37 val/340 train convs, empty intersection), no feature
  leakage (rows = parity base+32, audited in S1a), numbers recomputed
  independently. MAJOR CAUGHT: trained BIAS not deployable - engine conf
  is pure matvec_any over [h4096|markov256], no bias anywhere, validator
  pins [4352,1] -> S5 would silently drop b=-0.0113 (~18-20/8864 val gate
  flips). Fixed: conf3c.py --bias flag (DEFAULT OFF = deploy-faithful);
  refit confcal1b w/ hashed conv split (creation-order %10 skew fixed) +
  in-run val-pop baseline (.5448) replacing frozen .5457. Result: pooled
  .8453, ECE .0109, gate-region .4666 vs .4824 - PASS. Reviewer also
  ruled q8_0 requant acceptable (blockwise ~1% rel err << ECE).
- 00:5x S5 repack tool built + proof battery (see SLOTS:RESULTS S5).
  Roundtrip sha256 identity + in-place confcal1b swap: all four proofs
  PASS. INDEPENDENT REVIEW DISPATCHED (see next log entry when closed).
- 01:1x S5 INDEPENDENT REVIEW: PASS_WITH_MINORS, zero blockers/majors.
  Reviewer independently reproduced EVERY proof (4,566-byte diff locality,
  geometry, dequant bit-identity, rescore .8453/.8453/2 flips/.002730) +
  confcal1b closure (scores==sigmoid(X@w) to 1.8e-7, hashed split exact).
  ENGINE-DEQUANT-RULING: tool convention == file convention PROVEN via
  byte-idempotence on the official tensor; safe to deploy in-place.
  Minors fixed: q8_0 golden tests PINNED (tests/test_gguf_dq.py: synthetic
  layout golden + half-step bound + idempotence-on-official-bytes);
  prove-roundtrip/repack mutex; repack specs validated BEFORE the 6 GB
  copy (orphan-copy bug); dead DTYPE_NAMES removed; docstring now honest
  q8_0-only. Note: gguf_dq.py has NO q8_0 port (my premise was wrong;
  reviewer substituted stronger gold standard). S5 DONE.
- 02:2x S2 legs ALL GREEN: selfcheck PASS (gradcheck 8/8 f64; torch-vs-
  numpy conf cos min .99999988 over 30 rows, empty+warmed ring) and
  GATE A through torch IDENTICAL at 6 dp to the frozen numpy baseline
  (.989772/.977642/.967313/.953006/.927025; conv4 24 targets 19 hits)
  incl. today's numpy rerun control. INDEPENDENT REVIEW DISPATCHED.
- 02:3x S1b INDEPENDENT REVIEW: PASS_WITH_MINORS. Highest-stakes check
  clean: conf-mode rebuild byte-identical to shipped S1a slab (refactor
  safe). Full provenance audit 459,365/459,365; quarantine per-file exact;
  ledger corrected 266,952->264,932; era-agnostic claim holds empirically
  incl. seed-batch pids. S4 consumption rules pinned (see SLOTS:RESULTS).
  S1b DONE.
- 04:0x-04:2x S4 JOINT RUN s4full1 COMPLETE in 26 min (48 s/epoch, full
  32-epoch curve): plateau top1 .4652 / top3 .6110 vs anchor .4524/.5950;
  PAIRED vs anchor head on same day-3 holdout +1.31pp CI [+0.80,+1.82]
  p<1e-5 -> beats .4529 BAR with proper stats. delta_from_h .3275 new max.
- 04:4x S6 INDEPENDENT REVIEW: REWORK - BLOCKER DSPARK_MODEL lever dead
  (ds4-server.sh:59 clobber + start_server override: arms would be
  identical); --since windowing silently dead (log ts has no year);
  bench hit wrong port (8002=qwen no-DSpark; collect=8001); stats env
  never set in history. ALL FOLDED into v2 (patched-script-copy boot
  lever, byte-offset windowing, 8001 + /v1/models discovery, stats env
  both arms, P0 probe gate, tool prompt, tok/s demoted, offline
  re-baseline). Re-review dispatched.
- 04:4x S2 FIX RE-VERIFY GREEN: gradable smoke PASS (T3/T4 grads nonzero,
  RSS 6.99 GB), selfcheck PASS (gradcheck 8/8), GATE A rerun 6-dp
  IDENTICAL to frozen baseline. S2 DONE.
- 04:5x S4 INDEPENDENT REVIEW: PASS_WITH_MINORS. Every number reproduced
  (plateau from anchor's own json; paired 238/140 exact; sign p=5.3e-7;
  leak-bound <=.107pp differential; codes8 extended to day-8/12).
  Ledger MAJOR-for-text fixes applied (top3 basis .5944 plateau;
  'holdout-sampling noise' wording; no boundary claim; cos32clean3
  declassified as recipe control). Replication run s4full2 (seed 1)
  launched to close the seed-variance minor.
- 05:2x S4b CONFCAL2 joint conf head (engine-form 4352, markov features):
  val .8523 > official .8360 (+1.63pp) > confcal1b .8453 (+0.70pp),
  beats official on ALL 4 slots, ECE .0115. See SLOTS:RESULTS S4b.
  Review + S6 v2 re-check dispatched together.
- 05:5x S4 SEED REPLICATION (s4full2, seed 1): plateau .4646/.6103,
  paired vs anchor +1.17pp CI [+0.67,+1.71] p=9.2e-6; vs s4full1
  -0.13pp p=0.28. Two seeds both beat anchor; seed band ~0.1pp.
  Review MINOR 2 closed. trainer/TRAIN_V1_s4full2.json.
- 06:5x S4b + S6 RE-CHECK REVIEW: S4b PASS (every number reproduced,
  pairing byte-verified, repack end-to-end proven); S6 PASS_WITH_MINORS
  (new MAJOR: bench hist parse vs real k:count format -> FIXED inline:
  pair regex + parse_len_hist 17 bins + hist_add widened + loud garbage
  error; E2E test vs engine-shaped fprintf line PASS). AB-GATE-RULING:
  arm B = confcal2 (blade support_AB_confcal2.gguf sha 5c0286dd..701013,
  deterministic-repack confirmed), confcal1b fallback; keep .45,
  conservative-gate watch wording -> protocol doc v3. Doc folds done:
  AB-PROTOCOL v3 (arms, shas, engagement-coverage, watch item, hist
  format), official3c.py parity-seed trap comment, ledger S4b/S6 CLOSED,
  S4 claim upgraded to two-seed wording. Fold-over review (commit
  66d12ae) PASS ~07:0x: hist decode verified in BOTH field orders, p
  ruling: builder's p=0.28 correct, reviewer's 1.0 was a formula floor;
  2 minors fixed (A1b file name, *_PARTIAL.json gitignore).
- 07:2x CLOSE-OUT REVIEW (night report + minors commits):
  PASS_WITH_MINORS. Claim audit: EVERY number traces (confcal2 .8523
  set, shas incl. official full shasum re-verify, s4full set, 'beyond
  seed noise' + provisional-pending-live wording compliant); 'engine
  NEVER stopped' PROVEN (pid 60838 lstart Nov-3 20:32:11, no new log
  snapshot, live traffic same pid). MAJOR 1 '36/36 goldens' -> RE-
  RUN SETTLED IT: suite 36/36 PASS (07:2x), goldens FILE = 6 tests /
  3 pinned - both stated precisely in NIGHT REPORT item 3. MAJOR 2
  'exact: 1' NOT in log/ds4.log (terminal-banner-only string; log grep
  unsatisfiable) -> R procedure rewritten in policy + SLOTS:R + PROGRESS
  LOG 07:1x + AB-PROTOCOL (verify = log line 1 'DSpark support model
  detected' + ps -o command flags --dspark --mtp-exact-sampling
  --dspark-confidence 0.45; still NO probes). MINOR: review-history
  wording now credits the S6 REWORK cycle. RESTORE-GO: restart-collect
  safe as recorded; no repo state needed changing.
- 10:0x R INCIDENT + RESTORE EXECUTED: operator flagged stall -> diag:
  pid alive in __accept (stack sampled), one tool-call request took
  282.17s (chunk ~33 t/s vs avg 1.10 = inter-chunk stalls), swap 2.9 GB
  / free 18% -> SWAP THRASH, not hang; recovered pre-restart (the 282s
  request completed 10:01:59). Evidence /tmp/ds4_stall_{evidence,sample}_
  20261104.*. restart-collect: stop needed FORCE KILL (in-flight
  traffic; runbook says stop only when quiet - operator signal took
  precedence); first boot attempt FAILED on stale /tmp/ds4.lock flock
  (kernel still tearing down 60838's tens-of-GiB mappings when the
  script polled) -> second start-collect CLEAN: PID 73479, terminal
  banner 'support: official, confidence: 0.45, exact: 1', log line 1
  'DSpark support model detected … missing=0 invalid=0', ps flags
  confirmed. NO probes per operator; traffic self-resumed (7 chat lines
  + 117k-ctx session re-prefill ~400 t/s + kv cache stored within 90s).
  LESSON: force-kill restart may need a SECOND start-collect if the
  lock-holder teardown races readiness. Queue S0->R now ALL CLOSED.
- 07:1x R: engine NEVER STOPPED tonight (preempt/seal path never
  triggered - all jobs fit the memory envelope; PID 60838 serving
  continuously, day-14 collection still live/UNSEALED). Restore held
  per operator ("window open past 07:15, will signal"). On signal:
  ./ds4-server.sh restart-collect -> verify log line 1 'DSpark support
  model detected: …Vision-Exp-DSpark-support.gguf (…invalid=0…)' +
  ps -o command of new pid shows '--dspark --mtp-exact-sampling
  --dspark-confidence 0.45' (NO grep for 'exact: 1' - terminal banner
  only, close-out MAJOR 2; NO probe requests);
  day-14 seal = morning follow-up (stop->td_validate->seal->manifest->
  rsync->loops --audit), NOT part of R.

## NIGHT REPORT (final, ~07:15 20261104)
MISSION: beat the official DSpark conf head via the 3c lane - ACHIEVED
at the offline level; deployment decision deferred to the morning A/B.
1. CONF HEAD: confcal2 (engine-form linear-4352 over
   [h_d | markov_w1(prev_d)], no bias) val AUC .8523 vs official .8360
   (+1.63pp CI [+1.15,+2.11]) and confcal1b .8453; beats official on
   ALL 4 slots; ECE .0115; quantization-silent through deployed q8_0
   (.8523 through repacked bytes, 6/8992 gate flips). ARM B READY:
   support_AB_confcal2.gguf sha 5c0286dd..701013; confcal1b
   (3365e6b4..) fallback. Cross-era claim = provisional-pending-live;
   generalization evidence = single-pid single-day conv-disjoint.
2. DRAFT HEAD (milestone evidence only, NOT deployable): s4full1/2
   (two seeds) beat the .4524 anchor head on the frozen day-3 holdout:
   +1.31pp CI [+0.80,+1.82] and +1.17pp CI [+0.67,+1.71], runs
   indistinguishable from each other (-0.13pp p=0.28) -> beyond seed
   noise; max delta_from_h .3275. Engine keeps official w1/w2.
3. TOOLING: Gate A torch mirror port green (gradcheck 8/8, Gate A rerun
   6-dp identical); repack tool + q8_0 goldens green (test_gguf_dq.py:
   6 tests, 3 q8_0 goldens pinned; FULL trainer suite 36/36 re-run
   07:2x morning - the '36/36' in early drafts was the suite count,
   not the goldens file); bench
   client parse-complete vs real engine stats format; AB-PROTOCOL v3
   pre-registered (arms, shas, P0 probe gate, engagement-based
   coverage, keep-.45 conservative-gate watch).
4. HYGIENE: official GGUF sha 0807a67f.. unchanged (verified after every
   proof run); day-3 holdout frozen; NO live A/B executed, NO probes;
   corpus/blade artifacts never committed; secrets never echoed; all
   slots independently reviewed before closure (closures all
   PASS/PASS_WITH_MINORS; one REWORK cycle - S6 v1 - resolved in place;
   zero BLOCKERS survived the night).
MORNING TODO: (a) R restore on operator signal + verify (log line 1
'DSpark support model detected' + ps flags; NOT a grep - see policy);
(b) day-
14 seal when convenient; (c) run AB-PROTOCOL v3: P0 probe gate FIRST
(DS4_DSPARK_STATS=1 both arms, tool-bearing prompt, cycles>0), then
A->B->A->B; (d) if B advances: iterate conf training on growing pool +
engine-form head seeded from s4full recipe as NEXT research item.
  during user agent turns (342k-ctx session). Cause stack:
  (1) disk-KV cache 254G vs budget 262G -> constant evictions ->
      per-turn ~140k-token re-prefills (stored 344,064 but rehydrate
      stopped 204,800 then 200,704 = BOUNDARY REGRESSION); (2) under
      pressure single GPU prefill chunks ran minutes (sample:
      metal_graph_prefill_chunked_range waitUntilCompleted, GPU 90%+,
      NOT a wedge - my first all-threads-idle read was a grep artifact,
      corrected by user's GPU observation). MITIGATION: restart with
      KV_SIZE=524288 (PID 3106, budget line confirmed, nothing wiped,
      flags+banner verified). OPEN TEST (built into next session turn):
      if M in 'prefill chunk N/M' ~140k with a fresh cache = user's
      EXACT+DSpark KV-lineage hypothesis CONFIRMED (draft/verify
      token-stream rewrite breaks token-text-sha prefix reuse on tool
      traffic) -> DSpark workstream issue, candidate for ADR/DS4FORK.
      Runbook note: graceful stop IMPOSSIBLE mid-prefill (drain times
      out -> force kill; KV self-heal absorbs it) - stop only in gaps.
- 14:0x ROOT-CAUSE (user hypothesis EXACT+DSPARK CONFIRMED at
  mechanism level): stall windows = sample-proven inside
  generate_job_inner -> ds4_session_sync_internal (ds4.c:79564) ->
  metal_graph_prefill_chunked_range waitUntilCompleted, 715/715
  samples, GPU 90%+: an UN-INSTRUMENTED full-prompt rebuild pass
  runs EVERY turn before generation (no 'prefill chunk' lines =
  looks exactly like a hang; morning's 282s request = same pass
  inside-request). Why full every turn: stored KV past ~200k is not
  re-hydratable despite existing (stored 344,064; rehydrate boundary
  REGRESSED 204,800 -> 200,704) = prompt-prefix token-text-sha keys
  stop matching past the first DSpark draft-accept windows under
  exact-sampling (EXACT disables seed-batch direct-commit at
  ds4.c:62051 -> accepts go replay-fallback 84694; exact rewinds +
  resamples at sampling-mode boundaries, comment ds4.c:58719 -
  stalls land exactly at DSML_START boundaries). ENGINE BUGS TO
  FILE: (1) instrument sync_internal rebuild passes (progress lines
  + a warning when >N s); (2) EXACT+DSpark lineage: per-turn
  degradation of disk-KV prefix reuse on tool traffic; interim user
  mitigation: big tool-heavy sessions run clean WITHOUT --dspark (or
  without exact-sampling) until fixed. KV_SIZE=524288 stays (removes
  secondary pressure; cache untouched).
- 14:0x REPRO PINNED LIVE (hypothesis -> fact): post-restart turn 1
  rehydrated deep (M=16,910, prompt done 104.9s; turn 2 M=434, 2.9s -
  rehydrate ~327k, evictions 0). Then ONE DSML_START tool-turn
  (13:51:58 gen=450) collapsed the boundary again: next turn
  re-prefilling 144,692 from **200,704 - identical pin as pre-restart
  143,886-from-200,704** (13:45). => budget/restart irrelevant (as
  expected - state is on disk); a DSpark draft-accept turn at a mode
  boundary poisons prefix-key lineage from ~200,704 onward, EVERY
  subsequent turn pays the rebuild. Session is permanently in the
  pathological regime: stop using it for interactive work until ds4
  fix; fresh sessions stay clean until their first big draft-accept
  window. Silent phase has TWO shapes, both inside session_sync_internal:
  unlogged pass at ds4.c:79564 + the logged chunked rebuild at 79489.
- 14:2x ROOT-CAUSE STATUS CORRECTION (honest narrowing): the 14:02
  "pin 200,704" evidence is CONTAMINATED by the user's compaction
  (compact rewrites history -> rebuild-from-pin expected, not bug
  proof). CLEAN evidence = pre-compact pair only: FULL 344,064 store
  existed (12:45, no delta chaining, budget healthy) yet 13:11 AND
  13:45 turns both stopped matching at ~204,800 (same pin twice).
  Eviction/delta-chaining RULED OUT (full file, 38 s old). Survivors:
  (A) prompt re-render drift around token ~204,800 - engine's own
  warning names the suspect class: '4 message(s) first_role=tool
  contain structural syntax, rendered verbatim' (raw DSML round-trip
  in tool messages; DS4_KV_DEBUG rejects stores with named reasons
  e.g. tail-render-mismatch - ds4_server.c:12850s); EXACT+DSpark =
  CORRELATION via tool traffic, not proven cause; (B) engine-side
  lineage bug (exact-mode commit/rewind) - no mechanism found yet
  (rewind rewrites tail pre-emission only). PLAN: DS4_KV_DEBUG=1 on
  next collect boot (env-only); compacted fresh session = control
  (stall recurs -> B patch lineage; clean -> A client render drift);
  patch regardless: session_sync rebuild progress + 'prefix
  divergence at token N' log w/ both shas. Question pending user:
  does the agent/proxy repair/rewrite older messages?
- 14:3x CONTROL RESULT - hypothesis v3 (canonical-vs-raw DSML drift)
  FALSIFIED on the fresh compacted session: 3 consecutive tool-call
  commits (14:30:13, 14:31:17, 14:31:46) each followed by FULL
  rehydrate (406/468/209-tok micro-prefills, 1.2-2.0s, continuous
  ctx) - raw_tool_text=1 round-trips through
  append_dsml_tool_calls_text's raw-replay branch (ds4_server.c:3101)
  byte-stably. The 344k drift therefore requires a session-specific
  trigger. Live candidates: (i) OSCILLATING client-side re-render of
  specific old messages (user: opencode1 rewrote; opencode2 'not sure
  if it still does'; same-pin-twice + clean turn between fits an
  alternating A/B rendering of one message); (ii) engine-side era
  boundary in that session's checkpoint chain (lived through
  60838->2109 restart + macOS update). SECOND PHENOMENON logged:
  mid-DSML decode collapse on the fresh session (16 tokens in 176s,
  chunk 0.09 t/s, inside DSML_START..DSML_END, self-recovered) -
  candidate: EXACT disables seed-batch direct-commit (ds4.c:62051)
  -> per-block replay-fallback re-prefill over the tool span;
  measurable via DS4_DSPARK_STATS=1 (replay_fallbacks, prop_* timers).
  INSTRUMENTATION COMMITTED (925c2cc, reviewed PASS_WITH_MINORS):
  partial-stitch forensics in kvstore. NEXT operator-signaled boot:
  DS4_KV_DEBUG=1 + DS4_DSPARK_STATS=1 (both env-only). On next drift
  event the log names the first divergent BYTE + both windows ->
  client-vs-engine settled by data, not archaeology.
- 14:4x STALL BRANCH PINNED + INSTRUMENTED RESTART: live DSML-stuck
  sample (1691/1691 in ds4_session_sync_internal+2528 -> chunked
  prefill) maps to ds4.c:79547 branch: prompt cannot EXTEND the
  session's in-memory checkpoint (79520 push-loop) -> session reset
  (79546) -> FULL re-prefill metal_graph_prefill_chunked(prompt->len).
  Arithmetic fits all three sightings: 176s@90k, 2.5min@90k live,
  ~10-12min@144k. Disk-KV rehydrate is NOT the failure (209-468 tok
  logged deltas after). Question now sharp: why does a DSML-committed
  turn make the in-RAM checkpoint un-extendable while the on-disk
  prefix matches? Restart EXECUTED graceful (no force-kill this
  time) -> PID 4051 with DS4_KV_DEBUG=1 + DS4_DSPARK_STATS=1 +
  KV_SIZE=524288, all env verified in ps eww; missing=0 invalid=0;
  flags+banner exact:1. NEXT occurrence prints divergence byte +
  windows + replay_fallback counters. Follow-up patch candidate:
  one log line at ds4.c:79546 naming WHY the extend failed (token
  index where prompt != checkpoint) - completes the picture without
  byte-forensics.
- 14:5x ROOT CAUSE COMPLETE (user hypothesis EXACT+DSpark PROVEN,
  engine-side, code-localized end to end):
  1. EXACT sampling resamples at every sampling-mode-boundary crossing
     inside a verified block (ds4_server.c:16723: next_greedy !=
     greedy_tool_syntax -> resample=true) - fires constantly around
     DSML tool payloads (greedy structure vs temperature-sampled args).
  2. -> server_generation_rewind (ds4_server.c:16736, the exact stack
     frame in ALL four live samples): fast path
     ds4_session_rewind_speculative (ds4.c, snapshot restore) BAILS
     when the boundary pos falls OUTSIDE the last verify window
     (pos < d->verify_start) - long argument spans advance the window
     past the block start; DSML turns hit this constantly.
  3. Slow path: ds4_session_rewind + rebuild sync of the truncated
     prefix -> ds4_session_sync_internal RESET branch (ds4.c:79564)
     -> FULL GPU re-prefill of the entire kept context (the snap0
     comment at ds4.c:58719 warns of exactly this cost).
  LAW: stall_seconds ~= context_tokens / ~350 t/s: 83k->176s,
  90k->2.5min, 107k->302s, 117k->282s (morning), 144k->742s,
  344k-era pins -> 10-12min EVERY DSML turn. Same-pin-twice explained:
  pin = session's first DSML commit position; oscillation = turns
  whose boundary happened to stay inside the verify window (clean).
  Disk-KV was NEVER the problem; my v3 client-drift and v2 eviction
  theories both falsified by this. FIX MENU (owner's call):
  (1) extend spec-snapshot coverage so rewind_speculative succeeds
      across long DSML spans (verify windows per payload block);
  (2) truncation-aware sync in ds4_session_sync_internal: when
      prompt is a PREFIX of valid checkpoint, truncate instead of
      reset+replay (precedent: ds4_session_glm_cap_dense_cache);
  (3) observability NOW: log 'rewind slow-path: boundary pos=%d
      verify_start=%u rewind_end=%u -> full rebuild of %d tokens' +
      progress via the existing rebuild_progress plumbing (currently
      silent for minutes -> false-outage reports all day).
  ANSWER to 'do we need DSpark for collection?': YES (recs/parity ARE
  DSpark cycle logs, EXACT = mirror-valid era) -> fix ds4 (2)+(3) is
  the pragmatic pair; (1) is the real win.
- 15:0x HANDOFF: fix workstream SPLIT OUT per operator -
  PLAN-DSML-REWIND.md (new agent owns M0 observability -> M1 truncation-
  resume -> M2 snapshot coverage -> M3 upstream; guardrails: live engine
  untouchable, private instance port 8011 + isolated DS4_LOCK_FILE/
  TRAIN_DUMP, synthetic cycles NEVER into real corpus). ISSUE-EXACT-DSML-
  REWIND-REBUILD-20261104.md = full evidence + repro recipe + fix law.
  This (3c) lane stays on collection/training/A-B; stalls are latency-
  only (exactness preserved) so tonight's plan is unaffected. AGENTS.md
  workstreams line added.
- 15:1x QUEUE REVIEW (flash lane) PASS_WITH_MINORS, corrections folded:
  MAJOR attribution fix - the cited bail clause pos<verify_start/
  rewind_end is DS4.1-ONLY; live session class qwen4 bails via
  dspark_rewind_recent gates (ds4.c:89113) + snap exact-miss (89216);
  which clause fires is UNPROVEN -> M0 must log the field set and
  classify from live lines before M2 sizing. Also fixed: parity-disable
  is UNSET DS4_TD_PARITY (MAX=0 coerces to 4096); 'no log' nuance =
  progress plumbing exists but emitter unwired on rewind path; law
  table honest 195-600 t/s spread (282s@117k = 415, not 331); M1 chunk-
  alignment tolerance protocol + cost budget added; second silent route
  (kept<ntok && !resample) must print resample=. RETRACTION for 14:4x:
  during the stall pattern DS4_KV_DEBUG stitch lines are NOT expected
  (disk rehydrate full-hits; the instrument for THIS bug is
  DS4_DSPARK_STATS replay_fallbacks + the future M0 line). Tree clean
  at 2a88826 + this fold = handoff base for the PLAN agent.
- 16:2x SECOND CORRECTION (the PLAN agent was RIGHT, my reviewer was
  wrong): 'qwen4 branch' attribution re-verified against source and
  RE-RECTIFIED. Truth: snap0/1/2 machinery is double-gated
  (#ifdef DS4_HAS_QWEN4_GPU + ds4_session_is_qwen4 = QWEN4_EXP family -
  the 8002 qwen profile) and the ds41 branch is DEEPSEEK41-gated; the
  STALLING engine is DeepSeek-V4-Flash = DEEPSEEK4 family -> BOTH cited
  mechanisms are DEAD for it. Live fast path = unconditional tail
  ds4.c:89197 -> dspark_rewind_recent (89113, family-agnostic session
  fields from dspark_rewind_begin 64178). AMPLIFIER newly identified:
  DEEPSEEK4 has NO state_ok=true fallback in ds4_session_rewind (only
  qwen4 + glm branches set it) -> every dspark_rewind_recent bail
  (missing logits | end!=checkpoint.len | pos outside window | span>16
  =DS4_DSPARK_MAX_BLOCK_SIZE 2914) becomes a FULL rebuild. M0 field
  list corrected to the live generic set; fix option 1 re-scoped to
  build a family-correct restore via the existing dspark_rewind_frontier
  buffers. Spine (resample 16727 -> rewind 16736 -> RESET 79564 -> law)
  NEVER affected. Lesson logged: reviewer folds need the same
  adversarial check as builder claims - caught by the NEXT reader.
- 20:4x PLAN-DSML-REWIND agent session 2 - M0 LANDED, ROOT CAUSE FOUND,
  FIX DEPLOYED. Owns entries above (14:5x-16:2x) as background only; every
  claim below re-verified against source + live logs by me.
  M0 (b0b4966, reviewed 2x): gate condition extracted verbatim into
  dspark_rewind_gate(), exposed read-only as ds4_session_rewind_reason();
  server logs reason/requested/truncate/window/kept_tokens/est_s BEFORE the
  rebuild + elapsed_s/rate_tps AFTER + end_state= (which of 7 sites last
  cleared or armed the window). Zero behavior change.
  LABEL (live, first real rebuild): reason=no-verify-window, window=212547..0
  with logits=1 prefixes=4 checkpoint_valid=1 and requested==window_start -
  i.e. the restore state EXISTED and only the liveness flag was dead.
  Measured: 212,546 tokens in 601.4s = 353 t/s (est_s was 607; rc=2 = client
  had already disconnected, so the rebuild produced NOTHING). Second rebuild
  63s later, same turn. Census from ordinary logs: 24 rebuild chunks, 150 GPU
  min lost, 29.9% of pre-fix deep turns over 64s.
  ROOT CAUSE: ds4_session_eval_internal() cleared dspark_rewind_end on EVERY
  token, and the exact-sampling resample rewinds then replays its boundary
  token through that same function -> the first resample of a turn strands
  every later crossing. Explains the per-turn census ladder AND why my
  synthetic repro produced zero bails. NOT the verify-tail theory in the
  entry above: ruled out by the session's own counters (replay_fallbacks=0,
  verifier_unavailable=0, full==direct_full).
  FIX (265679b + 1ea1fec, DS4_REWIND_RESUME default ON): one predicate
  dspark_rewind_covered_after_move() shared by the gate AND
  ds4_session_restore_speculative_prefix() (also the TP worker's check).
  Review history matters: first attempt relaxed ONLY the gate -> SMASH
  (restore re-checked equality, so nothing was served while the log claimed
  reason=ok); fixed by sharing the predicate -> PASS_WITH_MINORS. Env OFF is
  decision-identical to the prior commit (verified: gate, restore, span check,
  eval site). TP: both ranks need the same env value.
  DEPLOYED: live engine PID 9304 since 20:13 (operator-authorized window;
  argv byte-identical, all 14 original env vars preserved, parity dir intact
  for the conf-head A/B). Interim: 0 rebuilds, 9 rewind events all
  reason=ok end_state=armed, deepest served 226,719 tokens. p=0.02 vs the
  >64s class but p=0.21 vs the strict >300s class - NOT yet conclusive;
  ~25 clean deep turns or the fixed-seed A/B closes it. Rollback:
  DS4_REWIND_RESUME=0 ./ds4-server.sh restart-collect.
  FOR THE 3c LANE: replay_fallbacks cannot see a stranded window (the
  clearing site is invisible in stats) - use the M0 line's end_state= instead.
  Collection correctness is unaffected either way (exact semantics preserved;
  this is latency only). Docs written back: PLAN-DSML-REWIND.md +
  ISSUE-EXACT-DSML-REWIND-REBUILD-20261104.md (corrections 3-6 recorded,
  including two of MY OWN claims retracted same evening).
- 21:1x RETRACTION + CORRECTION of my own 20:4x entry ("ROOT CAUSE FOUND,
  FIX DEPLOYED"): that entry named the eval-site clearing as the cause. It is
  ONE of two routes, and NOT the dominant one. The per-site label build (fcad472)
  named the real culprit within six minutes of deploying:
  end_state=ds4_session_eval_dspark_speculative_stochastic (21:03:30,
  kept=234,683 est_s=671). frontier - window_start = 4 = accepted_drafts pins it
  to the stochastic driver's direct-prefix commit (ds4.c:84669-84683): truncate to
  start, capture_invalidate (zeroes end), commit frontier prefix, push accepted
  drafts, mark valid - and no dspark_rewind_keep(). Its argmax twin (84122) does
  the same sequence and DOES arm. The stochastic driver has one arming site and
  three invalidation sites, and it is the sampled-temperature path -> an
  EXACT-sampling tool session hits it on nearly every block. Fixed in 18fa4f8
  (gated on DS4_REWIND_RESUME), deployed 21:12:17 PID 11552.
  WHY MY SYNTHETIC REPRO SHOWED ZERO BAILS, finally: that instance reported
  sample_cycles=0 - the stochastic driver never ran, because the repro never
  rejected a draft and then crossed a mode boundary in the same turn. Every
  "the stall doesn't reproduce" reading up to now was measuring the wrong driver.
  *** FOR THE CORPUS/PARITY LANE (action, not information): serve and rebuild are
  NOT bit-identical - they differ in reduction order the same way batched verify
  differs from serial decode. A flip needs the sampling CDF cut to land inside
  that ulp, but at request level a flip re-conditions everything after it. The
  served route is the engine's dominant reference (all four other commit paths
  already rewind this way), so this deploy moves emission TOWARD the reference -
  but it still changes what THIS binary would have emitted on exactly that branch.
  Re-baseline parity captures at the 21:12:17 deploy and mark the three
  stall-labeled turns (19:28:26, 19:39:31, 20:57:52, 21:03:30) as known-divergent
  if any parity diff is reported across the boundary. Rollback:
  DS4_REWIND_RESUME=0 ./ds4-server.sh restart-collect.
  Two engine restarts tonight were force-kills mid-rebuild; the disk KV store
  survived (229,376-token entry hit in 651.8 ms after the first), so no rehydrate
  damage observed - but that is one sample, worth watching on the next deep turn.

## 3c LANE ACTIONS from the DSML-REWIND deploy (21:12:17, agent's fix 18fa4f8)
- VERIFIED 21:56: 23x 'truncated without rebuild', 0 rebuilds, 0 decode
  collapses since deploy; one 752s prompt-done = post-restart cold
  re-prefill (expected, not a rewind).
- ERA SPLIT for day-14 corpus: pid files 60838/73479/2109/3106/4051 =
  PRE-FIX (stall-era: serve vs rebuild NOT bit-identical - reduction
  order; agent marks the 4 stall-labeled turns known-divergent if a
  parity diff spans the boundary). pid11552+ = POST-FIX. S1a/S1b slab
  builders: treat the deploy as an era line (like the pid>=53508
  exact-era split); re-baseline parity from 21:12:17.
- PLAN docs supersession: ISSUE/PLAN root-cause = 'gate bails'
  hypothesis; ACTUAL = stochastic direct-prefix commit never armed the
  window (agent ledger 1a57259). M0 DONE (fcad472+); fix landed via
  M2-flavor arming, not M1 resume - M1 now likely moot; M3 upstream
  write-up still open.
- Night-plan note: tonight's A/B (if operator greenlights) runs on the
  FIXED binary - arm A/B shas unaffected (support ggufs untouched);
  confcal2 gate unaffected. Bonus: post-fix turns are cheaper AND the
  A/B accept stats no longer contaminated by stall-era truncation
  differences (post-deploy data is the clean era).
- 22:5x ROUTE #3, and a SECOND parity discontinuity (action for the corpus lane).
  The watcher fired at 22:43:00 with `end_state=armed` — proof the 21:12 arming fix
  held, and proof of a separate gate defect: a truncation target equal to the
  window's END was refused as target-ahead-of-window and re-prefilled 70,614 tokens
  (172.5 s of silence in a live turn; ~670 s at 230k depth), with prefixes=4,
  logits=1, start and checkpoint_valid all intact. Two events in the same session
  25/55 s earlier served the same geometry one position earlier (truncate = end-1),
  so this is an off-by-one at the inclusive edge, not a state problem.
  Fixed + tested in ecef491 (shared predicate for gate AND restore; new
  test_rewind_window_gate in tests/test_session_state.c, model-free, RED→GREEN in
  both GPU and no-GPU builds). Independent review PASS_WITH_MINORS; its MEDIUM
  (pos == end == frontier would be served as a pointless re-replay) is fixed in the
  same commit and pinned by the sweep.
  *** PARITY: this is a SECOND numeric-route discontinuity at a SECOND deploy.
  The 22:43 event class changes from full re-prefill to snapshot+1-replay. It must
  be marked separately from the 21:12:17 marker — do NOT fold it in. Blast radius
  ~1 observed event in all logs. ecef491 is committed but NOT DEPLOYED: the engine
  still runs 18fa4f8 (PID 11552), because deploying kills an in-flight session and
  the operator has not named a window. Rollback for both changes remains
  DS4_REWIND_RESUME=0 (with it unset, both fixes reduce byte-identically to
  pre-fix behaviour; pinned by the test).
- 23:5x CORRECTION #4 (my own instrumentation lied to me): the DSpark shutdown
  counters sample_cycles / sample_full / sample_partial / sample_rejects had NO
  writers - declared and printed, never incremented. Both times I read
  "sample_cycles=0" and concluded the sampled driver never ran, and I wrote that
  into 18fa4f8's commit message, the 21:1x ledger entry, and the PLAN. Retracted;
  full correction in PLAN-DSML-REWIND.md.
  With writers added (stats-only, inside if (stats_enabled)), the same 2-minute
  workload shows sample_cycles=1022 of 1058 cycles and sample_partial=228: the
  branch 18fa4f8 fixes runs constantly under temperature-0.8 DSML tool traffic,
  with replay_fallbacks=0 and errors=0. First field proof the patched path is hot
  and healthy. Arm B (DS4_REWIND_RESUME=0, same seeds): sample_cycles=6300,
  rebuilds=0 - the internal restore path serves regardless of the tolerance, so
  the tolerance only matters for the server-side rewind route.
  That route stayed unreachable synthetically (4 attempts, generation rewind count
  0): echo-back keeps the prefix, byte-perturbation makes a NEW session (cold
  prefill, 22.8 s gap), and prefix branch replays hit disk KV (28.9 s, gap 0.0).
  So resample=1 is still unobserved post-fix; real traffic is the test, and the
  sentinel counts it.
  ENGINE STATE: restored to the real collect profile at 23:51:33, PID 19257,
  DS4_REWIND_RESUME=1, argv MATCH, capture -> /Volumes/FireCuda520/ds4-train/active
  and ds4-parity/20261103-s3. The test ran with capture redirected to a scratch
  dir, so NO synthetic rows entered the corpus (394 MB scratch deleted after).
  THIRD parity discontinuity marker: the binary now in service also carries the
  stats writers (behavior-neutral) - deploy boundary 23:51:33.

## DAY-14 BANK EXECUTED 20261105 06:2x (runbook complete, engine LEFT DOWN)
- Trigger: rewind-lane HANDOFF-BANK-20261105.md (quiet 03:30:58 -> clean
  stop at 06:2x, no force-kill; their Metal fix (PLAN-VISION-BUFFER-RACE,
  P0) waits for the down window - deploy is THEIRS on their signal).
- Sealed: /Volumes/FireCuda520/ds4-train/20261105-day14 = 13 pids,
  128,710 recs, 1,808 dropped (98.6% keep) vs day13's 30,609/98.7 ->
  4.2x HARVEST. td_validate: ALL chain=100.0%, inbreaks=0 (crash-
  truncated pid19257 tail = data gap not corruption: lost <=128-record
  ring only). Backup rsync'd -> ds4-train/backups/20261105-day14 (1.5G).
  active/ recreated empty.
- MANIFEST --note encodes THREE semantic era boundaries (supersedes my
  22:00 single-era-line note): PRE-FIX (8 pids incl. unattributed
  6848/9304, era by mtime, binary unverified) | FIX1 18fa4f8 21:12:17
  (arm window) | FIX2 ecef491 23:07:18 (edge serves) + 6f1d38e stats
  boundary + 19257 CRASH tail + 21663 MIS-PROFILED (VERDICT: KEPT but
  flagged - consumers may drop by pid; 712 recs) | ERA-FIXED 21843
  (115/115/0 rebuilds). Test boots 18926/19037 = scratch-only, pid gaps
  expected. Two admitted tiny health probes: no records materialized
  (their mtime check + our smallest-conv scan both clean).
- loops --audit --manual LOOP-MANUAL-20261103.json -> LOOP-QUARANTINE-
  20261105.json: 9 legacy segments (210,905 pairs, days 1/2/9/12) + 1
  manual span; ZERO day-14 additions. Quarantine committed.
- POOL MATH for next iterations: draft pool 457,371 -> ~584k pairs
  (+28%); conf pool 30,205 -> ~157k cycles (5.2x, spans MANY pids and
  both engine eras - the single-pid caveat dissolves for confcal3);
  post-fix era (21843, 55,956 recs) is the bit-identical-serve data;
  PRE-FIX turns on sampled-temp tool paths carry the serve-vs-rebuild
  reduction caveat (marked in manifest). Rollback lever for any parity
  diffing: DS4_REWIND_RESUME=0 (pinned by tests/test_session_state.c).
- 12:30 DSML/buffer lane -> corpus lane, day-15 boundary note (no action needed,
  just don't be surprised): FOUR engine boots since the day-14 seal, all while
  `active/` was empty — 06:41:43 (47991 batch-join guard), 07:36:06 (54539 retain
  mutex), 08:25:58 (59996 section detection + nil-encoder guards), 12:25:16
  (**84093**, current, review follow-ups). Proxy restarted 12:26:06 (84199):
  single-flight=1, upstream timeout 600->3600s, chunked bodies decoded, PNG
  rejections classified SOUND/BROKEN/TRUNCATED. Net effect: day-15 has NO era split
  inside it so far — the binary that writes the first qualifying turn is the current
  one, so a single `--note` covers the whole day.
  Independently reviewed after the fact (6 of 9 code commits had shipped without it):
  PASS_WITH_MINORS, "do not revert; keep collecting". Nothing in it changes corpus
  semantics; the guard diverts cross-thread batch joins and the proxy now queues
  rather than racing them.
  Two things that DO touch you: (1) `--kv-disk-space-mb` is 524288 again — the
  00:24:40-00:26:37 boot (pid21663) ran half-budget from my missed env re-source,
  already flagged in the day-14 manifest; (2) the 4 `frontier-contradicted` drops
  seen at 07:4x came from MY concurrency hammering (two holders, identical prompt =
  same conversation), are self-healing, and predate the guard/mutex (6 in the
  unguarded 23:51 boot). Not a store fault.
- 16:5x DSML/buffer lane -> corpus lane, DAY-15 BANK EXECUTED (runbook complete,
  engine LEFT DOWN, proxy stopped too - both ports verified clear):
  quiet 16:41:42 -> clean stop 16:49:47 (drained; KV persisted tokens=369147
  trimmed=0 reason=shutdown, so the next boot resumes warm).
  Sealed: ds4-train/20261105-day15 = 1 pid (84093), 51,391 recs / 829 convs,
  829 dropped (98.39% keep; drop = exactly one frontier-tail per conv).
  td_validate: chain=100.0% inbreaks=0. Backup rsync'd ->
  ds4-train/backups/20261105-day15 (619M, cmp byte-identical). active/ recreated
  empty. loops --audit --manual LOOP-MANUAL-20261103.json: ZERO day-15 additions -
  regenerated LOOP-QUARANTINE-20261105.json is byte-identical to the committed
  day-14 version (same 9 legacy segments, 210,905 pairs, days 1/2/9/12 + 1 manual).
  MANIFEST --note carries the era story: SINGLE BOOT, SINGLE ERA (all earlier
  day-15 boots captured nothing - active/ was empty at each), binary e3aadc6
  lineage with every rewind+race fix, 197/197 rewinds armed, 0 rebuilds,
  0 tripwires, deepest 368,142. Traffic ran DIRECT on :8001 (not through the
  proxy's single-flight); the engine-side guard held all day - zero foreign-
  thread lines. resample=1 and edge-target serves still 0/197 in the field
  (covered by the model-free gate test). Two of my health probes (12:26, 14:26,
  both <40 tokens) are named in the note as possible tiny-conv drops; validate
  shows nothing suspicious.
  POOL MATH: draft pool ~584k -> ~635k pairs (+8.7%); day-15 is the FIRST FULL
  DAY with zero era caveats inside it - every record is post-fix, bit-identical
  serve, single pid, single parity file (pid84093.bin, 4.21 GB persistent).

- ERA-GUARD LANDMINE (confcal3 blocker, 3c-lane fix owed in labels3c.py):
  D-S0-3's pid>=53508 threshold was a proxy for '--mtp-exact-sampling
  boot', valid ONLY within the never-rebooted day-13 pid monotonicity.
  Day-14/15 exact-era boots carry small pids (2109/3106/4051/6848/9304/
  11048/11552/15854/19257/21663/21843) -> guard as-conf currently REJECTS
  real conf-eligible data. Fix = explicit allowlist flag
  (--known-exact-pids 2109,3106,... default-still-strict) + banner
  evidence per pid; pids 6848/9304 = binary-unverified, EXCLUDE from
  confcal3 conf pool until boot-banner proof found (markov is era-
  agnostic, unaffected). All 3C boots since Nov-3 20:32 had exact:1 in
  the terminal banner by script design, but design != field evidence -
  per-lane rule 'verify binary strings before claiming features'.
## AB-CAMPAIGN + CONF-POOL GROWTH STARTED 20261105 17:0x (operator: "run all")
- ORDER: A1b offline re-baseline (CONFIRMS confcal2 head under traffic
  probe + temp battery; engine DOWN, instance lock held) -> boot ARM A
  (warm resume per dspark-lane recipe + 4-point verify + sentinel
  re-arm) -> P0 gate -> A-B-A-B sweeps (informal, SCORED) -> restore
  plain collect boot -> commit/push at every boundary -> confcal3
  slab rebuild over day13+14+15 (~210k recs, multi-pid multi-era) +
  joint-head training (L0 jobs <=8GB alongside engine).
- PRACTICAL FIXES FOUND WHILE PREPPING: ds4flash.gguf is a SYMLINK to
  the real Vision-Exp model (handover's '119-byte stub' = the symlink
  ENTRY size - tests ARE runnable here); AB-PROTOCOL boot-lever line
  ref corrected 204->213 (index 1 'vision' unchanged); A1b copies run
  with cd pinned to repo root (/tmp/dspark-ab/, SUPPORT -> blade
  confcal2 sha 5c0286dd verified before launch).
- P0-GATE CORRECTION (mid-campaign, 20261105 17:2x): the flush point is
  ds4_session_free (POOL EVICT / SHUTDOWN, ds4.c:76419 call 76445), NOT
  per-request as the night's S6 assumed (76362 matched then-code only).
  Each boot era emits ONE aggregate 'DSpark stats' line into its own
  log, snapshotted at the NEXT boot's rotation. So: informal-A sweep
  ran 28/28 requests (tool turns finish=tool_calls; tok/s 42->36 as
  ctx grows; ZERO errors) and its stats line WILL exist at the A->B
  switch -> P0 satisfied by design of the switch itself. bench script
  gets --allow-no-stats (mid-era safe); scoring parses ROTATED era
  logs per arm (parse_stats offset 0), proven on day-15's dump:
  cycles=114,333 accept=64.69% hist 17-bins decode on REAL line.
  Scored-window method now: boot era -> sweep -> next boot rotates ->
  parse that era file. Arm isolation by boot = strictly cleaner than
  the byte-offset live-window idea anyway.
## AB-CAMPAIGN VERDICT 20261105 17:5x: STAY OFFICIAL (rule-conformant)
Full protocol v3 executed: A1b (confcal2 offline ladder PASS, 8/10
shapes above no-dspark baseline; 'toolcall' shape -15% = first signal)
-> P0 PASS (era-cycles 78) -> informal A/B -> SCORED A/B (arm-isolated
BOOT ERAS; stats flush at session-free corrected mid-campaign;
--allow-no-stats + rotated-era parse; bench hist fix validated on
real lines). SCORED: A 85.98%/avg2.379 vs B 84.65%/2.242; full5_share
B 0.308 vs A 0.284 (only B-lean metric); zero_share 0.407 vs 0.345;
PAIRED t=0 (n=12 deterministic): B SLOWER 12/12, mean -1.64 t/s
(-3.9%, >5% STOP not tripped). Mechanism: confcal2 engages (cuts
~4% proposals/cycle) while official .45 gate ~inert on short fresh
bench contexts; drafting-everything wins there -> gate value is
REGIME-DEPENDENT; offline +1.63pp AUC untouched and uncontradicted.
PRE-REGISTERED RULE APPLIED AS WRITTEN: mixed/B-behind => stay
official, iterate training; threshold NOT moved (AB-GATE-RULING).
Follow-up (offline, no engine): does confcal2 beat official on the
300k+-ctx day-15 runaway-argument regime (era label replay)? That is
the regime a calibrated gate can win; decide adoption there.
Engine: RESTORED to official collect boot (PID 16003, argv byte-
identical, invalid=0, parity armed, sentinel re-armed). Artifacts:
/tmp/dspark-ab/{ab-verdict.json,era*.json,arm*-*.json,scorer.py};
blade confcal2 file kept for forensics. Day-16 collection live.
NEXT: confcal3 (era-guard fix -> slab over day13+14+15 -> joint-head
train with allowlisted exact-era pids).
- 18:0x CONF-SLAB BUILT: conf_s3cal_20261105.npz (5.6 GB, 15 pairs):
  207,624 cycles total (day13 30,205 | day14 13 pids 126,871 | day15
  50,548) = 6.9x day-13 pool; ALL pairs pos_mismatch=0, skip_quar=0
  (day-14/15 audits added nothing -> quarantine-1105 spans live only
  in days 1/2/9/12, consistent); 11 small-pid files admitted via
  --known-exact-pids. CRASH-ERA CONFIRMED IN DATA: pid19257 n=6540 vs
  npar=6574 (34 parity-only records = lost recs ring tail; index join
  took min cleanly). First launch attempt died on zsh no-word-split
  ($ARGS as one token) - relaunched via bash script; waiter on process.
  CHAIN RUNNING: official --slab-replay (full-slab H1 reference) ->
  conf3c --markov (slab conf_prev mode, no rebuild) -> confcal3. Then:
  per-pid val-AUC slices (6848/9304/21663 era sensitivity), per-era
  train/val split stats, review of 3-file change (pending since
  3b900a3) BEFORE confcal3 verdict.
- 18:2x CONFCAL3 RESULT (run_confcal3/, pending review): joint engine-
  form head on the MULTI-PID slab (188,219 train / 19,405 val cycles;
  480,177/49,281 rows - val 5.5x night). Val pooled .8501 vs official
  replay .8276 = +2.25pp (n>4x the night's, CI tighter expected).
  Per-slot ALL 4 beat official VAL-VS-VAL (review-corrected baseline
  [.8674/.8131/.8025/.7696]; my earlier table had the pool-wide row
  set - mixed-row-set MAJOR caught in review): .8787/.8389/.8230/
  .8058 -> margins +1.13/+2.58/+2.05/+3.62pp (slot3 fattest). The
  official .8690-slot0 number in 3db9d42 was pool-wide, superseded by
  this line. Headline bootstrap: +2.25pp CI [+2.03,+2.48] P>0=1.0000
  (reviewer rng 31337, 10k paired row-resample). ECE .0129. PASS_auc.
  official ref .8276 from the saved f32 npy (report's f64 .8283 - state
  source in any external quote).
  CROSS-ERA: per-pid val slices - confcal3 beats official on EVERY
  stable pid (delta +.0018..+.0694; big-clean-era 21843 +2.7pp n=9671,
  day15 84093 +2.4pp n=12634; day13 slice .8544 > night confcal2's
  .8523). Era-allowlist sensitivity PASSES (6848 +2.6pp, 9304 +6.9pp -
  no foreign-era signature). Conv-hash split = no cross-file leakage
  (same conv same side by construction). Day13-slice vs night confcal2:
  .8544 vs .8523 = +0.21pp CI [-0.10,+0.51] = NOISE (review paired
  test) - confcal3's claim is AGGREGATE + cross-era, not day13-victory.
  CALIBRATION FLIP: gate region (n=7656) pred .4659 obs .4502 =
  +1.58pp OVER-prediction (confcal2/day13 was -2.5pp UNDER) - a
  marginally LOOSE gate direction on the broad pool. Watch item for
  any future A/B mirrors last night's: expect B to cut slightly FEWER
  borderline-bad drafts than truth; coverage .9138.
  NOT YET: deploy artifact (no repack of confcal3 head - premature:
  arm-B entry needs operator want + blade verify gate). DONE since
  review CLOSED PASS on the 3-file change (prev independent 200/200
  raw-byte check; dual-mode replay byte-identical incl == night
  run_officialreplay1 npy = single-pair path UNREGRESSED; 36/36) +
  bootstrap CI (above) + per-pid slices independently reproduced.
  Known minors: misleading failure wording if --recs passed with
  multi-pair slab (loud, never silent-wrong); prev>=0 guard added
  (conf3c one-liner this batch).

## REGIME REPLAY (pre-registered 18:3x, offline-only, conf-deploy crux)
Q: does confcal3's .45 gate earn its keep in the LARGE-CONTEXT tool
regime (where live A/B on toy bench traffic could not show it)?
Data: sealed slab conf_s3cal_20261105 + saved score npys (official f32
.8276-source, confcal3 val-scores all-rows). Universe: VAL rows only
(honest, unseen), label!= -1. Bins: cycle pos (context depth)
[<8k,8-32k,32-100k,100-200k,200k+] x per-bin: n, true-accept-rate
(base), each head's coverage@.45, CUT-TRUE-RATE (accept-rate among the
rows that head cuts = damage-avoided quality; GOOD cuts => well below
base), kept-set accept rate, AUC. Secondary tables: pos>=32k by pid-
era; pos>=32k by slot d.
DECISION SHAPE (fixed before seeing numbers):
  PROCEED-A/B-v4 iff: (a) confcal3 cut-true-rate <= base - 15pp in
  BOTH >=32k bins (cuts real bad drafts), (b) official coverage there
  >= .99 (inert - nothing to lose by replacing), (c) small-bin (<8k)
  cut-true-rate not materially worse than big bins (i.e. no regime
  inversion where its cuts are junk on small ctx too), (d) confcal3
  AUC > official AUC in >=32k bins specifically (ranking advantage is
  located where deployment claims it).
  KILL-LINE iff confcal3 cut-true-rate >= base in the >=100k bins
  (cuts good drafts there = its gate is wrong where we claimed right).
  Everything else = AMBIGUOUS (report, no adoption push).
Note: engine-level speed trade (fewer drafts = fewer verify tokens =
slower when drafts are cheap) stays OUT OF SCOPE offline - this
answers ONLY 'is the cut decision better in the claimed regime'.
- 18:4x REGIME REPLAY VERDICT: PROCEED (all four pre-registered
  conditions met with 2.5x margin; kill-line nowhere near):
  (a) confcal3 cut-true-rate at >=32k: .342/.361/.365/.404 vs bin
  bases .732/.761/.787/.808 = 37-42pp below base (bar: 15pp) -
  cuts are overwhelmingly TRUE rejects; kept-set accept rises
  (+4-5pp) in every big bin. (b) official coverage .9997-.9999 in
  all >=32k bins = inert exactly where it matters (2-3 cuts total).
  (c) no regime inversion (8-32k cuts as good; <8k bin n=111 too
  thin - flagged as open noise, official outranked there .862 vs
  .807 - note for v4 small-ctx leg). (d) AUC advantage LOCATED in
  big ctx: .8443/.8501/.8653 vs .8230/.8232/.8428; merged >=32k
  bootstrap +2.27pp CI [+2.03,+2.51] (rng 777, 2k paired).
  ERA STABILITY: cutTR/AUC advantage identical across day13,
  day14-mid, day14-post+day15 groups -> not an era artifact.
  The toy-bench A/B loss is now EXPLAINED, not overturned: on
  fresh <8k conversations drafting-everything wins (no bad-draft
  mass to cut); at >=32k tool traffic the official gate is inert
  against a 24% reject tail that confcal3 removes at 62% precision.
  CAVEATS (unchanged): engine speed economics stay out-of-scope
  offline (cut rows were also free accelerators when accepted -
  only a big-context A/B measures the net); gate over-predicts
  +1.6pp now (watch item FLIPPED vs confcal2's under-prediction).
  PATH TO v4 (A/B-PROTOCOL needs rewrite as v4): (1) arm B =
  CONFCAL3 - needs repack + blade verify + review (gated chain,
  confcal2-era procedure); (2) traffic = big-context tool chains
  (>=32k, thinking+tool mix, fixed seeds for the paired-t0 leg;
  small-ctx leg kept as parity-guard since <8k remains official's
  claimed home); (3) decision rule re-anchored on kept-set accept
  + verify-token burn + tok/s at >=32k, not pooled toy accept.
- 18:5x CONFCAL3 DEPLOY ARTIFACT BUILT + VERIFIED (chain step 1 done):
  /Volumes/FireCuda520/ds4-train/repack_test/support_AB_confcal3.gguf
  sha256 a25cf9eea460c5add0462da1be8d3ce06758c6e364a9b578f498637388dd4698
  (official 0807a67f.. re-verified unchanged, file NOT written; sizes
  equal 5,989,114,528; changed bytes all in 4,624 B window @
  4,077,251,392; repack-tool dequant max|d| 9.29e-4; INDEPENDENT
  rescore THROUGH DEQUANT BYTES: val AUC .8501 == f32, gate-.45 flips
  62/49,281 (.13%, quant-boundary noise) -> deploy-faithful quality
  CONFIRMED, same calibration-silent profile as 1b/2).
  weights artifact note: run_confcal3/confcal1_weights.npz w_full
  (1,4352) b=0 finite markov 256/256 nonzero.
  SELF-STumble banked: hand-rolled dequant twice wrong (f16 scale
  stride confusion, then int8-as-uint8 sign) - canonical path is
  `from repack_dspark_tensor import q8_0_dequant` (tool layout IS the
  validator; ds4.c:2330 34B blocks). Future rescorers: import, don't
  re-implement.
- 18:4x AB-PROTOCOL v4 WRITTEN (arm B = confcal3 a25cf9ee..; two-leg
  traffic: BIG >=32k verdict leg [paired-t0 tok/s >=0 PRIMARY,
  proposed/cycle drop >=8%, kept-accept not <A-1pp, hist5 not <A-3pp,
  quality ladder], small PARITY leg non-verdict; era-line scoring
  method baked in; FLIPPED watch item documented - loose direction,
  stop-signature moved accordingly; confcal3 needs its OWN A1b before
  first boot). KNOWN GAP flagged for review: bench_ab_3c.py has no
  BIG-leg runner yet (growing multi-turn >=32k context) - implementer
  step owed before v4 execution.
- 19:0x PACKAGE REVIEW (qwen lane): R1 PASS_WITH_MINORS - PROCEED
  VERDICT STANDS + goal-post audit ruled HONEST (condition (c) passes
  literally at <8k, cutTR .333; the inversion is outside all PROCEED
  conditions and drove the parity-leg design; kept-set lift is
  +3.2..4.2pp not +4-5; era-list pinning: mid .411 excludes 11552,
  .394 includes - both reproduced; f32-threshold repr micro-deltas).
  PROCEED SURVIVES POST-FIX-ONLY EXCLUSION (21843+84093 alone: cutTR
  gaps 40.2/48.3/40.7pp, merged boot +2.50 CI [+2.16,+2.85]).
  R2 PASS - artifact FULLY independent: official sha unchanged;
  4,587 changed bytes all inside window; rescore-through-bytes .8501
  EXACT, 62/49,281 flips EXACT; re-quantize round-trip BIT-EXACT
  (weights->artifact provenance deterministic). R3 PASS_WITH_MINORS,
  MUST-FIXES APPLIED THIS COMMIT: (1) hard-rule-4 gate direction was
  INVERTED in v3 text (engine cuts on sigmoid < .45, ds4.c:37064);
  (2) STOP "overshoots A" vacuous (A inert .9998) -> re-anchored
  "B gate-KEEP-coverage > .9138 + 4pp"; (3) A1b SUPPORT hard-assign
  trap (silent-wrong-baseline = v1 boot-lever class) -> /tmp
  script-copy procedure documented; (4) paired-t0 determinism
  (ds4.c:88032 exact=greedy+exact-sampling, v3 12/12 precedent) must
  be CHECKED not assumed: BIG-leg runner per-pair completion sha256,
  divergent pairs dropped+flagged. REMAINING BUILD: --big-leg runner
  (reviewer shape: 2-4 fixed 40-60k-token seed texts committed OUT of
  corpus dirs; 4-8 canned multi-KB tool turns growing ctx; per-step
  sha256+ctok+prompt_tokens to prove >=32k crossing; t=1 reps
  non-verdict; DS4_TD_PARITY UNSET (never =0, coercion trap) +
  TRAIN_DUMP redirected during dev). v4 is EXECUTABLE when runner
  exists + operator signals.
- 19:0x BIG-LEG RUNNER SHIPPED (bench 062cb4d): --big-leg mode = 2 seeds
  (50k+65k-token canned doc seeds, sha-deterministic), 6 canned
  multi-KB tool-result turns growing context, per-turn content_sha256
  for arm pairing (scorer DROPS divergent pairs - determinism checked,
  not assumed, per v4 review), t=0 verdict core / t=1 directional,
  big-leg implies allow-no-stats mid-era. Offline proof suite green:
  pair-hist parse, 17-bin add, allow_empty + strict raise, seed/result
  determinism. NOTE for the record: commit ran while a self-test was
  failing - failure was my TEST constant (3024 vs 3044), code was
  correct; re-verified clean post-commit. LIVE SMOKE TEST OWED on v4
  day: first boot = dev boot with DS4_TD_PARITY UNSET + TRAIN_DUMP to
  /tmp, run one --big-leg seed, confirm >=32k prompt_tokens crossing
  + era-line cycles>0 at stop, THEN real arms. v4 is now
  FULLY-BUILT: runner + protocol + artifact + pre-registered rules;
  execution awaits operator signal + a quiet window (dev smoke, then
  A->B->A->B big-leg cycles ~1.5-2h).
- 19:2x DEV SMOKE #1 RESULT (runner v1): mechanics PASS (B boot argv =
  confcal3 a25cf9ee via /tmp lever; capture CONTAINED - pid25288 wrote
  ONLY /tmp/ds4-smoke-train, real active/ untouched; 14/14 turns
  >=32k ctx (66-100k); era line cycles=129 proposed=213 accept=77%;
  official reboot ARGV-MATCH + sentinel re-armed). TWO TRAFFIC-SHAPE
  DEFECTS found & fixed (7f2b499): (1) prose assistant echoes taught
  the model to stop calling tools (2/14 tool_calls) -> canned history
  now REAL tool shape (assistant tool_calls + role=tool results);
  (2) wall tok_s prefill-polluted (0.3 vs engine's true 33 t/s) ->
  first_token_s added (TTFB), tok_s kept for reference; max_tokens 300.
  NOTE for day-16 manifest: active/ pids 14145/15381/15799/15923
  include the v3-campaign BENCH sweeps' tool traffic (real requests on
  collect-armed boots - legitimate cycles but identifiable synthetic
  conversations; consumers can exclude known bench prompt hashes).
  RE-SMOKE #2 RUNNING (v2 runner, same redirect discipline, ~20 min).
  PAIRED-T0 NOTE: tool_choice:'required' NOT honored by server (only
  'none' parsed) - forcing is prompt-engineering + history-shape only.
- 19:4x DEV SMOKE #2 (v2 runner): PASS. 12/14 turns finish=tool_calls
  (was 2/14 - real tool-history shape fixed it); 14/14 >=32k (66-98k);
  IDENTICAL per-turn content sha across seeds (deterministic greedy
  core proven); era line cycles=153 proposed=566 accept=94.35%
  replay_fallbacks=0 errors=0; containment held (pid25899 only to
  /tmp dirs); official collect restored (PID 26564, ARGV-MATCH2,
  invalid=0, sentinel armed). BONUS FINDING: turn0 ftok ~3-5s =>
  disk-KV prefix cache HIT from smoke #1 across boots - paired
  first-cycle asymmetry (cold vs warm prefill) is EXACTLY what the
  pre-registered informal-first-cycle rule absorbs; no protocol
  change needed. V4 FULLY GO-READY: runner + scorer (self-tested) +
  A1b confcal3 PASS + artifact verified + protocol corrected.
  Execution = operator window (~2-2.5h, 4 boot swaps, user sessions
  pause minutes each swap).
