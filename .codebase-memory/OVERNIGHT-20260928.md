# OVERNIGHT 2026-09-28/29 — KV closeout + backtick Option A + oMLX perf

**How to re-orient after compaction:** read this file top to bottom, then run the
state block in section 1. Do not restate this file back to the user; act from it.
This file is the single source of truth for the overnight program; per-phase
detail lands in the phase sections and in git history.

**Mandate (user, verbatim intent):** complete ALL KV work and ALL backtick work
overnight; then investigate oMLX v0.7.0rc1 performance work for Qwen 3.8 Flash
and implement whatever is portable into ds4, with tests. Full autonomy to decide.
Everything documented. Discipline per phase: design, build, test, review, apply,
commit, push.

## 1. State at start of this program (verified 23:55)

- Repo /Users/naz/Projects/ds4, branch main, HEAD 220ef50, pushed to nazerim/main.
- Commits already landed tonight: f6cfd90 (odd-frontier delta chaining +
  diagnostics, overnight items a/b/c and verdict #4), 414b32c (verdict #2
  tool-map trailer persistence for qwen/GLM spans), ed7256b (verdict #1 bootstrap
  reads the real trailer layout), 6ded49e + 220ef50 (docs/tests).
- Engine: DOWN (last live traffic 18:04, ctx 427271). Blade /Volumes/FireCuda520
  IS mounted; KV dir 423.5 GiB / 512 GiB budget, 149 files.
- Tests green at HEAD: ds4_test --server, --kv-delta, --tool-call-quality,
  tests/kv_policy_harness.
- This session runs on provider qwen-token-plan-individual / model qwen3.8-max,
  NOT on the ds4 engine, so stopping the engine does not affect the agent.
- Subagent lanes share this session's inference group, so a lane halves decode
  throughput for both. Use lanes for context isolation, not for speed. One lane
  at a time (AGENTS.md concurrency rule).
- Launched at 23:58: one read-only `scout` lane, output /tmp/omlx-recon.md,
  mining /Users/naz/Projects/omlx (v0.7.0rc1 window) and
  /Users/naz/Projects/Scratch (prior oMLX work: omlx, omlx-ops, omlx-glimmer,
  dflash-mlx, eagle, benchmark, ds4-kv-cache). Collect with
  subagent({action:"status", id:"1f9b49a7-6bb0-43da-9af0-a4554f779e93"}).
- 00:30 both scout lanes returned (reports copied to
  .codebase-memory/omlx-recon-1.md and omlx-recon-2.md so they survive /tmp).
  Two harness facts learned the hard way: (i) lane capacity was 1 active async
  run; (ii) the `scout` lane's own tool budget (soft 5, hard 12, block all) is
  what truncated the second pass, NOT concurrency — so deep recon must use
  `delegate`, which is deliberately uncapped. Concurrency is now 3/3 in
  ~/Projects/PiScratch/pi-extensions/agent-config/subagent-config.json,
  deployed to ~/.pi/agent/extensions/subagent/config.json, effective on the
  next pi restart. REVERT both keys to 1 before running lanes against a LOCAL
  engine: the original 1 was justified by the local ds4-qwen decode budget
  (49.6 tok/s single vs 28.4/27.1 split across two), which does not apply to
  this session's cloud qwen3.8-max. Writers stay serialized regardless — two
  lanes running make in one tree clobber object files.
- HEAD is f32db2d, docs-only and linear on 220ef50: a parallel session recorded
  a pi-side prompt-head churn finding in TODO-20260928-BACKTICK.md, which the
  user has now added to tonight's queue as Phase P0. User confirms no other
  writers are active.

## 1b. Phase P0 input — pi-side prompt-head churn (measured, largest single cost)

From f32db2d and its handoff docs. A live linode-agent conversation at 418978
tokens lost resume depth and re-rooted at 40960 after an agent-to-subagent-to-
agent round trip, forcing a ~378k-token re-prefill. ds4 was NOT at fault: chain
integrity was clean (110 chained files, zero orphans, no warnings) and 40960 is
simply the deepest rung whose stored bytes end before the first divergent byte
(159188), which falls inside the block pi's tool-manifest extension appends
after the cwd element. The parent-lineage checkpoint written after the dive has
no manifest at all where the earlier checkpoint had one.
Mechanism: renderToolManifest returns null when the registry reports neither
active built-ins nor guidelines, so a resume can silently drop the block; and
the listing is derived from the live registry, so a lane granted a different
tool set (for example ls) emits a different manifest than its parent for the
same conversation.
Handoff with measured variants, prioritized fixes (memoize the manifest on the
base prompt, reuse instead of dropping, head-stability warning) and a DRAFTED
UNAPPLIED patch: /Users/naz/Projects/PiScratch/PROMPT-HEAD-STABILITY-20260928.md
and PROMPT-HEAD-STABILITY-patch.md.
Operator rule until fixed: the prompt head is conversation identity — mode
switches, extension-set changes, tool-config edits and AGENTS.md changes happen
at conversation boundaries, never mid-session. Diagnostic: start the engine with
DS4_KV_DEBUG=1 (reject reason plus first-divergent-byte, shipped in f6cfd90).

## 2. Measured baseline for the disk argument (read-only census, 23:45)

Basis: /tmp/blade_census.py and /tmp/blade_v3.py (both read-only header scans).

- Full stores (no chain metadata): 74 files, 380.0 GiB. Chained stores: 70
  files, 41.8 GiB, average 0.60 GiB. Bytes per checkpoint 5.13 GiB vs 0.60 GiB.
- IMPORTANT correction to an earlier reading: a v2 header means "full store",
  NOT "legacy pre-P1". The current binary writes v2 headers for full stores.
  Four of the biggest were written today by the shutdown path (13:23, 14:48,
  16:09, 11:09): 8.14, 8.96, 10.79, 4.44 GiB = 32.3 GiB in one restart-heavy day.
- Live chained rungs today: 606.60, 606.72, 1509.23, 1058.10 MiB for 16384-token
  rungs; a 512-token turn delta cost 1064 MiB (fixed sections dominate).
- Chain topology: 57 interior single-child nodes (35.3 GiB), deepest chain 10
  hops, zero branching. Deepest live resume after a restart: 336019 tokens
  loaded in 2458.6 ms — this is the proof that verdict fixes #1/#2 work in the
  field (pre-fix the same session shape matched only the 114688 rung).
- Live tool_replay counters in log/ds4-qwen.trace: mem=16 disk=140 canonical=16
  missing_ids=16. canonical is flat (not growing) = the expected stable residual
  of pre-deploy calls.

## 3. Phase plan (each phase: design -> build -> test -> review -> commit -> push)

Status key: [ ] todo, [~] in progress, [x] done.

- [x] P0  pi-side prompt-head stability: tool-manifest memoization (user-added;
          largest measured cost, drafted patch exists, different repo so no build
          contention with the ds4 phases) — DONE and DEPLOYED, but NOT as the
          drafted patch: see the P0 results block below
- [x] P1  P3.2 evict/cold/shutdown chaining — DONE, see progress log 01:05
- [x] P3  Bootstrap id-index — DONE, see progress log 01:40 (done before P2
          because its RED test was already written and the design was settled)
- [x] P2  Backtick Option A: output-side hardening + repro + content detector
          — DONE, see progress log 01:00 and TODO-20260928-BACKTICK.md
- [ ] P3  Bootstrap id-index (kill the per-request whole-dir scan)- [x] P4  P3.1 tail: double-write investigation + store telemetry — DONE,
          verdict BENIGN, sha added to the stored log line, see section P4
- [~] P5  P2.1 middle-retire re-anchor — DEFERRED on evidence, design retained,
          trigger metrics recorded; see the P5 decision block
