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

- [ ] P0  pi-side prompt-head stability: tool-manifest memoization (user-added;
          largest measured cost, drafted patch exists, different repo so no build
          contention with the ds4 phases)
- [x] P1  P3.2 evict/cold/shutdown chaining — DONE, see progress log 01:05
- [x] P3  Bootstrap id-index — DONE, see progress log 01:40 (done before P2
          because its RED test was already written and the design was settled)
- [ ] P2  Backtick Option A: output-side hardening + repro + content detector
- [ ] P3  Bootstrap id-index (kill the per-request whole-dir scan)- [ ] P4  P3.1 tail: double-write investigation + store telemetry
- [ ] P5  P2.1 middle-retire re-anchor (highest risk; gate on P1-P4 landing)
- [ ] P6  oMLX recon synthesis + implement portable perf wins for Qwen 3.8 Flash
- [ ] P7  Docs closeout (ADR, TODO, HANDOVER), full suite, restart engine, report
- [ ] P8  Adjacent hygiene (user instruction 00:50: add high-priority adjacent
          work to tonight's queue): untrack the two committed test binaries
          tests/kv_policy_harness and tests/test_prompt_prefix, which violate
          this repo's own "no binary artifacts tracked" rule and show up as
          permanently dirty after every make. Low risk, do last.

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

### Phase P4 — P3.1 tail: double-write + telemetry

- Double-write: the 2026-09-27 18:38 observation of the same delta written twice.
  Forensics first (grep the rotated logs for duplicate sha/frontier pairs within
  seconds), then decide: benign (two batched-session slots storing the same
  frontier) or a bug (missing dedupe). If benign, document; if a bug, dedupe by
  (sha, frontier) under the store lock.
- Telemetry: a periodic store-summary log line (counts by reason, chained vs
  full, bytes written, deferral count) so the disk argument stops needing an
  offline census script.

### Phase P5 — P2.1 middle-retire re-anchor

Highest risk: it rewrites an existing checkpoint. Design: when an interior node
has exactly one child, rewrite the child as a self-contained store (parent rows
plus child span), fsync, atomic rename, then unlink the parent. Crash-safety and
the chain verifier are the whole ballgame. GATE: only implement after P1-P4 are
pushed and green; if the risk controls are not clean by then, ship the design
plus a deferral-pressure metric and defer the code. Measured upside is modest
(35.3 GiB of interior nodes, most of which LRU would drain anyway); the real
upside is fewer stitch hops and less deferral pressure.

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
  GREEN: --server ok, production make clean.
