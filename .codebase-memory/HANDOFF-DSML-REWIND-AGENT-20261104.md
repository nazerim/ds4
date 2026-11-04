# HANDOFF BRIEF — PLAN-DSML-REWIND agent (20261104, supersedes the launch-prompt starter)

Paste-ready instructions for the fix-agent session.

---

You own PLAN-DSML-REWIND in /Users/naz/Projects/ds4.

## 0. Start
- `git pull` — your base is `fd10900` (docs were corrected twice today; work ONLY from the latest).
- Read fully, in order:
  1. `.codebase-memory/PLAN-DSML-REWIND.md` (your work order)
  2. `.codebase-memory/ISSUE-EXACT-DSML-REWIND-REBUILD-20261104.md` (evidence + corrected mechanism)
  3. Ledger 14:0x-16:2x entries in `.codebase-memory/3C-NIGHT-PLAN-20261103.md` (how the evidence was gathered — and the two mis-attribution rounds this doc set went through; trust NOTHING in it you have not re-verified against source yourself, including the numbers below).

## 1. The problem, one paragraph
Engine `ds4-server` (DeepSeek-V4-Flash Vision-Exp, DEEPSEEK4 family) with `--dspark --mtp-exact-sampling --dspark-confidence 0.45`: tool-bearing (DSML) turns freeze mid-generation for minutes, silently, then resume and complete. Verified law: `stall ≈ context_tokens / 195-600 t/s` (prefill rate). The stall is a full GPU re-prefill of the whole prompt inside `ds4_session_sync_internal`'s RESET branch (ds4.c:79547-79588, the `metal_graph_prefill_chunked(prompt->len)` call ~79564), reached from `server_generation_rewind` (ds4_server.c:14666, called ~16736) whenever EXACT's mode-boundary resample (condition ~16724-16727) needs a rewind the fast path cannot serve.