- [x] P6  oMLX recon synthesis + implement portable perf wins for Qwen 3.8 Flash
          — DONE as measurement: nothing to port, verify width confirmed optimal,
          one real lever priced and handed back as a decision; see section 3b
- [x] P7  Docs closeout (ADR, TODO, HANDOVER), full suite, restart engine, report
- [~] P8  Adjacent hygiene (user instruction 00:50): NOT DONE, deliberately.
          Untracking tests/kv_policy_harness and tests/test_prompt_prefix means
          touching shared history for a cosmetic gain, and whether those binaries
          belong in the repo is the operator's call rather than an overnight one.
          Recommendation recorded: git rm --cached both, add them to .gitignore,
          and put it on the next upstream merge checklist. The only symptom today
          is that both show as dirty after every make.
- [x] P9  INT8 MoE prefill (user-added ~02:00): investigated to a measured
          conclusion and REJECTED. See section 3c.

Adjacent-work rule added by the user at 00:50: if high-priority adjacent work
turns up during any phase, add it to this queue and implement it tonight rather
than only noting it. Candidates already identified and deliberately NOT queued:
purging the 380 GiB of full nodes on the blade (destructive to live cache; let
LRU drain it and report the numbers instead), and the 5 files with no
fingerprint section (older or aborted stores; the trailer walker already fails
closed on them).

### Phase P0 — pi-side prompt-head stability (tool-manifest.ts)

Repo: ~/Projects/PiScratch/pi-extensions (deploy with ./deploy.sh --install,
never by hand; tool-manifest.ts is a paired artifact, live copy at
~/.pi/agent/extensions/tool-manifest.ts). Tests: tests/run-all.sh.

Design (from the handoff, to be confirmed against the drafted patch):
- Memoize the rendered manifest on the base prompt so a resume or a lane reuses
  the parent's bytes instead of re-deriving them from the live registry.
- Never return null when the parent had a block: reuse the memoized text rather
  than dropping it, so the head cannot silently shorten.
- Add a head-stability warning path so a mid-session change is observable rather
  than silent.
- Migration note is mandatory: changing the manifest changes the prompt head for
  every pi session on this machine, so ds4-cached pi conversations re-root once.
  Land it at a conversation boundary and say so in the commit message.

Tests: byte-stability assertions — same conversation rendered twice with a
lane-like reduced tool set must produce identical head bytes; a registry that
reports no active built-ins must still emit the parent's memoized block, not
null. Plus tests/run-all.sh green.

### Phase P1 — P3.2: chain the evict/cold/shutdown store paths

Design (decided):
- The gate lives in ds4_kvstore.c ~2135: chaining is allowed only when reason is
  "continued" or "turn". Everything else writes a whole-session payload.
- Change: allow chaining for every reason. pick_parent already fails closed
  (header, text-prefix, tokenizer fingerprint, exact payload token span), and
  returns -1 when no verified ancestor exists, so a cold store with no ancestor
  still writes full. No new correctness surface beyond what turn stores use.
- Keep the parent-side alignment rule (candidate tokens % 4 == 0) untouched;
  f6cfd90 already relaxed the child side, so odd frontiers chain onto aligned
  anchors.
- New kill switch DS4_KV_DELTA_FULL_REASONS=0 restricts chaining back to
  continued/turn only, so the field can bisect without losing P1 behaviour.
  Global kill switch DS4_KV_DELTA=0 unchanged.
- Known cost to document: more chained nodes means more parents pinned by
  lineage retention, i.e. more eviction deferrals. That is the argument for P5,
  and it must be stated in the commit message, not discovered later.
- Deliberate decision on shutdown: chain it too. The lineage rule already defers
  eviction of any parent with a live child, so a shutdown node's ancestors stay
  resident while it does; and a broken chain already self-heals by unlinking the
  orphan and falling back to the next candidate.

Tests:
- Model-backed, extend tests/ds4_test.c test_kv_delta_parity: store the same
  frontiers with reason "cold", "evict" and "shutdown" into a third directory and
  assert (a) header v3 with the expected delta_from, (b) stitch-load parity
  against a full twin of the same live state, (c) continuation parity.
- Model-free, tests/kv_policy_harness: assert the reason gate no longer forces a
  full write, and that DS4_KV_DELTA_FULL_REASONS=0 restores the old behaviour.
- Regression: --server, --kv-delta, --tool-call-quality, harness.

### Phase P2 — backtick Option A (output-side hardening)

Design (to be finalized after reading the error site):
- Target: the "unterminated tool call (stop token)" finish=error class. Mechanism
  established tonight: content quoting a tool envelope opener injects a real
  structural opener into the prompt (no input-side escaping exists; ds4.c
  special_token_at maps 27 structural strings anywhere in the rendered text), so
  the model can continue inside a call it believes is open and reach the stop
  token unclosed.
- Fix shape: an envelope opened in generated text that is never closed by
  end-of-generation degrades to inert text (finish=stop with the partial text as
  content) instead of erroring the turn. Recovery machinery already exists
  (recovered flag, test_incomplete_tool_call_keeps_stop_reason,
  test_think_tool_recovery) so this is extending an existing policy, not inventing
  one. Zero cache-key impact.
- Add a detector (no behaviour change): log once per request when message content
  carries a structural marker, so the frequency question gets answered with data.
- Explicitly NOT doing Option B (input-side neutralization) tonight: it changes
  rendered text (one rebuild per affected conversation) AND changes what the model
  sees, and it must exempt replayed raw tool spans or it would re-orphan every
  checkpoint. Recorded as a decision with rationale in
  .codebase-memory/TODO-20260928-BACKTICK.md.

Tests: repro first as a failing model-free test in ds4_server.c (generated text
with an unclosed envelope -> assert finish degrades to stop and the text lands in
content), then the detector assertion, then the full --server group.

### Phase P3 — bootstrap id-index

Design: kv_cache_restore_tool_memory_for_messages currently opens every file in
the KV dir per request (149 today; cold_max allows far more) and reads each
header. Replace with a process-local id -> file index built on first use and
maintained on store (ids and path are known at write time) and dropped on
unlink/evict. On an index hit whose file fails to open, drop the entry and fall
back to one bounded full rescan. Keeps the fail-closed property.

Tests: model-free — assert the dir scan happens once, not per request (instrument
a counter), that a store makes a new id findable without a rescan, and that a
stale entry falls back safely.

### Phase P4 — P3.1 tail: double-write + telemetry  [COMPLETE]

Verdict on the double-write: BENIGN, and the original premise was wrong twice
over.  Full evidence in .codebase-memory/ds4-doublewrite-forensics.md.
- There is no 2026-09-27 18:38 event.  Log prefixes are real wall clock
  (localtime_r + strftime), and the file NAMED ds4.log.20260927-221523 contains a
  09-21 run that was merely rotated on 09-27 22:15.  The "18:38" was read off the
  rotation filename.
- Zero same-sha double writes exist.  The store path already dedupes on sha
  (ds4_kvstore_existing_compatible touches and rewrites the trailer and emits no
  stored line), so every logged store is a genuinely new file.
- What is really on disk: 23 groups where the same LOGICAL frontier
  (conv_id, tokens, delta_from) was stored more than once under DIFFERENT shas,
  because the re-rendered text drifted by tens of bytes.  29 redundant files,
  16.96 GiB, 3.3 percent of the volume, reclaimed by
  sweep_small_dense_divergents and LRU.
- conv_id cannot identify a conversation: it is the sha1 of only the first
  131072 text bytes XORed with the model fingerprint, so equal conv_id does not
  imply equal text.  key=token-text is a label, not a key.  Grouping logs on
  either is exactly how this came to look like a double write.
