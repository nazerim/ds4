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