## 2. Family-correct mechanism (re-verified 16:2x — THIS is your target)
- Live fast path = UNCONDITIONAL tail: ds4.c:89197 `dspark_rewind_recent(s,pos)` (89113), on session fields set by `dspark_rewind_begin` (64178) in shared DSpark verify: `dspark_rewind_start/end/logits/prefixes/frontier`.
- Its gates: logits non-NULL; `dspark_rewind_end == checkpoint.len`; `pos in [start,end)`; span `<= DS4_DSPARK_MAX_BLOCK_SIZE`=16 (ds4.c:2914).
- **AMPLIFIER**: in `ds4_session_rewind`, `state_ok=true` recovery exists ONLY behind `ds4_session_is_qwen4` (snap0/1/2) and `ds4_session_is_glm`. DEEPSEEK4 has NO fallback -> every bail = truncation + graph invalidation + FULL rebuild.
- **DEAD FOR YOUR ENGINE — do NOT instrument**: ds41 branch fields (`verify_start`, `rewind_end` on `ds41_dspark`) = DEEPSEEK41 family; `snap_pos x3` / `qwen4_graph` = QWEN4_EXP family (that's the qwen profile on port 8002). Both were cited wrongly at some point today — the ledger documents it.

## 3. Hard guardrails (live system)
1. NEVER restart/stop/kill the live engine (PID 4051, port 8001). It is collecting day-14 training data and has a pending conf-head A/B tonight. Deploy of any rebuilt binary happens ONLY at an operator-signaled restart — ask before any restart, ever.
2. Private instance: port **8011+**, `DS4_LOCK_FILE=/tmp/ds4-fix.lock` (honored, ds4.c:61747), `DS4_TRAIN_DUMP=/tmp/ds4-fix-train` (or unset), parity OFF by LEAVING `DS4_TD_PARITY` UNSET (setting `DS4_TD_PARITY_MAX=0` does NOT disable — 0 coerces to 4096, ds4.c:76172), separate log. Synthetic cycles must NEVER enter `/Volumes/FireCuda520/ds4-train/active`.
3. Memory: run YOUR instance with ctx ~131072 (80k repro fits), not the live 1M; stop it when idle — the live engine's memory is the operator's concern; yours is yours.
4. `make` builds are fine (binary swap only happens on restart). `make test` / `./ds4_test` ONLY with the ENGINE STOPPED (refuse otherwise — process lock). `--server` test subsets coexist with a running engine.
5. Repo culture: commit at every milestone, pushed to `nazerim/main`; every milestone gets an INDEPENDENT fresh-context review (subagent, read-only) BEFORE you mark it done; ledger appends only; never commit binaries/secrets; never force-push.

## 4. Milestones
### M0 (first, log-only, land fast)
In `server_generation_rewind`, when the slow path is about to rebuild, emit ONE DS4_LOG_KVCACHE line containing: `resample=` (there is a SECOND silent route reaching the slow path — `kept < ntok && !resample` — do not miscount those as boundary bails), target pos, `dspark_rewind_start/end`, `logits!=NULL`, `prefixes`, `checkpoint.len/valid`, common-prefix depth vs prefix.len, rebuild tokens + est seconds. Wire the existing `rebuild_progress` keepalive pattern (ds4_server.c:15176+) ONLY after confirming headers are already sent at that call point; when in doubt: log-only.
Acceptance: on YOUR private instance running the repro (below), the line appears exactly at the stall. Then classify which gate fires on a real session's lines — **that classification gates M2's design**.
Bonus live evidence (read-only): the live engine booted with `DS4_DSPARK_STATS=1`; `grep 'DSpark stats' log/ds4.log` shows per-session `replay_fallbacks`, `direct_full/partial`, `miss_first` counters corroborating bail frequency (note: PID-4051 binary has no M0 line — counters only).

### M1 (pragmatic fix: truncation-aware resume)
Goal: prompt that is a PREFIX of a live checkpoint should truncate+resume from the surviving common depth (`metal_graph_prefill_chunked_range(start=depth, len=rest)`) instead of reset+replay-from-zero. First map what state survives `ds4_session_rewind` truncation for DEEPSEEK4 (graph write cursor, compressor/indexer rows, GLA/PLE recurrent state) — this is the feasibility gate; a written "blocked because X" verdict is an acceptable outcome.
PITFALLS already paid for: resume must align to ABSOLUTE chunk boundaries (compressor/indexer row finalization order — see continuation-branch comment ~ds4.c:79462 and `metal_graph_resume_prefill_min_tokens()` ~79480) or bit-level reduction order changes and bitwise determinism is unattainable; pre-commit a tolerance protocol (byte-diff transcripts, report divergence positions, distinguish parity-affecting diffs from reduction artifacts).
Gate behind `DS4_REWIND_RESUME=1`, default OFF.
Acceptance: slow-path wall time ~ctx/350 -> ~(ctx-common)/350, common >= 90% on typical DSML turns, at ctx>=80k; transcripts identical vs default path on 20 fixed-seed prompts (subject to tolerance protocol). COST: each slow-path sample is ~3-7 min GPU; the 20-prompt matrix is 1.5-2.5 h sequential — run idle-time, stop between batches.

### M2 (real win — only after M0 data)
Family-correct restore for DEEPSEEK4: reuse the session-owned `dspark_rewind_frontier` buffers (comment ds4.c:64175: they "survive the verifier call") to make `dspark_rewind_recent`'s window robust across the resample-rewind pattern. Size the memory cost FIRST, report, then implement behind env gate.

### M3 (upstream)
Condense ISSUE + your diffs + repro into a PR-ready write-up. Fork lands first; upstream offered after.

## 5. Deterministic repro (private instance)
1. ~350-500 KB conversation text as message history (multi-turn or one big user msg) + `tools` with a function schema + request temperature > 0, thinking enabled (the live pattern: THINKING then DSML tool payload).
2. One agent-like turn requesting a LONG tool-call argument block (multi-KB shell/write payload).
3. Watch: decode halts mid-DSML_START ~ctx/350 s with no output; with M0 installed you get the line naming the gate; `DS4_DSPARK_STATS=1` shows `replay_fallbacks++`.
4. Fixed seeds + greedy where possible so the 20-prompt matrix is deterministic (exact-sampling makes this strict).

## 6. Definition of done (plan-level)
M0 committed+reviewed; M1 landed (env-gated) OR written feasibility verdict naming the blocking state precisely; M2 sized (or gated on M0 field data with a one-line ask to the operator); M3 drafted; all findings appended to `.codebase-memory/PLAN-DSML-REWIND.md` / ledger so the 3c collection/training lane never re-derives anything. Report every correction you make to these docs — the doc set has been wrong twice today; your re-verification IS the audit.
