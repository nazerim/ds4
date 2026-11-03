# DSPARK-STATE-20261102 — full knowledge dump (compaction anchor)
Supersedes DSPARK-STATE-20261029.md as the cold-start entry point (that file
remains valid for Oct-28/29 detail). Read order for full context: this file ->
NIGHT-RUN-3-RESULTS.md -> NIGHT-RUN-2-RESULTS.md -> PLAN-TRAINER-20261101.md
-> CAPTURE-SCHEMA.md -> DSPARK-VISION-EVAL-20261028.md (Oct eval ledger).

## 1. LIVE SYSTEM STATE (Nov-03 ~08:20)
- **ESCALATION ERA LIVE** (Nov-03 08:1x): commit 12cc181 (re-entry
  escalation: suppress thinking after 2 same-request tripwire closes; valve
  trap fixed; detector lines now carry gen=; sidecar closes/suppressed -
  TRIPWIRE-FOLLOWUP-RESULTS-20261103.md; live trips x2 observed period-127
  class; suppression path not yet fired live - daily grep watch). Server
  PID 47769 = that binary (strings 18 tripwire, verified pre-deploy),
  re-armed: TRAIN_DUMP=active (day-12 in progress, holds 1,726 pre-restart
  + new) + PARITY s3 + budget 8192 + tripwire 1. BANK NOTE from now on:
  era = 'tripwire-escalation'. Prior: PID 41277/43176/47339 lineages in
  day-10/day-11/active.
- TRIPWIRE ERA (pre-escalation): PIDs 41277 (binary Nov-03 00:13 with
  tripwire commits 2fd684d+e94d055, strings-verified pre-deploy): env =
  budget 8192 + DS4_THINK_TRIPWIRE=1 (script default now), TRAIN_DUMP=active
  (day-11) + PARITY /Volumes/FireCuda520/ds4-parity/20261103-s3 (MAX 200k).
  loops.py gained TRIP_RE ('thinking loop tripwire: closed span at N tokens'
  line) + TYPED spans: trip-marked windows guard at depth>=4 (447-period loop
  measures only ~6x - positive-evidence close, proven live Nov-03), budget
  windows keep >=8 (pos-collision safety); 17/17 tests; quarantine json now
  8 segs + 31 budget + 7 trip spans); auth_proxy 40863. NOTE: DS4_API_KEY
  appeared in plaintext in a ps output Nov-02 session - operator advised to
  rotate.
- Budget era (Nov-02 18:56 - Nov-03): commits 5249d80 (hard budget, byte-
  identical when unset; make-test ok, acceptance 4/4) + a1861bc (sh default
  8192) -> day-10 data is budget-era (15 chop spans logged, 31 total in
  audit json); superseded by tripwire era above.
- In flight: **3b mirror build - MILESTONE 1 PASSED (Nov-02)**:
  trainer/dspark/mirror.py replays the confidence gate offline from parity s1
  (18,424 slots, 6 boundary-tolerance violations = 0.033%; conf dist
  p05 .478/med .741/p90 .998) - validates feature layout h++w1[prev], w1
  token-id indexing, sigmoid>=.45 rule, extracted tensors. Next milestone:
  MTP-trunk replay to reproduce proposal logits rows (not in dump; needs the
  72-tensor stages forward). Parity s1 (65,536 cycles, 5.4 GB, zero header
  anomalies; bands 128k+ 33.3k/64-128k 14.4k/16-64k 17.7k/<16k 33) SEALED
  + backing up to ~/Backups/.../parity-s1/; s2 live for shallow band.
- Blade: ~845 GB free; internal disk backup of ALL banked days:
  ~/Backups/ds4-train-20261101/ (days 1-8, sha-verified).
