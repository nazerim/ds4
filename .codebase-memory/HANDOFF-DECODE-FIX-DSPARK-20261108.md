# HANDOFF — decode-degradation fix + dspark saga (context compaction ledger)

**Date:** 2026-11-08 ~19:27 | **State:** all committed, nothing unpushed, engine up
**Successor to:** DS41-SMOKE-20261106.md (perf baseline) — read that for pre-fix numbers

## 1. The solved problem (headline)

V4.1 SSD-streaming decode on M5 Max decayed **19→2.5 t/s over ~1-3 h** of agentic
traffic; restart "healed" it. Root cause: **`g_transient_buffers` (ds4_metal.m)
never reaped on the success path of both shared-event drain helpers**
(`ds4_gpu_signal_batch_and_wait_event`, `ds4_gpu_commit_and_wait_selected_readback`)
+ three V4.1 sites inserting **raw `id<MTLBuffer>`** into a list whose fork-side
reaper (`fc94f2e` owner-scoped release, `release_transient` scans wrapper ivars)
can never reap them (owner reads 0x0 → matches no thread). List grew ~90
entries/token → every batch-end release scanned O(list) → linear-in-session decode
wall. Page cache / eviction policy / hotness knobs / swap all exonerated earlier
(see DS41-SMOKE + PLAN-DECODE-DEGRADE-20261108.md ledgers).

## 2. What shipped

| Where | What | State |
|---|---|---|
| fork `main` | `88920e1`: 5 fix hunks (3 raw→`ds4_gpu_retain_transient`, 2 success-path `release_transient(NO)`) + F1/F2 hotness envs + seed-gate fix + dspark-lane script wiring + reports | pushed `nazerim/main` |
| fork `main` | `37eaea6` evidence kit: `.codebase-memory/decode-ab-harness/` (soak, runners, analyzers, ab3 collector) + AGENTS pointer; telemetry niggles comment posted on #1330 | pushed |
| fork `main` | `65c35c4`+`5f4ae9f`: `ds4_session_dspark_stats_begin/line` — per-request `dspark req:` lines, **positive zero-cycles observable** | pushed |
| upstream | **issue #1330** (Metal: g_transient_buffers not reaped on shared-event drain success paths) | open |
| upstream | **PR #1331** branch `fix/upstream-event-drain-reap` = `f8a454a` (2× `removeAllObjects` + comments, +10) | open, signed |

Verified effect (deterministic 24-turn soak, pre/post): worst turn 597→137 s,
full soak 87→46 min, transient list 129,440/2min → max 4, decode 15.7→2.3 t/s
curve → flat ~19-23 t/s. ds4-bench pair: 64k gen_tps 6.58→23.76, first-token
326→201 ms, bug curve decays within one bench run (late/early 0.34).

## 3. The dspark revelation — CORRECTED 19:5x (read this version)

The "server never speculates" claim (commits 65c35c4/01f3bdd-era analysis) was
**WRONG — probe-traffic artifact**. `dsml_token_id` (ds4_server.c:16249) is
assigned only for toolLESS chat (DSML-suppression sampler, >=0 -> spec branch
gate `dsml_token_id < 0` fails). Requests WITH `tools` keep it -1 -> spec branch
RUNS. Every probe/smoke so far was tool-less -> cycles=0 truthfully but
unrepresentatively. Positive proof of live server speculation in the bench legs
themselves: `generation rewind` lines (sole call site = multi-token
spec-boundary path 16869) — 33 in noexact leg, 20 in exact leg, 0 in plain
(no drafter -> mtp_draft_tokens=0 -> gate correctly closed), 0 current boot
(only tool-less smokes yet). ds41_session_speculate increments cycles at ENTRY
(90269), so telemetry cannot miss entered speculation.
=> The noexact tail advantage (p90 25.5 / max 33.9 vs plain 23.7 / 25.2) is
likely REAL draft acceptance; median-neutral + tail-positive = moderate accept
rate on tool traffic (cf. REPL: avg_accept 0.667/cycle, proposed ~1.006/cycle —
drafter barely extends on short prompts). Exact leg slower (median 21.8):
--mtp-exact-sampling resample overhead (resample=1 rewind+replay at temp>0).
DSPARK_EXACT config itself verified: script line 638 drops --mtp-exact-sampling
when EXACT=0; running PID 1796 has no exact flag, has --dspark --dspark-confidence 0.45.
Batched-mode exclusion (docs SERVER.md:92-96) stands — irrelevant to single slot.

## 4. Live system state (verified 19:27)

