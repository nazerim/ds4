# DSPARK-STATE-20261029 — full knowledge dump (compaction anchor)
Covers the Oct-28/29 DSpark+KV+capture arc. Read this + the three docs it
indexes to resume cold with zero loss.

## 1. LIVE SYSTEM STATE (as of 2026-10-29 ~13:30 local)
- ds4-server PID 71237 — DeepSeek V4 Flash Vision-Exp (ds4flash.gguf ->
  91GB ...Vision-Exp-Layers37-42Q4KExperts...gguf), port 8001 loopback,
  ctx 1048576, tokens 384000, --vision encoder,
  **DSpark ACTIVE**: --mtp-model
  gguf/DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf --dspark
  --dspark-confidence 0.45, env DS4_DSPARK_SCHEDULER_WINDOW=12 (exported by
  ds4-server.sh dspark path). Verified serving + dspark detect line.
- auth_proxy PID 62277: 0.0.0.0:8100 -> 127.0.0.1:8001, key
  ~/.config/ds4/desktop.key (never echo).
- Qwen engine idle (one-engine-at-a-time enforced by script).
- Blade /Volumes/FireCuda520 (~1.0-1.1 TB free): ds4-kv-deepseek ≈403 GiB,
  ds4-kv-qwen ≈472 GiB, budgets 512+512 GiB — safe, engines never co-run.
- Binaries: ./ds4 + ./ds4-server rebuilt 2026-10-29 (td capture module, v1.3,
  +324 lines in ds4.c, INERT without DS4_TRAIN_DUMP). LIVE: PID 73614 runs
  the v1.3 image, UNARMED (post-day-1 harvest, 16:2x).
- LIVE SERVER: PID 72466 Vision-Exp dspark conf0.45 win12, UNARMED (post-
  smoke restore 13:59), proxy 62277 live, serving verified. Capture is
  server-VERIFIED (armed smoke 13:59, PID 72369: 3 conversations -> 162 recs,
  segments/labels/features all clean) — collection day = restart with
  DS4_TRAIN_DUMP, path proven.
- Working tree UNCOMMITTED: M ds4-server.sh, M ds4.c (+257 td module),
  ?? tools/gguf_meta.py, tests/dspark_{traffic_probe,followups,temp_battery}
  .sh, tests/dspark_bounding_pass.py, .codebase-memory/{DSPARK-VISION-EVAL-
  20261028,PLAN-CAPTURE-PIPELINE-20261029,CAPTURE-SCHEMA}.md, log/dspark-*
  artifacts. Commits only on explicit ask (repo rule).

## 2. ds4-server.sh CHANGES (Oct-28/29)
1. DeepSeek KV retarget: /tmp/ds4-kv(128GiB) -> KV_DIR default
   /Volumes/FireCuda520/ds4-kv-deepseek, KV_SIZE 524288 MiB (mirrors qwen
   profile e482367; justified by ~13.2KiB/token ladders, 3GiB stores at 229k
   tok, two 1M-ctx sessions coexisting).