- Uncommitted (per rule; PROPOSED-COMMITS.md has the 7-group split):
  M ds4.c (+~480 lines: td capture module v1.3b, parity dump, escape,
  jitter), M ds4-server.sh (KV retarget/mount guard/vision keys/tuned
  defaults), untracked: tools/* (7 audit/gguf tools), tests/dspark_* (4),
  trainer/ (uv project, torch 2.15), .codebase-memory docs.
- Fingerprints: model_fp dspark-any-config 11038508403456853112 (tuning
  free, mode-toggle expensive); server only drafts TOOL requests (plain chat
  never engages — ds4_server.c ~15692 gate).

## 2. CORPUS (as of this banking)
BANKED 578,394 records (all chain=100%, inbreaks=0 post-v1.2, keep>=99.1%):
  day1 27,371 | day2 82,536 | day3 7,514 | day4 85,750 | day5 1,620 |
  day6 6,215 | day7 829 | day8 100,721 | day9 175,075 (record day: Nov-02,
  3 pids, 137 convs incl 464k+ & 82k-record single sessions; 99.91% kept) |
  day10 90,763 (budget-era evening: 15x8192 chops, cross-segment re-looping,
  ~11.9k records span-excised; 99.81% kept)  => BANKED TOTAL 578,394.
Soaking: day-11 in active/ from 00:30 Nov-03 (tripwire era, PID 41277).
Suitability audit (Nov-02): cross-day duplicate feature rows = 0; full
duplicate records 150/311,703 = 0.05% (intra-day echoes; loader can drop by
(label_hash, feat_hash)); bigram repeat density 80% is natural distribution
weighting, NOT redundancy. Depth bands (recs by conv max-ctx): day1+2+4+8:
128k+ ~31k, 64-128k ~52k, 16-64k ~57k, <16k ~13k. Structural: ~1% records
touch tool boundaries; ~4k control instances coverage-corrected.
DROPS: n_label==0 = only rule (frontier tails + legacy self-quarantine).
**LOOP QUARANTINE (Nov-02 evening audit):** thinking-loop convs identified
corpus-wide (rule v1: conv pairs>=5000 AND uniq<1200; **SUPERSEDED by
rule v2 = repetition-depth pairs/uniq>15x AND >=300 pairs** - size-free,
catches capped-era loops; depth separation is clean: true loops 19.6-187.6x,
cap-era residue <=10.2x, healthy 2-5x; re-audit under v2 queued) ->
trainer/LOOP-QUARANTINE-20261102.json: day1 12k | day2 62.7k (5 convs - the
real reason day-2 is the 'easy' split!) | day9 127k (conv97+129, the two
monsters = 75% of day-9). TOTAL flagged 201,823 pairs; clean pool 284,747.
Rows are true target-distribution data BUT degenerate contexts (collapse
effective corpus, skew conf calibration to extremes) -> EXCLUDE at load.
Loader filter (quarantine json -> conv skip) = queued task. Post-budget-era
loops are bounded at 8192 tokens/span by the new default (was 82k/span).
Day-3 = frozen holdout; day-2 = EASY split (9% unique bigrams) - never
compare metrics across splits.

## 3. EVIDENCE CHAIN (numbers that are settled)
SPEED: production tool-traffic, controlled A/B (identical tools payload,
dspark ON vs OFF, 12 cells): **+6.4% e2e / +6.7% decode-median**, t0 +6.5
/ t1 +6.3; engaged share 15-86% by cell tracks gains; cstruct the only
negative cell (-1.5/-1.8%) = surviving weak class. (Oct "+3.6%" was noise:
plain-chat arms never drafted - see FINGERPRINT/gate note §1.)
FIDELITY: 46/48 byte-identical temp-1 tool traffic (valid, tools present).
CAPTURE FORMAT: fp8-jitter in-engine A/B: acceptance +0.4pp mixed-sign =
NEUTRAL -> f16 switch permanently descoped.
DEEP-CTX: engaged drafting at 64-322k works (day-8 capture proves toolcall-
own full-blocks at 128k+); regime caveat: THINKING-at-depth engagement low
(day-4/8 bands: p_ge3 .48 vs .88 DSML-heavy day-2).
MIRROR SURROGATE (offline, engine-free, all frozen day-3 eval):
  official markov .120/.182 -> v0 bigram FT .179/.280 -> v1 linear-h .4014
  /.5334 (3.35x, plateau at 195k AND 203k pairs, review-reproduced 4-dec
  off saved weights); delta_from_h +.27; mlp trunk WORSE (.357) => class
  bound-likely; swapval (day-2 holdout) .592 = split-difficulty demo only.
  Confidence heads: AUC .742/.786 stable through CE training (probe floor
  .74/.79; engine gate sees MORE than probe - floor not ceiling).
  cos32x15 DELIVERED - DATA-BOUND CONFIRMED: 304k pairs -> .4369/.5769
   (+3.6pp clean-convergence lift; delta_from_h .30; boundary .851; ~+3.5pp
   per 1.5x step -> ~.45@2x). CAVEAT (Nov-02 loop audit): pool contained
   ~75k quarantined loop pairs (easy rows) -> headline is upper-bound
   flavor; clean-pool anchor rerun queued; data-bound DIRECTION unaffected
   (loops present in both 195k and 304k pools).
BOUNDARY class: surrogate .82-.84 top1 - h nearly free-predicts DSML
template continuations (consistent with head already strong there).

## 4. TOOLING INVENTORY (all tested; reviews done where marked *)
Engine (ds4.c, read-reference): ds4_td_* capture module v1.3b (grab/close/
segment_end/adoption-hooks/lineage-shift backstop/labels-at-close/pure-emit
flush; envs DS4_TRAIN_DUMP, DS4_TD_DEBUG), DS4_TD_PARITY dump* (81,952 B
/cycle: engine CPU-mirror proposals + 5x4096 conf-input rows; parity-*.bin;
capped; smoke-validated: proposals consistent with pos-gaps; NOTE was absent
from Nov-01 05:52 server binary - rebuilt Nov-02 09:0x, live on PID 19990;
LOADER RULE: compare conf rows only for d<dlen (rows >= dlen are stale
static-buffer residue, not zero); banner+record decode verified byte-exact
Nov-02 on real 436k-438k-ctx traffic, seed==props[0], stages=3, full-window
proposals ~48%), DS4_DSPARK_FP8_JITTER
(acceptance-harness env, experiment done), DS4_DSPARK_ALLOW_CUSTOM_SUPPORT
(revision-pin escape, reject/accept tested both ways*).
tools/ (repo): td_validate.py (invariants, strict-JSON era), td_manifest.py
(per-bank MANIFEST.json + drop list), td_classify.py* (structural classes,
lattice-corrected instances, segments via flags+break-split), td_textclass.py
(vocab-piece decode of plain-bucket windows -> content classes + gap stats;
Ġ/▁->space, Ċ->NL; labels[0]==seed doubling trap fixed*), vocab_ids.py (ds4
GGUF dialect: bool=7 STRING=8 ARRAY=9 u64=10 - NOT standard llama numbering),
gguf_write.py* (typed writer; byte-exact 6 GB roundtrip of official support),
gguf_meta.py.
tests/ (repo): dspark_bounding_pass.py (NOW tools[] payload - mandatory for
server-side drafting), dspark_traffic_probe.sh, dspark_temp_battery.sh,
dspark_followups.sh; make dspark-acceptance targets stale 0731 support file
(absent in this fork) -> run manually vs Vision-Exp support (expect known
c_add guard 3<8 quirk, rc quirk not a defect).
trainer/* (uv project, python 3.14, torch 2.15 MPS): dspark/{data_contract
(record format single source; e4m3 decode incl 0x7F->+480 sentinel note),
records, dataset (break-split, day splits), model (V1... mirror: 81-tensor
inventory == engine bind list == validator cross-check; loads 72 non-expert
tensors; experts FROZEN native), markov_ft*, confidence_probe*, train_v1*
(linear+MLP h-surrogate, phase-frozen attribution, cosine, CLI-logged
protocols; ALL results in TRAIN_V1_*.json incl holdout curves), gguf_dq*
(q2_k/iq2_xxs bit-exact vs ds4.c scalars, golden tests), baseline}; 12/12
tests green; artifacts: BASELINE.json, MARKOV_RESULTS.json,
CONFIDENCE_PROBE.json, TRAIN_V1_{scale,lowwd,lowlr,long12,deep16,attr,
cos32,swapval,mlp32,cos32full}.json + v1_head*.pt.
Extraction: /Volumes/FireCuda520/ds4-train/tensors-20261101/
  mtp_nonexpert_f32.npz (2.07 GB, 72 tensors) + expert0_f32.npz + report;
  full expert materialize = 4.4 min CPU / ~65 GB if ever needed.
* = passed independent code-review passes (ses_e783cbd34..., ses_e6d2a1c83
...); findings all applied.

## 5. DECISION LOG (settled - do not relitigate without new evidence)
1. DSpark Vision-Exp stays ON in production, conf .45 + win 12 (worth +6.4%
   tool-traffic; fidelity clean; deep safe).
2. fp8 capture format stays (jitter-proven neutral); f16 descoped forever.
3. Experts frozen for v1 fine-tune (trainable = markov/conf/norms; expand
   only if gates stall).
4. Retraining target = uniform quality + calibration, NOT boundary rescue
   (head already full-blocks at boundaries; weak spot = plain content,
   esp cstruct-flavored continuation).
5. Surrogate plateau comparisons same-split only; day-3 = frozen yardstick.
6. Data question resolves by curve: cos32x15 in flight; if flat at 1.5x,
   2x point is the tiebreak; if rising, soak-to-1M.
7. Commit-only-on-ask; PROPOSED-COMMITS.md ready (7 groups).
8. Server-mode dspark = tool-requests-only is ENGINE BEHAVIOR (by design,
   DSML suppression) - capture inherits it (agent traffic = target distro).

## 6. QUEUE (ordered)
0a. DONE Nov-02: loops tool (trainer/dspark/loops.py, 4 new tests, 16/16
    green), train_v1 --quarantine wiring (flag absent = unchanged behavior),
    LOOP-QUARANTINE-20261102.json v2 (8 segs 201,799 + 9 chop spans;
    exclusions day1 12k/day2 63k/day9 127k/active 1.2k-day 2.5%; days 3-8
    ZERO; clean pool 332,367 recs). Tripwire handoff specced:
    TODO-TRIPWIRE-20261102.md (separate agent; continuation of 5249d80).
0b. SUPERSEDED then DONE: cos32clean (.4495) was trained on a FLAWED span
    filter (review B1: windows anchored [B-T,B] instead of [B+G-T,B+G] ->
    excised older clean records, kept loops; segment-level day1/2/9 was
    always correct). FIXED Nov-03 03:0x: loops.py v3 (gen-geometry from
    log gen= fields + progress-line recovery for trip lines; pid-per-line;
    day-keyed segments; completion-prefix regex; lineage intersect S2;
    mmap; version guard; tests 20/20 incl synthetic-log end-to-end; credit
    ses_e62ad1309). v3 excisions: day10 53.5%, day11 69.6%, clean pool
    333,224. **cos32clean2 rerun with v3 json = the real anchor** (queued
    AFTER bounding battery - training load contaminates decode t/s).
    Original anchor line kept for history.
    cos32clean2 RAN Nov-03 (v3 geometry, 324,081-pair pool): **THE HONEST
    ANCHOR = .4529/.5950** (delta_from_h .3185 max, boundary .8657 max,
    AUC .742/.793; clean convergence epochs 24-31 plateau .4519-.4529).
    All mirror/head targets now chase .4529 on clean pools; next clean
    scaling point cos32x2clean at ~600k clean pairs.
    (311,029-pair clean pool vs .4369/.5769 on 304k contaminated pool of
    ~same size: CLEANING PAID +1.26pp top1 - loop rows were gradient
    hijackers, not easy wins; degenerate contexts actively hurt general
    holdout). delta_from_h .3159 (new max), boundary .858, AUC .746/.793.
    All future scaling claims vs .4495 on clean pools ONLY. Next clean
    scaling point ~600k clean pairs (tripwire-era healthy days keep ~100%,
    ~4-5 heavy days out; same cmd + --out-tag cos32x2clean).
    POLICY (Nov-03 08:4x FINAL, superseding the 'excise outright' rule):
    spans=[lo,hi,kind,pid] attributed via log parity-banner (per-line
    tracking); TRIP chops excise unconditionally (firing == cycle evidence);
    BUDGET chops require window depth >= 4.0 because legit long thinking
    ALSO rides to 8192 (day-10 v3 windows split 8 legit 1.9-3.8x vs 16
    soft-loops 4.1-11.6x; morning rebuild turns chopped at 1.8-2.5x).
    Effect: day-10 excision 63.9k->38.6k (+25k legit pairs back); CLEAN
    POOL 350,196. cos32clean2 .4529 trained under the over-cut => anchor is
    a FLOOR (missing data, not poison); no rerun. Trip spans of day-11
    unchanged (11,304). 21/21 tests (budget-needs-depth case added).
    Trigger health 08:34: tripwire correctly SILENT on legit turns; budget
    chopped exactly at 8192 twice; DSML tool blocks flowed post-chop;
    capture chain 100% across chops. ONE 'kv cache discarded corrupt
    text-prefix payload' (08:33:47) = self-heal guard (client replay of
    post-chop history differs from stored prefix); token-mismatch ~40-tok
    tails = normal churn. Watch: repeat discards on one lineage would
    mean wrap-up text leaking into KV keys. Escalation SHIPPED (12cc181).
1. DONE: cos32x15 -> data-bound confirmed (§3, w-loop caveat). Next scaling point at
   ~600k pairs (2x of x15; ~4-6 heavy workdays at 100k/day soak) - one
   command when pool is there: same protocol, --out-tag cos32x2.
1M. MILESTONE-2 RESUMES AFTER COMPACTION: read
    .codebase-memory/MILESTONE2-HANDOFF-20261103.md (full knowledge +
    execution plan S2.1-S2.5; materials complete incl token_embd_f16.npz;
    open issues = metal kernel reads for hc mixers/rope/MoE-256vs128).
2. Engine-free, on ask: mirror forward build (parity dump unblocks it -
   validate stage0/1 offline vs parity-*.bin proposals + conf rows; anchor
   code: ds4.c prepare_impl 80060-80560, markov/conf helpers 36537-37100,
   metal graph in ds4_metal.m dspark section); 2x-corpus rerun when banked
   pool ~400k; iq2_xxs dequant already ported for expert materialization.
3. Needs future window (each short): deep-ctx+tools bounding refinement
   (128k+ prompts, ON/OFF) - optional polish; parity dump capture session
   for real mirror validation run (engine ON is fine - dump is env-gated,
   so actually NOT window-bound beyond a restart).
4. Bank rhythm: say "bank" -> stop/validate/manifest/backup dated dir;
   graceful stop only (shutdown-flush verified: conv tails persist via
   server_close_resources -> ds4_session_free -> td flush).
5. SEPARATE-AGENT READY: TODO-THINKING-BUDGET-20261102.md - Tier-1 (server-
   only) hard thinking budget build, fully specced w/ prior art + tests;
   handoff doc is self-contained.
6. Hygiene: active/ accumulates from 00:30 Nov-02; backup set through
   day-8 complete; /tmp/ds4-kv deleted; old-fp ladders age out via budget.

## 7. METHOD NOTES (what bit us, so it doesn't bite again)
- ds4_gpu_tensor_read/write return 1=success/0=fail (inverted-check caused
  the v1 zero-capture bug). DS4_N_EMBD is RUNTIME (g_ds4_shape).
- ds4 GGUF enum: STRING=8, ARRAY=9 (not llama standard) - see vocab_ids.py.
- Server chat loop: capture tap points = prepare wrapper (features alive
  pre-impl; single-row tensor holds h[pos-1]; batch kernel copies last row
  into it); adoption = direct checkpoint struct swap (x4 sites) - hooked.
- GPU-buffer proposals read post-impl show stage-0 noise pattern only;
  true per-cycle proposal = s->dspark_draft_tokens CPU mirror (+dlen).
- Bounding/battery on SERVER requires tools[] in payload; CLI batteries run
  vs stopped engine (process lock; windows are user-granted).
- Server logs rotate per restart (log/ds4.log.<ts>); decode chunk rows are
  bimodal by engagement; rare >90 t/s rows = timestamp-collision artifacts;
  ls/log/date timestamps can disagree on this machine - cross-check before
  theorizing about "lost" data (day-5726 false alarm).
- Feature-presence claims about the RUNNING server must be checked with
  `strings` - and TEST-BINARY claims too: ds4_test had not rebuilt from a
  NO_GPU-broken tree since Nov-01; 'ds4 tests: ok' was stale until the
  01:4x Nov-03 real pass. Build the test binaries, don't trust the file.
- Validator deep distinct/per-file only; cross-file dup audit was ad-hoc
  (0.05% found) - promote if it must gate banks.
- Feature-presence claims about the RUNNING server must be checked with
  `strings ds4-server | grep ENVNAME` - source-only validation burned us twice
  (parity thought live for an hour at 3a; binary lagged ds4.c).
- Thinking control in ds4 = prompt-injected effort only (DeepSeek V4.1 numeric
  1-100 via --think-level/reasoning_effort; Qwen3.8 named levels; thinking
  on/off via API). Anthropic `budget_tokens` is ACCEPTED-IGNORED by design
  (compat unit test ds4_server.c ~20263; parser ~1077 skips unknown object
  keys). No omlx/strata-style hard cap exists; ~30-80 line patch would add
  one (server span-counter exists via thinking-replay tracking; needs a
  one-shot force-next-token sampler hook for exactness). Post-head-training
  feature - budgets would reshape capture distribution (thinking = 45%
  full-window drafting regime).
- Text windows = 6 tokens max; classification is aggregate-grade, not
  per-record-trustworthy; 'other' bucket ~40-56% = mixed content, fine.
- Everything captured stays blade-local (reconstructible session text);
  never commit dumps; backups internal-disk only.

End of dump. Deep history: DSPARK-STATE-20261029.md (speed/KV/eval arcs),
DSPARK-VISION-EVAL-20261028.md (§1-10 batteries), PLAN-CAPTURE-PIPELINE-
20261029.md (5-stage plan), PLAN-DSPARK-PERF/TEMP-SPEC + SPLASH-INTEL-
20261023 (architecture origin), DS4FORK.md (fork divergence).