- Engine PID **1796**, `restart-v41-dspark` lane: `--dspark --mtp-model
  gguf/DeepSeek-V4.1-Flash-DSpark.gguf` (8.0 GiB, 81/81 verify), env
  `DSPARK_EXACT=0 DS4_DSPARK_STATS=1 DS4_DSPARK_SCHEDULER_WINDOW=12`
  (operator-set window — no effect observed while cycles=0), confidence 0.45.
- Drafter loaded ⇒ expert cache 73.96 GiB (vs 79.51 plain); server-side dspark is
  pure overhead until §5 lands. Smoke: 64 tok/6.2 s, positive `dspark req:` line.
- **Operator termbench A/B in progress or imminent** on this lane — collect plan:
  `grep -c 'dspark req' log/ds4-v41.log`, zero-cycles tally per turn, `avg=`
  medians via harness scripts; compare against banked baselines: plain med
  23.3/p90 23.7/max 25.2 (n=495), noexact med 23.2/p90 25.5/max 33.9 (n=677,
  non-speculating), exact med 21.8 (n=370) — all `avg=`-field, artifact-proof.
- Logs: rotate-on-boot (`log/ds4-v41.log.<YYYYMMDD-HHMMSS>`, keep 10; snapshots in
  /tmp: base/d1024/do/do_d1024/accept/dspark legs/probes). Lane ops: `start-v41`,
  `restart-v41`, `restart-v41-dspark` (plain default lane remains the honest one
  until §5 done). auth_proxy PID 57930 :8100.
- git: clean, pushed to `nazerim/main @ 5f4ae9f`. Worktree `/tmp/pr-upstream-eventdrain`
  holds `fix/upstream-event-drain-reap` (remote branch pushed).

## 5. Open threads (priority order)

1. **Score live server speculation** (it ALREADY runs on has_tools traffic —
   no wiring needed for that): read `dspark req:` cycles>0 lines + accept_rate
   distribution from operator's running termbench on PID 1796; compare tails vs
   banked plain baseline. Optional extension: make tool-less chat spec too
   (DSML-suppression sampler has no speculative variant — real patch, gates
   spec out of most ordinary turns). Wire NOT needed: 90405 dispatch is live.
2. **Upstream response on #1330/#1331** — watch; retest legs if changes requested
   (worktree + branch + patch all staged).
3. **`make cpu` pre-existing link breakage** (`ds4_gpu_set_decode_phase` no stub
   under `DS4_NO_GPU`, identical on HEAD — recorded, unfixed).
4. **Chunk-rate telemetry artifact** (shared progress state across prefill/decode)
   — noted in #1330 comment; one-field fix, unfixed.
5. **Accept-rate telemetry on REPL path exists** (`DS4_DSPARK_STATS` session-free
   print) — server path now has it; both quiet unless spec runs.
6. HF token rotation from the Nov 6 download still operator-side.

## 6. Files to remember

- Ledgers: `DS41-SMOKE-20261106.md` (perf truth), `PLAN-DECODE-DEGRADE-20261108.md`
  (decision trail incl. falsified hotness-policy legs: d1024/do/do_d1024 all
  FLAT), `UPSTREAM-TRANSIENT-LEAK-20261108.md` (issue text), `decode-ab-harness/`
  (scripts + README rules incl. turn-segment-at-`finish=` normalization).
- Scripts: soak=`ds41_soak.py`, legs=`ds41_run_leg.sh`/`ds41_run_leg_dspark.sh`,
  comparison=`ds41_ab3.py` (artifact-filtered), corpus=`ds41_speed.py`.
- Model: `gguf/DeepSeek-V4.1-Flash-1017-IQ2_XXS-Q2_K-imatrix.gguf`
  (= `ds4flash.gguf` link; IQ2_XXS gate/up + Q2_K down experts, Q8_0 dense,
  main weights **2.36 bpw**; engram = MXFP8-ish E4M3+E8M0 8.25 bpw on-disk I8,
  188.8 GiB disk-only). DSpark support: `DeepSeek-V4.1-Flash-DSpark.gguf`.

## 7. Next-session primer (if woken cold)

Read §5 first — the pending decision is whether to wire server speculation.
Check engine: `./ds4-server.sh status`; collect operator's termbench legs per §4.
Baseline invariants: plain lane ≈ 19-23 t/s flat at any session age (the decay is
fixed — if it returns, check `DS4_METAL_CB_TIMES=1` list growth FIRST, everything
else was already exonerated). Upstream fix is 10 lines; fork fix is 5 hunks;
both in git history. Do not re-litigate eviction policy (three null legs on
record).