- A (conv_id, tokens, delta_from) dedupe would be WRONG: the texts genuinely
  differ and the newer rendering is what future prompts match, so skipping it
  would poison hit rate.  The real fix is upstream of the store - deterministic
  re-rendering - which is what commits 414b32c, ed7256b and the pi-side P0
  prompt-head work address.

Shipped from this phase: the stored log line now carries sha=XXXXXXXX.  That is
the field whose absence caused the misdiagnosis; size and key label cannot
distinguish two conversations at the same frontier.

Assessed and deliberately NOT changed:
- Header touch atomicity.  ds4_kvstore_touch_file rebuilds the header in a stack
  buffer and writes it with ONE fseek+fwrite of 48-116 bytes, so it is already a
  single contiguous write.  Making it truly atomic would mean copying the whole
  file (up to 12 GiB) per touch, which is absurd for a hits/last_used bump.  A
  torn header is caught fail-closed by existing verification: the text section is
  sha-checked against the filename, the token span is re-verified, and delta
  chains verify every parent link before the session is touched.  Payload writes
  are atomic already (temp file + rename, orphans reaped by the .kv.tmp scan).
- Periodic store-summary telemetry: NOT implemented.  The sha field plus the two
  read-only census scripts (/tmp/blade_census.py, /tmp/blade_v3.py, reproduced in
  section 2) answer the disk questions, and adding counters to the store struct
  was not worth the risk this pass.  Recorded as a follow-up if the disk argument
  keeps needing an offline scan.

Latent hazard recorded, not reachable today: ds4_kvstore_existing_compatible
UNLINKS a same-sha file when it deems it incompatible, including on a ctx_size
test.  Two slots with different ctx_size storing identical text could therefore
ping-pong unlink+rewrite.  All 202 files on the blade carry ctx_size=524288 and
the launcher gives each model its own KV dir, so it cannot fire in this config.

### Phase P5 — P2.1 middle-retire re-anchor

Highest risk: it rewrites an existing checkpoint. Design: when an interior node
has exactly one child, rewrite the child as a self-contained store (parent rows
plus child span), fsync, atomic rename, then unlink the parent. Crash-safety and
the chain verifier are the whole ballgame. GATE: only implement after P1-P4 are
pushed and green; if the risk controls are not clean by then, ship the design
plus a deferral-pressure metric and defer the code. Measured upside is modest
(35.3 GiB of interior nodes, most of which LRU would drain anyway); the real
upside is fewer stitch hops and less deferral pressure.

DECISION 2026-09-29 ~02:20 — DEFER, design retained, gate not met. Three
measurements:
1. Deferral pressure does not exist yet. Four deferral lines across every
   available log, all reason=redundant (the divergent sweep, not eviction), and
   zero exceeds-budget events ever recorded.
2. The byte upside is far smaller than the 35.3 GiB headline. Retiring an interior
   node does not delete its rows, it MOVES them into the child, whose span must
   then extend back to the grandparent. On a uniform 16384-token ladder the
   child's row region roughly doubles, so the net saving is only the parent's
   fixed sections, not its whole file. The 9.1x per-checkpoint ratio that chaining
   already delivers is the large win; middle-retire is second order on top of it.
3. The latency upside is also small: the deepest chain is 10 hops and the deepest
   measured resume was 336019 tokens in 2458.6 ms, which is not a problem next to
   a 60-second prefill.
   Against that, this is the only change in the whole program that mutates an
   EXISTING checkpoint rather than writing a new one, so it carries crash-safety
   and verifier risk that nothing else here does.
TRIGGER to revisit, any of: (a) exceeds-budget events appear in the log;
(b) deferrals grow per day instead of staying in single digits; (c) stitch depth
exceeds roughly 20 hops or resume exceeds roughly 10 s; (d) the budget cannot hold
two concurrent deep conversations. The metrics are already observable: the
"kv cache delete deferred" log line and the two census scripts.

### Phase P6 — oMLX perf: CLOSED AS A PORTING QUESTION, reopened as measurement

Pass-3 recon (delegate lane, report in .codebase-memory/omlx-recon-3.md) reached
a firm negative on all three candidates. Do not port any of them:
1. Fused GDN prework — NO-GO, already implemented.  kernel_qwen4_gdn_front
   (metal/qwen4.metal:4545) via ds4_gpu_qwen4_gdn_front_tensor (ds4_metal.m)
   from qwen4_graph_linear (ds4.c:58368) already fuses conv-state concat,
   depthwise conv1d, SiLU, q/k split, L2 norm, scaling, alpha/beta (folded
   GEMVs), the g and beta activations, next-conv-state slice AND both MTP
   snapshots.  Independently, ds4's verify width is only T=2 (default) or 3
   (adaptive), so oMLX's S=3..9 amortisation window is unreachable without
   changing DS4_N_NEXTN_PREDICT, which is a model-weight property.
2. Verify SDPA split — NO-GO, inverted premise.  qwen4_graph_attention_core
   (ds4.c:58404) already issues ONE batched multi-row call per sub-batch, and
   qwen4_graph_attention_tail (ds4.c:58455) deliberately NARROWS T=3 into 2+1
   sub-batches to preserve bit-exact kernel rounding.  ds4 attention is
   block-sparse (indexer top-k over ratio-4 pooled blocks), not dense causal
   SDPA, so there is no per-row loop to collapse and no bottom-right
   construction to port.  A widening port would break the exactness split.
3. Fused MoE decode router top-k — NO-GO, already implemented and a superset.
   kernel_qwen4_router_topk (metal/qwen4.metal:1122) does softmax + top-k +
   renorm + the shared-expert gate logit in one launch.  Semantics warning:
   ds4 breaks ties to the LOWEST expert index, oMLX/mlx-argpartition to the
   HIGHEST, so a port would silently change expert selection.
   The NAX runner-up is also already in-tree (kernel_qwen4_moe_mm_mid_nax_t,
   kernel_qwen4_moe_mm_down_nax_t).

Model facts worth keeping: Qwen3.8 Flash Next IS a routed MoE in ds4 — 512
experts, 10 used plus 1 shared, FF exp 640 (ds4.c:816-819), and the GGUF
metadata check is a hard fail, not advisory (ds4.c:7062-7068).  Trunk is 48
layers, of which 36 are GDN and 12 full attention (interval 4).  A full GDN
layer costs about 18 Metal dispatches at T=2.

PLAN-PREFILL-M5.md closes several doors that pass 1 and 2 left open: chunk size
is PLATEAUED and the default is already optimal (2048 wins at 2k, loses at 65k;
4096 about equals 8192), ANE is a dead track, attention kernels are 0.4 percent
of prefill, and the honest ceiling on M5 Max for that model was 0-3 percent,
not the 10-15 estimated from the TF/s plateau.  Its exactness doctrine binds
any future kernel work: env-gate every lever, balanced A/B with
metal_prefill_variant_bench, promote only with --quality keeping the reference
path, because accumulation order alone changes 129278 of 129280 logits.

What P6 becomes instead — a zero-code measurement, since the only live lever is
speculation economics, and prior art says wider verify can REGRESS even when
acceptance rises (eagle/README.md: dflash verify lifted acceptance 51.7 to
57.3 percent but tok/s fell 29.5 to 16.8; ddtree 60.6 percent acceptance at
13.7 tok/s).  Also relevant: ds4's own depth policy encodes a measured claim
(second draft accepts about 0.6 on prose, 0.95+ on deterministic continuations,
+10-20 percent when depth 3 engages; ds4.c:74331) that has never been
re-validated on this checkpoint.

