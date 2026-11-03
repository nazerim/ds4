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
- [ ] S1a 22:50-23:55 CONF-ERA LABELS: recs x parity x conv -> npz slab,
      exact-era SEALED ONLY (day-12 pid53508 file + day-13; active/60838
      EXCLUDED - append-live). Builder dspark/labels3c.py.
      GATE: counts match td_manifest, chain 100%, era tags verified by spot rows.
- [ ] S2.5 23:55-00:40 ACCEPT-PROXY EVAL HARNESS (dspark/eval3c.py):
      conf>=.45 precision/recall + AUC vs observed accepts.
      GATE: reproduces milestone-1 anchors AUC .7418/.7933 +- .005 on frozen replay.
- [ ] S3 00:40-02:45 CONF-ONLY CALIBRATION RUN: BCE on accept labels, trainable
      confidence proj (parity conf rows in, NO trunk). Tag confcal1.
      GATE: held-out AUC >= .7418/.7933. (Agent builds S5 repack tool during run.)
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
- [ ] S5  (inside S3/S4 compute windows) REPACK TOOL on COPY of support gguf:
      extract -> re-insert -> file sha256-identical proof; layout ds4.c:6113.
- [ ] S6  (gap filler) AB-PROTOCOL.md doc + bench script (no execution).
- [ ] R   07:15 ENGINE RESTORE (see policy) + night report section below.

## SLOTS: RESULTS (fill live)
- S0:
- S1a:
- S2.5:
- S3:
- S2:
- S1b:
- S4:
- S5:
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
4. EXACT-ERA PRIORS (amended - marginal != commit): JOINT per-slot commit
   .801/.465/.238/.175 <- THESE feed BCE priors; conditional-on-prefix
   .807/.58/.51/.74; marginal matches .807/.641/.556/.512 are CONTAMINATED
   by post-reject coincidental matches (reviewer independent recompute,
   n=27,862). The old "1+.807<=1.9x" plausibility clause = unfalsifiable,
   dropped. Seed-era marginals .978/.955/.934/.920 = opportunistic, conf
   labels NOT used from that era.
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
- 23:1x S0 INDEPENDENT REVIEW (fresh ctx): probe CORRECT + reproducible
  241/241 fields; VERDICT REWORK on decision TEXT only: cap-case mislabel
  (would inject 12-72% false negatives), marginal-vs-joint prior confusion,
  dlen==1 fake_argmax exclusion, conf-rows-stale-beyond-dlen mask,
  index-1:1 join for S1a. All folded into D-S0 FINAL above. S0 DONE.
