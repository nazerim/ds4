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
  ./ds4-server.sh restart-collect -> verify banner `exact: 1` in log/ds4.log
  (capture banners are lazy - fire on first tool-bearing request, do not probe).

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
- [ ] S1b 05:15-06:45 FULL-POOL MARKOV SLAB (all sealed days ex-day-3,
      next-token labels era-agnostic, quarantine-masked). Partial OK (exact first).
- [ ] S4  06:45-07:15 JOINT RUN START — ONLY if S2 all green AND S3 green.
      Checkpointed/epoch, hard 07:15 stop, morning = partial epoch curve vs .4529.
- [x] S5  (inside S3/S4 compute windows) REPACK TOOL on COPY of support gguf:
      extract -> re-insert -> file sha256-identical proof; layout ds4.c:6113.
      DONE ~00:5x tools/repack_dspark_tensor.py: roundtrip sha256 identity
      PASS + in-place q8_0->q8_0 swap of confcal1b head proven on copy;
      independent review CLOSED PASS_WITH_MINORS ~01:1x (minors fixed,
      q8_0 goldens pinned).
- [ ] S6  (gap filler) AB-PROTOCOL.md doc + bench script (no execution).
      MUST encode (from S5 review): (1) swap strictly copy-based, record
      output sha next to official sha in ledger; (2) engine binds
      confidence_proj AT BOOT -> swap requires restart: quiesce collector
      before restart, re-anchor comparison window post-boot (no mid-run
      paired stats across swap); (3) heads keep deployable form: linear-
      4096, NO bias, zero markov half until S4 features land; (4) gate
      semantics = sigmoid(logit) >= .45 (not logit); (5) judge live A/B on
      acceptance-length histogram + coverage, not AUC alone (gate-region
      under-prediction ~1.6pp); (6) q8_0 golden tests must stay green
      before any repack tool change.
- [ ] R   07:15 ENGINE RESTORE (see policy) + night report section below.

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
- S2:
- S1b:
- S4:
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
- S6:
- R:

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