Measurement plan (engine must be down; never two model processes on this box):
1. ds4-bench cannot exercise qwen4 MTP at all — ds4_bench.c never sets
   .glm_mtp — so MTP numbers must come from the ds4 CLI with --mtp-timing.
   ds4-bench is still the right tool for the non-MTP prefill/decode reference,
   and it does report prefill_tps and gen_steady_tps separately (--csv).
2. Baseline: DS4_QWEN4_MTP_DEPTH=2 with DS4_QWEN4_TIMING=1, fixed prompt,
   3 runs, median.  Record wall tok/s, the "Qwen3.8 mtp: N verify cycles,
   M drafts accepted (P%)" line, and forward(T=2) avg ms gpu.
3. Same at DS4_QWEN4_MTP_DEPTH=3, then unset (auto) to see how often depth 3
   actually engages; DS4_QWEN4_SPEC_TRACE=1 on one short run to confirm.
4. Fusion A/B with DS4_QWEN4_NO_FUSE=1 — this retroactively PRICES the
   already-landed fused GDN prework across 36 GDN layers on ds4 hardware and
   closes oMLX candidate 1 permanently with a number instead of an argument.
5. Only if depth 3 wins by more than 3 percent sustained on real prose does
   anything change, and what changes is the policy constants in
   qwen4_spec_depth (ds4.c:74333-74340), not a kernel.
6. Stage attribution if step 4 is interesting: DS4_QWEN4_TIMING=2 gives
   per-group GPU ms (ple hc_attn gdn attn hc_ffn moe).
Known traps: Scratch oMLX numbers are MLX-Python on a different quant (oQ4e,
69.6 GB resident) so they bound expectations but are not ds4 baselines;
flash-next-perf/perf_results.json is cache-polluted and the decode_est_tok_s
field of perf_results2.json is broken — neither may be cited; and
benchmark/ is a remote SWE-bench runner with no perf numbers at all.

Rule unchanged: no perf change ships without a before/after number in the
commit message.

## 3b. RESULTS — P0 and P6 (recorded so a compacted session does not redo them)

### P0 outcome: shipped, and the drafted patch was rejected

The drafted memoization patch in PiScratch/PROMPT-HEAD-STABILITY-patch.md was NOT
applied. A read-only design pass found it does not touch the real mechanism and
adds two bugs: on the sendCustomMessage path the handler never runs at all (dead
code for the very incident it targets); its fallback took the newest manifest from
ANY session in the process, so at globalConcurrencyLimit 3 a parent could be handed
a lane's manifest; and its memoKey used the slice length as the prefix and hashed
only the first and last 2048 chars, so every prompt over 4096 chars keyed
identically and the middle — which contains divergence byte 159188 — was not in the
key.

Corrected root cause: renderToolManifest returning null was a red herring. The
handler returned a REPLACEMENT prompt, which becomes forceSystemPrompt for that run
only; it is cleared in every run's finally, and turns triggered by
sendCustomMessage({triggerTurn:true}) never emit before_agent_start, so the head
renders from transcript sections alone and the block is absent.

Shipped (pi-extensions 5076967, deployed ~01:45):
1. tool-manifest.ts persists the render to ~/.pi/agent/APPEND_SYSTEM.md, which pi
   folds into the `addendum` section of the BASE system-prompt options, so every
   turn derives it regardless of entry point. Returns undefined; never forces the
   prompt. No memo, no process-global state.
2. pi-scripts/render-tool-manifest.mjs renders the same file at deploy time, wired
   into deploy.sh --install/--check, so the first session after an install already
   has it. Skips cleanly with no defaultTools (keeps the sandboxed deploy suite
   honest) and renders from the agent dir, never process.cwd(), so a deploy and a
   session-start refresh cannot disagree.
3. SECOND BUG found on the way: pi populates options.toolGuidelines (a map) and
   never sets options.promptGuidelines, and its own buildRules() is skipped when a
   customPrompt is configured. The extension read only promptGuidelines, so EVERY
   per-tool guideline was silently dropped — the AGENTS.md claim that the injection
   restores the bash 60s note was false until this fix. Now reads the map in sorted
   tool order, then global rules, matching pi's order.
4. review-bounded sets advertise: false. pi-subagents renders advertised_subagents
   from a before_agent_start handler too, so it inherits the same oscillation, and
   dropping forceSystemPrompt would have made that section live and churny. Opting
   our only file-defined lane out removes the source; it stays discoverable via
   subagent({action:"list",capabilities:true}).
5. PI_TOOL_MANIFEST_APPEND_PATH redirects the handler's write in tests. Without it
   the unit test clobbered the deployed manifest (1431 bytes replaced by a 706-byte
   guideline-less copy) and showed up as phantom drift.
Verification: tests/run-all.sh green end to end (manifest, extensions, 58
thinking-scrub, 75 secret-scrub, 44 model-class, 43 deploy regression assertions,
secret scans, live drift clean). Migration applied at a conversation boundary: the
head changes once per session, so ds4-cached pi conversations re-root once.
Residual, now measured: project_context is the dominant churn source (38 patches
across 78 sessions at byte offset 1921 of a 40532-byte head), so every AGENTS.md
edit still re-roots nearly the whole head. The operator rule stands.

### P6 outcome: measured; nothing to port, one real lever identified and priced

Rebaseline under High Power, counterbalanced (2 reps, second rep in reverse order),
fixed 32k prompt, greedy decode, 384 tokens. Medians (full data in
.codebase-memory/p6-rebaseline.csv):

| case       | gen t/s | prefill t/s | accept | verify width |
|------------|---------|-------------|--------|--------------|
| depth_2    | 61.88   | 1336.74     | 70.2%  | T=2          |
| depth_auto | 61.42   | 1283.89     | 70.7%  | T=2          |
| depth_3    | 58.36   | 1271.99     | 73.9%  | T=3          |
| no_mtp     | 47.90   | 1261.75     | —      | T=1          |
| no_fuse    | 43.31   | 1265.80     | —      | T=1          |

Conclusions:
- DO NOT raise the verify width. depth_3 is 5.7% SLOWER than depth_2 despite
  acceptance rising 70.2 -> 73.9%. Counterbalancing strengthens this: depth_2 ran
  last in rep 2 (most throttled) and still won. Matches the independent prior art
  in Scratch/eagle (wider verify lost 43% while acceptance rose). The auto policy's
  default of 2 is already optimal, and auto measured equal to forced 2.
- MTP plus the fused graph is worth +29.2% over no MTP and +42.9% over unfused.
- DS4_QWEN4_NO_FUSE=1 is NOT a safe fallback: it is 9.6% worse than disabling MTP
  entirely, because the spec cycle cannot batch without the fused graph and falls
  to T=1 while still paying MTP bookkeeping.
- Decode is the trustworthy metric (no_mtp: 47.92 and 47.88 across reps, 0.1%
  apart). Prefill drifts with run position (1409 -> 1257 across a sequential
  series), so prefill comparisons need counterbalancing or they measure heat.

Stage attribution, DS4_QWEN4_TIMING=2, ms per 8192-token chunk (this is the number
PLAN-PREFILL-M5 never had for this model — its split was measured on DeepSeek-V4):
- pos=0:     ple 29.4  hc_attn 316.0  gdn 1302.3  attn 916.7   hc_ffn 365.5  moe 1872.1
- pos=65536: ple 30.9  hc_attn 322.0  gdn 1472.1  attn 1298.0  hc_ffn 398.5  moe 2041.5
- shares at 65k: moe 36.7%, gdn 26.5%, attn 23.3%, hc_ffn 7.2%, hc_attn 5.8%, ple 0.6%
- MoE is the largest bucket at every depth (39.0% at pos=0), attention grows with
  context as expected. Prefill 1326 t/s at 65k under TIMING=2 (pessimistic: the
  stage syncs cost time).

