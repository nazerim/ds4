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
- [ ] P1  P3.2 evict/cold/shutdown chaining (biggest recurring disk win)
- [ ] P2  Backtick Option A: output-side hardening + repro + content detector
- [ ] P3  Bootstrap id-index (kill the per-request whole-dir scan)
- [ ] P4  P3.1 tail: double-write investigation + store telemetry
- [ ] P5  P2.1 middle-retire re-anchor (highest risk; gate on P1-P4 landing)
- [ ] P6  oMLX recon synthesis + implement portable perf wins for Qwen 3.8 Flash
- [ ] P7  Docs closeout (ADR, TODO, HANDOVER), full suite, restart engine, report

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

### Phase P6 — oMLX perf for Qwen 3.8 Flash

Recon status (two scout passes, reports in .codebase-memory/omlx-recon-1.md and
omlx-recon-2.md). All three candidates are NEEDS-MEASUREMENT, none is GO:
1. Fused GDN prework for MTP verify widths S=3..9 (oMLX
   omlx/patches/qwen35_gdn_prework.py, one Metal launch replacing ~10 dispatches
   per GDN layer per verify cycle; ~0.29 ms x layers measured on M3 Ultra).
   ds4 hook: metal/qwen4.metal GDN kernels driven from ds4_metal.m, state layout
   near ds4.c:39668-39675 (conv state plus 2 MTP snapshots per linear layer).
   BLOCKER: if ds4's MTP verify width never reaches 3, the premise evaporates.
2. Verify SDPA split / chunked causal attention (+4..9% decode with speculation
   upstream). ds4 hook: metal/flash_attn.metal and metal/qwen4.metal via
   ds4_metal.m. BLOCKER: unknown whether ds4 already chunks causally.
3. Fused MoE decode router top-k (~51 us to ~5 us per layer upstream). ds4 hook:
   metal/moe.metal plus the qwen4 router dispatch. NOT dead: ds4.c:39663 sizes
   qwen4 scratch with (DS4_N_EXPERT_USED + 1) * (E + 2 * DS4_N_FF_EXP), which
   only makes sense for a routed MoE FFN — but DS4_N_EXPERT_USED > 1 for this
   checkpoint is unconfirmed, and tie-break semantics must stay bit-identical.

Verified ds4 facts: DS4_QWEN4_PREFILL_CHUNK is read at ds4.c:39636 in
qwen4_prefill_chunk_tokens, default 8192, 0 or >65536 falls back to 8192,
clamped to ctx, and there is NO fixed-shape alignment (so oMLX's chunk-align
idea has no ds4 counterpart). ds4_qwen4_layer_is_linear(il) gates per-layer
attention vs recurrent handling. ds4-bench and ds4_bench.c exist; make targets
dspark-acceptance, dspark-verify-depth and mtp-verify-depth exist and are the
likely MTP-width harnesses. PLAN-PREFILL-M5.md at repo root is UNREAD and is the
most likely place for existing M5 prefill reasoning — read it first.

Still unknown and blocking: actual verify width S, per-layer GDN dispatch count,
ds4-bench CLI flags and whether prefill/decode report separately, and ALL prior
art in /Users/naz/Projects/Scratch (not mined — scout budget). Next action: one
`delegate` lane (uncapped) to mine Scratch for measured tok/s and to answer the
ds4 blockers, then benchmark before porting anything.

Rule: no perf change ships without a before/after number in the commit message.
Cheapest first win from verified evidence: an A/B sweep of
DS4_QWEN4_PREFILL_CHUNK (env knob, zero code risk), watching the memory
estimator coupling at ds4.c:39652 since larger chunks raise scratch_bytes.

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