2. Blade mount guard in start_server: any /Volumes/ KV path must be a REAL
   mounted volume — compares df -P device ids vs root (plain -d passes on
   unmounted mountpoint dirs; the 2026-10-03 silent-SSD-fallback incident).
   Covers both engines. Lesson: strip "/Volumes/" prefix BEFORE %%/* or you
   truncate to empty string.
3. DSPARK_KEYS += "vision" -> start/restart-vision-dspark work.
4. Phase-3 pinned: DSPARK_CONFIDENCE default 0.6 -> **0.45**;
   DSPARK_SCHED_WINDOW=12 exported on dspark launches; rationale comments +
   help text updated.
5. Old /tmp/ds4-kv residue deletable anytime.

## 3. SPEED RECHECK VERDICT (user felt V4 "slower than remembered")
NO REGRESSION. Same binary (Oct-23) reproduces Oct-23 smoke (44.6 t/s short
burst; CLI single-session 44-45 t/s @<16k, 35 @51k, 26.8 @160k, 18.4 @323k;
cold prefill 574 t/s @51k, ~413 eff @160k, 675-735 @2k band per
DS4FORK.md:1203). The felt slowdown = (a) sessions now live 128-384k band
where decode is 18-27 t/s (KV-bandwidth decay curve, architectural), (b)
multi-session interleaving pulls chunk averages down (server log means < CLI
singles). Blade KV move costs nothing measurable (131k-tok anchor load
≈370-470ms ≈3.3GB/s; stores p50 63-363ms).

## 4. DSPARK VISION-EXP EVAL — the numbers table (docs index:
### DSPARK-VISION-EVAL-20261028.md §1-10)
- Ingest: official 5.99GB support GGUF (download_model.sh
  ds4f-vision-dspark, repo antirez/deepseek-v4-gguf) loads missing=0
  invalid=0 metadata_errors=0; taps layers 40/41/42; revision pin
  e46e16bf6035c6f317eb2ac7458eb0362926d402 matches target. "FP8 pending" in
  download_model.sh = GLM-5.3, NOT this file (dtypes: F32/Q8_0/Q5_K/Q4_K/F16).
- Semantics: greedy dspark output FP-EQUIVALENT not byte-identical (direct
  verifier-state commits tie-flip occasionally, then diverge; verifier
  errors=0 everywhere; fixture REQUIRE_IDENTICAL default 0 is correct).
- temp 0 (greedy): +10..22% short/mid ctx (pyrefactor+15.4, mathq+22.6,
  bashops+9.9, hello+18.7), +3.3% @51k synthetic; ≈0/−2% ≥128k; weak:
  docrewrite +0.6, cstruct −1.1, c_add guard fail 3<8 (stale 0731-tuned
  guard, not a defect). vs 0731-era AUDIT: G/OFF was 0.87 everywhere —
  Vision-Exp head categorically better; August "markov ceiling" verdict
  does NOT transfer.
- DSML/tool-call EMISSION: +27-33% server-side (45-50 vs 34 t/s chunks);
  tool-call DECISION phase: weak (accept 63-68%, avg 0.19) — THE unclaimed
  territory. C-continuation: weak.
- temp 1.0 CLI battery: opportunistic +6.2% mean; exact (DSPARK_EXACT=1)
  −0.4% mean (auto conf-lift to 0.8 + readback/softmax tax) — reconfirms
  PLAN-DSPARK-TEMP-SPEC "temp sampler performance-negative".
- PHASE 2 FIDELITY (server-scale, 6 prompts x 8 seeds, temp 1.0, armB dspark
  vs armA plain): **46/48 byte-identical**; within-arm diversity metrics
  EQUAL (unique 1.000/1.000, first-diverg 4/4, self-BLEU .703/.703,
  distinct-2 .483/.483); tg +1.6% noise. Greedy-bias concern did NOT
  reproduce — model's temp-1 distribution too peaked. VERDICT: KEEP.
- PHASE 2.5 (real opencode session ses_043eb69e; head 160,142 tok ⊂ full
  323,172 tok prefix-identical): decode 100-165k: 25.0 plain vs 24.5 dspark;
  165-323k: 18.4 vs 18.4 → **deep-ctx NEUTRAL** (safe on, no gain). Anchor
  reuse: both arms cached=147,456 (exactly 9x16384 grid rung), 175,716
  re-prefill. Wall deltas explained by engine cold boot, not speculation.
- **FINGERPRINT RULE (operational)**: model_fp includes DSpark on/off
  (plain 5096181743673580336 vs dspark 11038508403456853112) but NOT
  confidence/window values (0.45w12 == 0.6w4 fp). => TOGGLING dspark = one-
  time full ladder rebuild per conv + parallel ladder sets accumulating
  (blade dir grew ~75GiB during eval toggling). TUNING = free. Stay in
  dspark mode.
- **SERVER SPEC-GATING DISCOVERY (10-29 smoke; REFINES §4 claims)**:
  ds4_server.c (~15692): plain chat requests (no tools[]) set dsml_token_id
  >=0 for DSML-suppression sampling, which KILLS the speculation gate
  (~15797): dspark NEVER drafts server-side for tool-less requests. Opencode/
  agent traffic HAS tools -> drafts normally. CONSEQUENCES: (a) Phase-3
  bounding_pass server arms (+1.2%/+3.6%) compared TWO SPEC-OFF SERVER ARMS =
  NOISE (config adoption still justified by CLI battery + fp rule, but the
  server numbers don't support it); (b) Phase-2.5 "deep-ctx neutral" server
  arms similarly vacuous — deep-ctx ENGAGED dspark has never been measured
  server-side (re-test queued); (c) fidelity battery (phase2_battery.py DID
  send tools) and its 46/48 + tg+1.6% remain VALID (armB genuinely drafted);
  (d) termbench bimodal forensics remain valid (live agent traffic). KEEP
  verdict unchanged: CLI gains + tool-traffic fidelity + live engagement
  carry it. Re-verify queued: bounding + deep-ctx arms with tools payload.
- PHASE 3 BOUNDING (adopted; deltas server-measured = noise per above, CLI
  battery is the real evidence): conf0.45+win12: temp0 +1.2% (4/6), temp1 +3.6%
  (6/6, 12/12 cells up), no probe worse than −0.2%, fp unchanged. Pinning
  justified: knobs gate ENGAGEMENT (bypass band was 87% of prose chunks);
  they cannot raise head ACCEPTANCE — tuning chapter closed at +1-3%.
- Live-traffic forensics method: decode chunk rates are BIMODAL under dspark
  (floor 30-40 bypass / engaged 45-90+); engaged share 13-19%; rare
  >90 t/s log rows are timestamp-collision artifacts (exclude from means).

## 5. CAPTURE PIPELINE — Stages 0+1 DONE
Docs: PLAN-CAPTURE-PIPELINE-20261029.md (5 stages, ~3.5-4wk) +
CAPTURE-SCHEMA.md (contract + v1.1 status).
- Measured dims from support GGUF (tools/gguf_meta.py -t --show-names):
  hidden=4096 (DS4_N_EMBD runtime g_ds4_shape!), in_dim=12288=3x4096
  (main_proj [12288,4096]), block 5, stages 3, vocab 129280, markov rank
  256 (w1/w2 [256,129280]), confidence [4352,1]=4096+256, 256 MoE experts,
  4 HC streams (hc_fn [16384,*]), MLA-ish attn (q_a1024, q_b→32768,
  kv_lora 512, 64 sinks). Tensor names mtp.<stage>.<suffix>.
- Engine module (ds4.c, +257 lines, before ds4_session_free):
  DS4_TRAIN_DUMP=<dir> gate (lazy, banner "capture ACTIVE"), DS4_TD_DEBUG=1
  lifecycle probe. Design: ds4_td_grab(s,token,pos) CALLED BEFORE
  prepare_impl (single-row dspark_target_hidden provably holds h[pos-1]
  pre-impl: last batch row copied into it + note_slot, ds4.c~29872; the
  draft forward then invalidates), stage ring 128/sess, ds4_td_close on
  prepare-ok, flush on cap + ds4_session_free; writer: train_pid<pid>.recs
  (12,320B fixed: pos,seed,streak,n_label,n_feat,flags,5 labels, 12288 fp8)
  + .idx.jsonl; e4m3 satfinite RZ encoder inline. Labels = checkpoint
  v[pos..pos+4] at flush; streak informational (pos-gap semantics; 255=open).
  Frontier-only records (prompt interiors not captured — correct scope:
  drafter conditions only at frontiers).
- v1.2 (2026-10-29, server smoke found D3): SERVER SLOTS REUSE ONE SESSION
  OBJECT across conversations (transition = ds4_session_rewind to prefix-reuse
  point). Flush-time label resolution then relabeled old-conv records with the
  NEW conv's checkpoint tokens (164 corrupted records in the first server
  smoke). Fix: labels snapshotted into RAM records (backfilled per flush while
  the owning checkpoint is alive) + ds4_td_segment_end() hooked at
  ds4_session_invalidate + ds4_session_rewind + rewind_speculative (final
  flush BEFORE checkpoint mutation, conv_id++, flag=1 next record). Segment =
  one request's frontier stream (agent continuations rewind per turn).
  Re-verified: 162 recs / 3 convs: labels[0]==seed 100%, chain 80/80, feats
  162/162 distinct, all three flush triggers exercised (cap/boundary/retire).
- v1 failure post-mortem (the lesson block): (a) tap post-impl read dead
  capture; (b) **ds4_gpu_tensor_read returns 1=SUCCESS/0=FAIL**
  (ds4_metal.m:9494) — v1's ==0 silently dropped every good read; (c)
  DS4_N_EMBD not compile-time-constant (VLA-in-struct build error). Debug
  probe (valid=1 + nfeat=0) separated (a) from (b). v1.1: 30/30 recs
  nfeat=3, 100% nonzero feat bytes, 10/10 per-pos distinct, |v| mean 1.7-2.9
  max<64; control run w/o env: zero files.
- Stage-2 RUNNING 2026-10-29: armed engine (PID 72805, v1.2 image) capturing
  live termbench to /Volumes/FireCuda520/ds4-train (26k recs / 309 MiB by
  16:0x, invariants green: chain 100%, labels0==seed, 100% distinct).
  2 suspect windows from disk-KV adoption bypass (see CAPTURE-SCHEMA v1.3/D4):
  v1.3 BUILT (adoption hooks + close() lineage-shift backstop + labels resolve
  per-cycle at close, flush=pure emit; +324 lines total) — LIVE since 16:2x.
  DAY-1 HARVEST BANKED (16:25): /Volumes/FireCuda520/ds4-train/20261029-day1/
  = 27,371 recs, 321 MiB, 42 conv segments, keep 99.44% (drop rule = n_label==0
  self-quarantine; the adoption bypass never mislabeled — avail=0 guard left
  153 records label-less instead). Depth coverage: 12.1k recs at 64-128k ctx
  (deepest frontier 105,128), 12.8k at 16-64k, 2.4k <16k. Tools:
  tools/td_validate.py (invariants, rc-gated) + tools/td_manifest.py
  (MANIFEST.json: per-conv stats, drop_indices, lineage_breaks provenance).
  Stage-3 sizing: day-1 = 14% of a 200k-record sanity corpus; keep the engine
  armed through normal agent work to accumulate.
  DAY-2 HARVEST BANKED (20:4x): 20261029-day2/ = 82,536 recs, keep 99.88%
  (97 frontier-tail drops), 97 convs, chain 100%, ZERO inbreaks (v1.3 proven
  over a full session: adoption hooks never leaked). Depth: 128k+ band =
  29,048 recs, deepest frontier 240,483 — first ultra-deep coverage.
  CORPUS TOTAL: day1+day2 = 109,907 recs (~55% of 200k sanity corpus).
  THEN: day3 7,514 + day4 85,750 + day5 1,620 = 204,791 BANKED (target
  passed); backups in ~/Backups/ds4-train-20261101/; active soak continues.
  Bounding re-verified controlled: +6.4% e2e tools-traffic prod config
  (supersedes Oct-29 +3.6% noise). See NIGHT-RUN-3-RESULTS.md throughout.
  Engine re-armed (PID 73865 -> new pid, active dir reset) 20:4x.

## 6. QUEUE / NEXT ACTIONS (ordered, with first steps)
1. Stage 2 capture day (relink DONE 10-29 13:35): at next natural restart or
   granted window, restart-vision-dspark with
   DS4_TRAIN_DUMP=/Volumes/FireCuda520/ds4-train (export or via SERVER env), day-1 verify: known conversation => recs ≈ committed tokens
   past frontier; drop-on-fail = g_ds4_td_io_failed stays silent; blade write
   ≈12.3KB/pos.
2. Stage 3 trainer (2-3 wk): torch mirror of ds4.c dspark graph;
   fine-tune from the official support GGUF (dequant mtp.*); objectives:
   stage CE vs next-5, markov transitions, confidence BCE calibrated @0.45;
   HOLDOUT GATE: p1≥.75, p3≥.55-.60 (official Vision-Exp head engaged
   accept ~87-90% but weak-class profile: cstruct avg_accept .12-.26,
   toolcall-decision .19-.20 — those are the targets). Fail gate => DFlash2
   lattice body on SAME dataset (block-diffusion + 2M path selector +
   two-tap convs; needs new Metal kernels; PLAN-DSPARK-PERF phase-5 adjacent).
3. Stage 4: trainer->support GGUF writer (mtp.* schema, align=32, Q8_0/Q4_K/
   F32, 12 KV metadata keys — tools/gguf_write.py roundtrip-proven).
   REVISION-PIN ESCAPE DONE 11-01: DS4_DSPARK_ALLOW_CUSTOM_SUPPORT env in
   support_model_checkpoint_compatible(); reject-by-default + accept-with-
   loud-warning both verified on a patched-revision clone.
   Ship gate: ≥+3% mixed-traffic tg, fidelity ≥ official.
3b. Re-verify with tools payload (post-gating-discovery): bounding arms
   conf sweep + deep-ctx engaged dspark server-side; expect deltas to move
   (noise -> real effects).
4. Parked/optional: exact-mode in-graph acceptance (PLAN-DSPARK-PERF P1) is
   the ONLY path to profitable temp>0 lossless speculation; c_add fixture
   guard retune; DSpark-for-Qwen impossible (external drafts rejected for
   qwen4 — ds4.c:73009-era finding stands).
5. Hygiene: /tmp/ds4-kv deletable; old-fp ladder stores age out via 512GiB
   budget eviction; dumps = sensitive (contain session text) — blade-local,
   never commit.

## 6b. WEAK-CLASS AUDIT + TOOLING REVIEW (2026-10-29 21:0x)
tools/td_classify.py (promoted after independent code-review pass) audits
harvests for structural control-token coverage. Corrected corpus facts:
- toolcall-own (seed == |DSML｜ marker): ALL 117 records have gap exactly 5.00
  = full-block (5-draft) acceptance AT boundaries. The drafter is STRONG at
  DSML boundaries in Vision-Exp (template continuation near-deterministic);
  the August "0.19 acceptance at tool-call decisions" was the 0731-head era
  and does NOT transfer. Skip-gaps (mean 8-13) sit mostly on plain-class
  records (scheduler skip-pauses after low-acceptance runs), NOT at markers.
- Only 2 control ids ever appear in generated windows for this family:
  128825 (|DSML｜) and 128822 (think-END); chat-template tool/file/role ids
  are prompt-side or emitted as plain text — zero-rows there are EXPECTED.
- Structural coverage: ~1,057 boundary instances (coverage-corrected ~1.2x
  for the 5/6 lattice: tokens at phase 5-mod-6 between cycles are invisible),
  0.82% of records touch a boundary. Tool-call emission is NOT the weak
  class to train; the residual weak targets are inside the 99% plain bucket
  (code/C vs prose split needs a BPE piece-decoder pass — open todo).
Review fixes applied: CAPTURE-SCHEMA §RECORD FORMAT replaced with the ACTUAL
ds4_td_diskrec contract (old Stage-0 draft never implemented; loaders must
mask labels by n_label, padding is ZEROS not 0xFFFFFFFF; no accept_bits/
conf_gate/logits fields exist); sidecar doc corrected (per-batch, not
per-record); classify: segment-keyed dedup (was losing ~2% instances to
cross-conv pos collisions), shared n_label==0 drop predicate (matches
manifest exactly), sentinel-excluded gap means, cluster-local coverage
correction; manifest docstring/code divergence + dict-mutation fixed;
validate: docstring honesty (breaks=NOTE not exit-1), float percentages.
Tools: td_validate.py, td_manifest.py, td_classify.py, vocab_ids.py (GGUF
kv reader with ds4's nonstandard type enum: bool=7, STRING=8, ARRAY=9).
TEXT AUDIT DONE (td_textclass.py — decodes plain-bucket windows via cached
vocab pieces; Ġ/▁->space, Ċ->newline; window=labels[0..nlab-1] since
labels[0]==seed; classes c-family|code|json-args|markup-args|markdown|math|
prose|other by regex): shares prose 48%, other 40% (5-token mixed-window
noise), code 8.4%, json 1.5%, md 0.9%, math 0.4%, c-family 0.3%.
ENGAGEMENT FLAT across content (mean-gap 3.1-3.9, no structural cliff);
least-engaged (gap==1%): c-family 12.1, math 11.7, markdown 8.8, prose 7.9
vs code 4.3, markup 0.7 — a mild echo of battery-era weaknesses, but the
0731-era weak-class story is dead under the Vision-Exp head. c-family is
also THIN (355 recs): any C-targeted training would need a dedicated capture
session, not passive accumulation.
Collection LIVE: PID 75402 armed -> ds4-train/active (371+ recs by 21:31).

## 6c. NIGHT RUN #2 (11-01 ~00:40): first training signal — read
**NIGHT-RUN-2-RESULTS.md**. Headlines: markov FT +49%/+54% rel on frozen
day-3 holdout, scaling curve STILL RISING at 195k pairs (persisted);
confidence AUC .74/.78 from captured h (features validated as informative);
expert dequant port bit-exact (golden tests); 12/12 tests; review
(ses_e6d2a1c8) fixed 3 stacked gguf_dq bugs + probe/markov hygiene. Engine
untouched; heartbeat soak continuing.
## 6e. NIGHT RUN #3 (11-01 01:15 -> 06:15): h-signal PROVEN. Read
**NIGHT-RUN-3-RESULTS.md**. Linear read of captured taps plateaus at top1
**.4014** / top3 **.5334** (32ep cosine, day-3 holdout) = 3.35x official
markov, 2.24x tuned bigrams; delta_from_h +.273; boundary .828; conf AUC
.735/.785. Schedule exhausted -> next discriminator is DATA vs MODEL-CLASS
ceiling (2x-corpus retrain answers it; soak continues). Mirror/parity
(engine window) = critical-path next. Engine untouched all night; soak +
heartbeats normal (631+ recs on PID 2559 by 04:30).

## 6d. NIGHT RUN #1 (2026-10-31 23:45 -> 11-01, autonomous, engine untouched)
Stage-3 groundwork complete: head reverse-engineering CLOSED (81-tensor
inventory == engine bind list == layout validator; 72 non-expert tensors ->
2.07 GB f32 npz on blade at ds4-train/tensors-20261101/), trainer/ uv
project scaffolded (torch 2.15 MPS; data loader + segment break-split +
day splits + model mirror; 9/9 tests encoding known corpus defects green),
BASELINE.json proxies computed (selection-bias caveat), gguf_write.py
byte-identical roundtrip of the official 6 GB file. Collection verified
still running throughout (PID 96888). Full state + decisions + next actions:
**PLAN-TRAINER-20261101.md** (read that, not this summary, when resuming).

## 7. HARNESS / METHOD NOTES (what worked)
- Engine lock: any ./ds4/ds4_test CLI run needs server STOPPED ("window
  open"). Server-mode tests (fidelity, bounding, phase2.5) run against the
  live loopback server instead — zero downtime, add light load only.
- Passive monitoring pattern: read-only log watcher, exit on 10-min quiet
  after activity, snapshot TSV (see scratch termbench_watch.sh).
- Request API: POST 127.0.0.1:8001 /v1/chat/completions supports
  temperature/top_p/seed/stream; usage.prompt_tokens_details.cached_tokens
  reveals anchor reuse. Vision-Exp THINKING eats small token budgets — probes
  need >=400 tokens; reasoning_content field carries the chain.
- Server logs rotate on restart (ds4.log = current window only; prior window
  in ds4.log.<ts>). tg analysis: regex decode-chunk lines by ctx band +
  label class (THINKING/DSML_START).
- Tools: tools/gguf_meta.py (v3 header + tensors; -t --show-names);
  tests/dspark_bounding_pass.py (server-mode arms + log attribution);
  tests/dspark_traffic_probe.sh; tests/dspark_temp_battery.sh;
  tests/dspark_followups.sh; scratch/: phase2_battery.py, phase2_metrics.py,
  phase25_extract.py (opencode.db -> head/full), phase25_run.py,
  tg_analyze.py, bounding_prompts.json. opencode sessions:
  ~/.local/share/opencode/opencode.db (38GB sqlite; session/message/part
  tables; tokens_input cumulative ≠ single-turn; reconstruct by time order).


End of state dump. Deeper history: DSPARK-VISION-EVAL-20261028.md §1-10,
PLAN-CAPTURE-PIPELINE-20261029.md, CAPTURE-SCHEMA.md; pre-existing context:
SPLASH-INTEL-20261023.md (DFlash2/decision-tree origin), PLAN-DSPARK-PERF.md,
PLAN-DSPARK-TEMP-SPEC.md, DS4FORK.md KVCACHE sections.