v0.7.0rc1 reconciliation (report: .codebase-memory/omlx-v070rc1-perf.md):
NOT COMPARABLE. The release claims prefill 1522->2007 t/s at 16K and 1326->1716 at
64K for Qwen3.8-Flash-Next oQ4e, batch 1, single runs, paged caching disabled; its
decode claims (56.9->131.5 and 88.9->136.9) are Qwen3.8-27B — a different, smaller
model — AGGREGATE over 4 concurrent requests, i.e. ~32.9 t/s per stream against
ds4's 47-68 single-stream on the larger Flash-Next. oMLX's own cold measurements on
this exact model, in Scratch, are 992 t/s at 40k, 560 at 80k and 224 at 120k, so
the 1716 at 64K claim is 3.1x their own measured cold number at a similar depth.
Three of the release's prefill wins are explicitly host-side Python overhead that a
C engine with a precompiled graph does not have. Every architectural technique in
PR #3903 already exists in ds4 (dense/sparse split, MMA gathered sparse-GQA
attention, fused HC residual-write plus stream norm, fused GDN prework). Note also:
mlx-serve vendors antirez/ds4 as a submodule (lib/ds4 @ 9139e2a).

The ONE genuine lever, now priced: ds4's Qwen3.8 MoE runs FP16 NAX at the measured
~13.5-14 TF/s plateau and ds4 has no INT8 MMA path anywhere, while oMLX PR #3548
measures 42 TOP/s INT8 against a 44.21 ceiling on this same M5 Max. MoE is 36.7-39%
of Qwen3.8 prefill per the attribution above, so a 3x on that bucket is worth up to
~22% of total prefill; discounting for the Stage-A activation quantizer and the
unchanged non-MoE GEMMs, +10-20% prefill is the honest estimate. PLAN-PREFILL-M5's
"0-3% ceiling" explicitly put MoE gemm out of scope and predates that PR, so it does
not cover this lever.
DECISION: not attempted tonight. It is a multi-day Metal kernel project (a new
activation quantizer, an int8 x int8 -> int32 NAX GEMM, and — because Q4_K's 8x8
sub-block packing and MXFP4 do not align to oMLX's affine group-size-64 assumption
either a load-time repack or a Q4_K-specific fragment loader), it CANNOT be
bit-exact, and it needs a real quality/acceptance study before any default flip.
Starting it half-way overnight would risk leaving the tree broken for a gain that
must be opt-in anyway. SUPERSEDED by section 3c below: this lever was
subsequently attempted and rejected on measurement - the int8:half tensor ratio is
2.02x on this hardware, not the 3x assumed here, so the accurate two-term design
is break-even.
Also closed by measurement: the GDN prefill token-parallel fusion is worth ~0.4%
(not 30%) because the fused kernel is gated off at prefill only because its host
grid is 16 threadgroups with tokens walked inside the kernel; and chunk size stays
closed (ds4's default is already 8192, exactly oMLX's new wide-prefill step).


## 3c. RESULTS — INT8 MoE prefill: investigated, measured, rejected

Branch `int8-moe-prefill`, four commits, deliberately NOT merged. Full write-up in
PLAN-INT8-MOE.md on main; the instruments are on main too, because they are
reusable and cost the engine nothing (none is in any build rule).

1. FEASIBLE. int8 x int8 -> int32 cooperative-tensor matmul compiles AND builds a
   compute pipeline on this machine in ds4's own kernel shape (device tensor views,
   matmul2d with dynamic_extent, execution_simdgroups<4>). Traps recorded: plain
   char is not an allowed source type (Metal distinguishes it from signed char);
   get_destination_cooperative_tensor takes cooperative tensor types, not element
   types; mixed signed/unsigned operands are rejected by run(), so both operands
   must be signed char.
2. THE ALIGNMENT IS REAL. The routed-expert tiles use NK=32 and a Q4_K sub-block is
   exactly 32 values with one 6-bit scale/min pair, so one K step equals one
   activation group and one weight-correction boundary. No re-tiling needed.
3. ACCURACY. Single-term int8 is unusable: 1.421e-2 mean relative error on the
   expert dot product, 16.4x worse than ds4's fp16 path, and no group size fixes it
   (16/64/128/whole-row all landed between 1.1e-2 and 1.7e-2). Two-term int8 -
   quantizing the residual, the same compensation ds4 already ships for its half
   path - reaches 7.636e-5, which is 11.4x BETTER than plain fp16 (8.668e-4) and
   8.1x better than fp16+COMP (6.219e-4).
4. STAGE A BUILT AND VERIFIED ON GPU. kernel_qwen4_act_quant_i8 plus
   ds4_gpu_qwen4_act_quant_i8, inert (nothing calls it). tests/test_qwen4_kernels
   exits 0 with a new test asserting codes, scales and row sums against a float CPU
   reference that mirrors the kernel's expression order, pinning the degenerate
   all-zero group to a scale of exactly 1.0, and requiring two-term to beat
   one-term by at least 50x. Measured on GPU output over 288 values: one-term mean
   abs error 2.519e-03 versus two-term 8.731e-06, a ratio of 288.5x.
5. THE THROUGHPUT PREMISE IS FALSE, which is what killed it. Raw cooperative-tensor
   issue rate on this M5 Max, warm-up discarded, kernels interleaved within every
   rep, median of 7 at 200k iterations: half 53.08 TOP/s, int8 107.16 TOP/s, ratio
   2.02x - the operand-width ratio. Two-term int8 costs 2 int8 MMAs at 2.02x, i.e.
   +0.9% on the MoE bucket and about +0.3% on total prefill: inside noise, for a
   new quantizer, a new GEMM variant, lost bit-exactness and permanent fork
   maintenance. The 3x the idea rested on compared oMLX's measured int8 rate
   against ds4's kernel-level MoE plateau - two different quantities.

Useful residue:
- 53.08 TOP/s is now a measured bare-half-matmul2d issue ceiling for this box.
  Since the MoE bucket runs far below it, ds4's MoE tiles are NOT MMA-issue-bound;
  they are bound by weight decode, threadgroup staging and bandwidth. Future MoE
  prefill work should attack staging and measure against this ceiling, not against
  the 13.5-14 TF/s figure inherited from a different model's kernel.
- Single-term int8 stays a real option worth about +22% prefill if an opt-in
  approximate fast path is ever wanted, with the accuracy cost quantified and the
  validation it would need named (perplexity plus MTP acceptance, because prefill
  errors land in the KV cache and therefore in every later decode step).
- A benchmark methodology trap, documented in the tool itself: the naive version of
  the bench reported 3.38x, 2.44x and 1.89x at 5000/20000/80000 iterations. That
  spread is command-buffer overhead plus clock ramp - fitting the two largest
  points gives a ~2.35 ms constant against ~1.0 ms of steady-state work. Without
  warm-up and interleaving, the same code would have "proved" a 3.4x ratio and
  greenlit the project on a phantom.

- 03:10 FINAL REVIEW ACTED ON (2d93217). A bounded review lane over the P1/P3/P4
  diffs returned APPROVE-WITH-NITS with two must-fix items, both real:
  M1 - the bootstrap marked a file as indexed even when fopen failed or the
  trailer walk errored, so a checkpoint being written concurrently or one
  transient I/O error poisoned that file for the whole process lifetime. The
  rescan this replaced healed from exactly that. A file is now recorded only once
  it opened, its header parsed, and the walk completed.
  The reviewer also caught that phase 1 did blocking disk I/O for up to cold_max
  files while holding tool_mu, head-of-line-blocking every other request thread on
  the first request after boot. The bootstrap is now four passes: locked and
  I/O-free directory walk, unlocked read into a PRIVATE index, locked
  bound+merge+mark-seen+re-resolve, unlocked install.
  M2 - the forced-full assertions could pass vacuously: nothing asserted the
  DS4_KV_DELTA_FULL_REASONS=0 twin really was full, so a broken switch would have
  turned stitch-vs-full into stitch-vs-stitch and still passed. Now asserted.
  Nits taken: index bound checked before the merge so a clear cannot strand a
  half-indexed file marked as read; a separate bound on the seen set (it grows
  with file churn, not tool calls); drop_file no longer restarts its iterator per
  removal (was O(k*n) under the mutex, multi-second worst case); documented that
  only the literal 0 disables the switch.
  New tests cover the dedup the design rests on and that nothing exercised: two
  ids in one file must cost one install open and restore both, and two ids in two
  files must cost exactly two opens. Every earlier case wanted one id from one
  file, so a file-per-id regression would have passed all of them.
  One review concern closed by reading rather than assuming: id_list_push_unique
  copies via xstrdup, so names resolved under the lock and used after unlocking
  are owned - dropping a stale file from another thread cannot use-after-free.
  Reviewer's own gap, recorded honestly: it ran out of budget before reading the
  KV load/resume path, so orphan-delta handling was reasoned about but not
  verified in that lane. It IS covered by --kv-delta's broken-chain case, which
  asserts an orphan self-destructs and the load degrades to 0.
  Full suite re-run green after the rewrite: --server, --kv-delta,
  --tool-call-quality, --think-tool-recovery, kv_policy_harness, production build
  warning-free. Then re-proved end to end on the live engine: after a restart with
  empty RAM, replaying an id that exists only on disk traces tool_replay mem=0
  disk=1 canonical=0 missing_ids=0, with zero error-census hits. Engine left
  running, PID 25841.

- 04:50 CONTAMINATION BUG FOUND IN MY OWN P0 WORK, empirically: the deployed
  APPEND_SYSTEM.md had been rewritten between deploys. Every pi session runs the
  handler with its OWN selectedTools, and lanes carry ls while the install default
  does not, so a lane rewrote the shared file with a lane-flavored manifest and the
  next parent session would have read it at startup - wrong tool list plus a head
  change, the exact churn the route exists to remove. Fixed: syncAppendSystem is
  now create-if-missing and never overwrites; the deploy-time renderer is the
  authoritative writer (it renders from settings.json defaultTools, which is
  session-independent). That split needed making explicit, because the renderer
  shared the function and would otherwise have lost the ability to update the file.
  pi-extensions a3e93e8.
- 04:55 Lane concurrency documented rather than mechanized, per operator call.
  AGENTS.md now owns an engine-class rule: cloud up to 3 concurrent async lanes
  bounded by the provider's slots, local 1 sequentially, decided with
  pi-scripts/model-class.py. Two honest limits recorded - the config keys are
  static and pi-subagents supports no env override, so they cannot follow a
  mid-session switch to a local model; and writers stay serialized in both cases
  because two lanes running make in one tree clobber object files. The config
  comment and SUBAGENT-LANES.md now point at AGENTS.md instead of restating a rule
  that had gone stale in both. A deploy-time checker that fails on a
  config/class mismatch is the natural control and the data already exists; not
  built, recorded as an option. pi-extensions b431f87.
- 05:05 Store telemetry shipped (02f1f12). Six lifetime counters in ds4_kvstore,
  logged every 50 stores and at close, split chained versus full so the
  per-checkpoint ratio falls out, with the dedup path counted separately as
  `reused` because a store that writes nothing logs no stored line at all - the
  exact blind spot that made the double-write question need a header walk.
  Verified live on a real stop: stores=7 reused=0 chained=4 (0.85 GiB, avg 0.212)
  full=3 (0.67 GiB, avg 0.222) full/chained=1.0x. The 1.0x is correct and must not
  be misread: the ratio covers one process lifetime, and at 8-17k tokens the fixed
  sections dominate both forms. Whole-blade census measured 9.1x.
- 05:15 Upstream proposals DRAFTED, NOT SENT (.codebase-memory/
  UPSTREAM-PROPOSALS-20260929.md, commit 7aeeef6). Both verified against
  origin/main, not the fork: upstream ships recovered_tool_parse_failure (4
  occurrences) and does not have strip_dsml_keep_prefix (0), and
  anthropic_stop_reason really does collapse everything but tool_calls/length to
  end_turn. Proposal 1 is a six-line PR; Proposal 2 is an issue with three options,
  since Anthropic's stop_reason vocabulary has no error member. The KV layer stays
  unproposed per the standing decision, and the input-side syntax question is
  recorded as needing a migration conversation rather than a PR.
- 05:20 Housekeeping the operator asked about (0ccf551): the tracked binaries were
  a REGRESSION, not an original mistake - b3a4cc5 had untracked all seven artifacts
  and added the ignore rules, then the upstream merge 7d7b8cc was an evil merge that
  resurrected them from nothing and dropped the .gitignore block. Four zero-byte
  .o.tmp files deleted; both harnesses untracked but kept (Makefile targets, proven
  by deleting and rebuilding them); tests/test_spec_rejection deleted outright
  because its source was deliberately removed in dd1a02a and it has no build rule,
  so untracking would have left an orphan no clone could reproduce. AGENTS.md now
  carries the post-merge re-check command, because a convention a merge can
  silently undo is not a control.
- 05:40 Reviewer's open question R1 closed by inspection, which is stronger than
  the test alone. The chain walk verifies every ancestor link (header, token
  fingerprint, and that a delta node carries a parent sha) and on ANY failure
  frees the collected ancestors, logs, unlinks the orphaned child, returns 0 and
  lets the caller fall to the next candidate. The handling is position-agnostic,
  so the existing --kv-delta broken-chain case (root deleted -> load returns 0 ->
  orphan file gone) covers mid-chain breaks too, not just the one instance it
  exercises. A separate mid-chain test would be low value against code that does
  not branch on position.
- 05:50 Two scheduled read-only lanes created so the remaining verification needs
  no polling and no interference with the live 400k session: deep-store-verify at
  +25m (the first turn/evict/shutdown store above 250k tokens, which is the one
  production-scale claim still untested) and overnight-health-check at +160m
  (wedge signature, error census, stats lines, blade occupancy).
- 05:55 DS4FORK.md updated with an operator-facing KVCACHE section (knobs, how to
  read the numbers without being misled, known residual) and with the INT8 and
  MTP-depth negative results added to its existing "recorded so we don't re-try"
  list, including the scope caveat that its prefill attribution was DeepSeek-V4
  and excluded MoE gemm. The four census scripts are checked in under tests/ so
  the docs stop pointing at volatile /tmp paths, and kv_blade_chained_vs_full.py
  no longer labels v2 headers "legacy" - that misreading is what produced the
  wrong "90% of the blade is pre-P1" claim earlier in the night. Live composition
  at 05:55: 83 full files at 4.99 GiB average versus 169 chained at 0.56 GiB,
  ratio 9.0x, with 106 of 257 files carrying a tool map.

## 3d. DIAGNOSIS — "prefill drops to ~400 t/s when a second slot is active" (2026-09-29 ~06:10)

Observed: a subagent lane cold-prefilling 178775 tokens ran at 411.66 t/s average
(434 s), while solo prefill measures ~1180 t/s. The interactive session stayed
healthy throughout, decoding at 53.7 t/s.

NOT a regression, and not depth. Three pieces of evidence:
1. The lane's rate is FLAT at 377-432 t/s from chunk 128 (0.1% depth) through
   178688 (100%). A depth or cache effect shows a profile; this does not.
2. The store path is healthy in the same window: chained deltas of 606 MiB with
   save times of 175-445 ms, no pick_parent reject storm, no deferral churn, zero
   errors.
3. Nothing in the 2026-09-29 work touches prefill, the scheduler, or the quanta.
   The changes were the store-reason gate, the trailer scanner, the bootstrap
   index, finish-reason handling, and log lines.

Mechanism, in upstream code: ds4_server.c:13972 reads
`int quantum = generation_active ? s->mixed_prefill_quantum : 2048;` and
mixed_prefill_quantum defaults to 128 (ds4_server.c:17393). So while ANY decode is
resident, background prefill advances in 128-token slices instead of 2048 - a 16x
reduction, plus interleaving overhead. That is the batched-session scheduler doing
its job: it trades background prefill throughput for interactive decode latency,
which is why the resident session held 53.7 t/s. The flag arrived in the
2026-08-04 upstream merge (--mixed-prefill-quantum replaced
DS4_SERVER_MIXED_PREFILL_QUANTUM), so it predates all fork KV work.

Knob, if the trade is ever wrong: SERVER_EXTRA_ARGS="--mixed-prefill-quantum 512"
./ds4-server.sh restart-qwen. Larger quanta speed a background prefill and make the
resident decode lumpier, because the prefill holds the GPU longer per slice. Not
changed tonight - it needs a restart, which would kill a live prefill, and the
current setting is favouring the right thing for an interactive session. Any change
should be measured, not assumed: the decode side is what the operator feels.

Separately, and worth knowing: a lane cold-prefills BY CONSTRUCTION. Its prompt
head differs from the parent's (different system prompt, tool set, and context
files), so no checkpoint matches and it starts from 0 - here 178775 tokens for
434 s. A forked-context lane at depth therefore pays a full cold prefill, while a
fresh narrow-task lane pays only its own small prompt. At 178k that is the
difference between seconds and seven minutes, and it is a stronger argument for
fresh lanes than the concurrency budget is.

## 3e. Scheduled deep-store verification (lane, 2026-09-29 ~05:40) — and corrections

Verdict on the last open production-scale claim: **NOT YET OBSERVED above 250k
tokens** for the newly chainable reasons, which is a legitimate gap rather than a
contradiction. Chaining for cold/evict/shutdown IS confirmed live up to 62153
tokens (`reason=evict delta=24576..62153 size=1398.49 MiB`); no turn/evict/shutdown
store above 250k has happened since the change, because the deep stores in the
current session are all `reason=continued`, which chained before tonight.

Numbers from that pass which are better than anything recorded earlier:
- **Delta stores cost ~607-609 MiB FLAT regardless of session depth** for a
  16384-token window, from 262144 all the way to 425984 tokens. Full stores scale
  linearly with depth. That flatness, not the ratio at any one depth, is the win.
- At comparable depth the ratio is **23.0x**: 427271 full at 14023.39 MiB versus
  425984 chained at 609.24 MiB. The 9.1x whole-blade average is diluted by shallow
  stores and understates the deep case badly.
- **Latency, not previously recorded at all:** a deep full store stalls the slot
  ~5.2 s (save=4982.8 ms at 427271) against ~0.85 s chained. That is a stall the
  operator feels, on every deep store.

Correction to my own earlier framing, and it makes P3.2 worth more than I claimed:
the deep EVICT path was just as expensive as shutdown, and it fires far more
often. On 0928 between 17:32 and 18:04 - 32 minutes - the engine wrote three deep
evict stores at 416088 (13.34 GiB), 418613 (13.42 GiB) and 425107 (13.63 GiB),
plus a shutdown at 427271 (13.69 GiB): **~54 GiB of whole-session payloads in half
an hour**, each costing ~5 s of stall. I had documented P3.2 against the shutdown
path only (32.3 GiB/day). Evict is the larger half, because it fires on every
request that misses the resident prefix and needs a disk load - i.e. on session
switches, which is exactly the long-agent-session workload.

Correction to the lane's report, verified before propagating: it claimed
`reason=turn` has zero store lines in any log ever. False - there are three, on
0910 and 0912, all `key=visible-transcript` (that reason is the multimodal /
visible-transcript store site). The lane searched only the ds4-qwen rotations.
Nothing else in its report needed correcting.

Telemetry gap found by the same pass and fixed: the current process had done 43
stores in 42 minutes and emitted no `kv cache stats:` line, because the cadence was
every 50. Lowered to 25 (`KV_STORE_STATS_EVERY`), so a normal session reports at
least once mid-run; the close path always reports regardless. The one stats line in
the log is from the short-lived 04:57-04:59 process and covers only its 7 shallow
stores, where `full/chained=1.0x` is meaningless - the ratio only informs at depth.

tool_replay on the live session at that point: `mem=13 disk=47 canonical=18
missing_ids=18`. disk=47 confirms restoration is working; the earlier disk=187 was
a different, longer session and not a regression.

## 4. Standing constraints (do not violate)

- Never run two engines; model-backed ds4_test runs only while ds4-server is
  stopped (a second 70 GiB map thrashes a 128 GiB box).
- Commit only intended files. tests/kv_policy_harness and tests/test_prompt_prefix
  are tracked binaries that make rebuilds; leave them unstaged (repo convention:
  no binary artifacts in commits). lan/wsl/ stays untracked.
- Never print the bearer key (~/.config/ds4/desktop.key) or any secret.
- Chat prose: no backticks, no code fences, no literal tool-call/DSML markup, no
  opencode-style invoke syntax. Markup lives only inside tool payloads, and is
  built from chr() in generator scripts. This broke the previous opencode session
  twice and produced malformed tool calls in this one.
- Upstream PR posture unchanged: do not PR ds4-server.sh, auth_proxy.py, or the
  kvstore disk layer. ds4_kvstore.c is ~700 fork-divergent lines; keep changes
  surgical for the next upstream merge.
- Restart the engine at most once per phase boundary, and leave it running at the
  end of the night unless the blade is unmounted.

## 5. Verification commands (exact)

    cd /Users/naz/Projects/ds4
    make 2>&1 | grep -E "error|warning"            # must be empty
    make ds4_test 2>&1 | grep -E "error|warning"    # must be empty
    DS4_LOCK_FILE=/tmp/ds4.test.lock ./ds4_test --server 2>&1 | tail -3
    DS4_TEST_MODEL=gguf/Qwen3.8-Flash-Next-Q4.gguf DS4_LOCK_FILE=/tmp/ds4.test.lock ./ds4_test --kv-delta 2>&1 | tail -2
    DS4_TEST_MODEL=gguf/Qwen3.8-Flash-Next-Q4.gguf DS4_LOCK_FILE=/tmp/ds4.test.lock ./ds4_test --tool-call-quality 2>&1 | tail -2
    ./tests/kv_policy_harness 2>&1 | tail -1
    ./ds4-server.sh stop ; ./ds4-server.sh start-qwen   # engine cycle
    python3 /tmp/blade_census.py ; python3 /tmp/blade_v3.py   # read-only census

## 6. Progress log (append after every phase; newest last)

- 23:55 program started. State verified as in section 1. Scout lane launched for
  oMLX/Scratch recon.
- 00:15 first scout returned (omlx-recon-1.md): three candidates, no v0.7.0rc1
  tag locally (HEAD ac34387 is past v0.6.2), Scratch not mined.
- 00:30 second scout returned (omlx-recon-2.md), truncated by its own tool
  budget: DS4_QWEN4_PREFILL_CHUNK verified, MoE/verify-width/dispatch-count
  unverified, Scratch not started. Lesson recorded: use `delegate` for deep
  recon, not `scout`.
- 00:35 user added the pi-side prompt-head track (Phase P0) and confirmed no
  other writers are active. Concurrency raised to 3/3 and deployed; pi restart
  pending, after which this file is the resume point.
- 00:50 pi restarted, capacity verified at 3/3 active async runs. Three
  read-only `delegate` lanes launched (uncapped budget, unlike `scout`):
  P0 pi-side prompt-head recon -> /tmp/pi-prompt-head-recon.md;
  P6 oMLX/ds4 blockers plus Scratch prior art -> /tmp/omlx-recon3.md;
  P2 backtick Option A recon -> /tmp/backtick-a-recon.md.
  Lane ids: bc2ca018 (P0), 1f43059e (P6), b9fe5888 (P2). Tree baseline
  re-saved at e80a68d before launching, since lanes hold bash.
- 01:05 P1 (P3.2) COMPLETE. RED first: the new third-directory block failed
  exactly three assertions (evict node not v3, shutdown node not v3, chained
  node not smaller). Implementation: kv_delta_reason_chainable() admits all
  five server reasons, gated by a new uncached DS4_KV_DELTA_FULL_REASONS switch
  (default on) so the pre-P3.2 restriction is restorable and testable. The
  three existing forced-full stores in the parity test now force fullness
  through that switch instead of relying on the reason string, which would
  otherwise have silently turned "delta equals full" into "delta equals delta".
  GREEN: --kv-delta OK, --server OK, kv_policy_harness all scenarios passed,
  production make clean. 154 insertions, 6 deletions across ds4_kvstore.c and
  tests/ds4_test.c. Review deferred: capacity was 3/3 and the parity test is
  the mechanical gate, so per the standing under-400-lines rule this batched
  into a single review lane with P2/P3.
- 01:25 P6 recon lane returned (report .codebase-memory/omlx-recon-3.md):
  ALL THREE oMLX candidates are already in ds4 in equal or stronger form, so
  there is nothing to port.  kernel_qwen4_gdn_front already fuses the whole GDN
  prework plus both MTP snapshots; kernel_qwen4_router_topk already does
  softmax+topk+renorm+shared-expert gate in one launch (and breaks ties to the
  LOWEST index where oMLX uses the highest, so a port would silently change
  expert selection); verify attention is already batched and deliberately
  narrows T=3 into 2+1 for bit-exactness, so the SDPA-split premise is
  inverted.  ds4's verify width is only T=2 or 3, so oMLX's S=3..9 window is
  unreachable.  PLAN-PREFILL-M5.md additionally closes chunk size (plateaued,
  default optimal) and ANE (dead track), with a stated ceiling of 0-3 percent.
  P6 is therefore redefined as a zero-code MEASUREMENT plan (MTP depth 2 vs 3
  vs auto, and DS4_QWEN4_NO_FUSE A/B to price the fusion that already landed).
  Note ds4-bench cannot exercise qwen4 MTP at all (never sets .glm_mtp), so MTP
  numbers must come from the ds4 CLI with --mtp-timing.
- 01:35 P2 recon lane returned (report /tmp/backtick-a-recon.md, to be copied
  into .codebase-memory): the unterminated-tool-call error site is the else
  branch at ds4_server.c:15627-15630; the escape case is every STREAMING chat
  request (the continuation route at 15595 excludes stream), plus a second
  unclosed envelope or exhaustion of max_tokens.  A degrade path already exists
  at 15731 but is unreachable because the parser refuses to recover when
  finish is already "error" (guard at 6994).  Fix is to stop setting error
  there, which routes into the shipped degrade.  One real hazard to handle:
  d.5/d.7, a visible checkpoint could be remembered while the live session
  still holds the stripped markup tokens - mitigation is to clear live state in
  the degrade branch.  Also confirmed upstreamable: the site is upstream code
  (origin/main 14313-14359 from upstream commit 759dd7c).
- 01:40 P3 COMPLETE.  RED: second bootstrap call opened 3 files instead of 1.
  Implementation: tool_map_index (id -> file name, plus a seen set) guarded by
  tool_mu, with the install phase outside that lock because
  tool_memory_put_source takes the same non-recursive mutex; one trailer walker
  serves both install and index modes so the section-order fix cannot drift.
  Eviction behind our back drops the stale ids on the failed open.  One crash
  found and fixed en route: my own test called free() on mkdtemp's stack array
  (SIGABRT, pointer not allocated) - production code was not at fault.
- 01:55 P0 COMPLETE and DEPLOYED (pi-extensions 5076967 + 82a9add, pushed).
  The drafted memoization patch was rejected: on the sendCustomMessage path the
  handler never runs, so it was dead code for the incident it targeted, and its
  cross-session fallback plus slice-length memo key would have manufactured new
  divergence. Shipped instead: persist the manifest to APPEND_SYSTEM.md (pi folds
  it into the base options' addendum section, so every entry point derives it),
  a deploy-time renderer wired into deploy.sh --install/--check, advertise:false
  on our only file-defined lane to kill the pi-subagents oscillation, and
  PI_TOOL_MANIFEST_APPEND_PATH so tests cannot clobber the deployed file.
  Second bug found en route: pi populates options.toolGuidelines and never sets
  promptGuidelines, and its buildRules is skipped under a customPrompt, so the
  extension had been silently dropping EVERY per-tool guideline - the AGENTS.md
  claim that it restores the bash 60s note was false until this fix.
  tests/run-all.sh green end to end (43 deploy regression assertions, live drift
  clean). Migration applied at a conversation boundary: the head changes once per
  session, so ds4-cached pi conversations re-root once.
- 02:10 P6 measurement complete. Counterbalanced High Power rebaseline and the
  DS4_QWEN4_TIMING=2 stage attribution are tabulated in section 3b. Headline:
  do NOT raise the verify width (depth 3 is 5.7% slower despite acceptance rising
  70.2 -> 73.9%), production auto is already optimal, MTP plus the fused graph is
  worth +29.2%, and DS4_QWEN4_NO_FUSE=1 is NOT a safe fallback (9.6% worse than
  disabling MTP entirely). MoE is 36.7-39.0% of Qwen3.8 prefill, which is the
  bucket PLAN-PREFILL-M5 put out of scope when it declared a 0-3% ceiling on a
  different model.
- 02:20 P5 DEFERRED on evidence (see the decision block in its phase section).
- 02:30 P3.2 VERIFIED LIVE on the running engine, which was the last unproven KV
  claim. Fresh-nonce probe (reusing text hits existing checkpoints and stores
  nothing, so the first attempt proved nothing): cold@10240 421.26 -> 175.13 MiB,
  cold@12288 482.81 -> 175.15, evict@12066 505.51 -> 198.67, and
  shutdown@17661 chained at 196.23 MiB where the same path wrote 8.14-10.79 GiB
  at production depth earlier the same day. A fresh conversation with no ancestor
  still wrote full (158.17 MiB), so fail-closed behaviour is intact. Census
  afterwards: 126 chained files at 0.60 GiB average versus 79 full files at
  5.46 GiB average, zero v3 full stores, and 71 of 210 files carrying a tool map
  (was 0 of 115 at the start of the night). Blade at 509 of 512 GiB, so LRU is
  actively reclaiming; zero error-census hits. Engine left running, PID 23463.
- 03:50 P9 (INT8 MoE) CLOSED as rejected on measurement; see section 3c. Branch
  int8-moe-prefill carries four commits (feasibility probe, design with the
  NK=32 / Q4_K-sub-block alignment, validated two-term numerics, Stage A
  quantizer verified on GPU, and the benchmark that killed it). PLAN-INT8-MOE.md
  and the four instruments are on main at e08c14b; the implementation is
  deliberately not merged, because Stage A would be dead code in a fork that
  already carries merge burden. No shipped path changed at any point.
- 03:55 P8 declined with a recorded recommendation (untracking two committed test
  binaries is the operator's call, not an overnight one).
