# PLAN: Fix V4.1 SSD-streaming decode degeneration (execution plan)

**Date**: 2026-11-08 | **Status**: ACTIVE | **Extends**: `PLAN-DECODE-HOTLIST.md` (draft, Oct 9)
**Baseline symptom**: decode 16–23 t/s fresh → 3.1 t/s floor after ~1 h plain agentic traffic,
2.3 t/s after 3 h with dspark (ctx 58k→142k). System healthy (swap flat, page-cache purge
proved innocent). Live-traffic evidence in `.codebase-memory/DS41-SMOKE-20261106.md`.

## Root cause (from source recon, 2026-11-08)

Expert cache eviction (ds4_metal.m, upstream `3d65923`, in our build) picks victims by
**minimum route-hotness, tie oldest `last_used`** (16495–16560). But:

1. `DS4_METAL_STREAM_EXPERT_HOTNESS_DECAY_TOKENS = 16` (enum const 1114, NOT env): the
   whole 40×384 hotness table **halves every 16 tokens** (integer, 15145) → any 551-token
   append = 34 halvings = ALL hotness collapses to 0 → eviction degenerates to **pure LRU**,
   so every fresh-routing burst (tool JSON, code, CJK) evicts the decode-critical working
   set exactly as the sustained decay measures show.
2. Hotness is credited identically for prefill-phase and decode-phase routing
   (`note_selected_hotness` 40608/43966; sweeps via `note_frequency_hotness` 54575) —
   prefill selections outnumber decode ~6.7× (PLAN-DECODE-HOTLIST math).
3. Aging clock `note_token` advances on every streamed token incl. token-major appends
   (40490/42759/54537) — the V4.1 <1024-token token-major prefill cliff churns the clock.
4. V4.1 has NO built-in hotlist: `ds4_streaming_hotlist.inc` seeds only V4 Flash/Pro
   (variant check 23126-23128 excludes FLASH41); upstream PR **#1321** (open, 2026-11-05)
   adds the V4.1 table + `hotlist_to_inc.py` + `DS4_MOE_RECORD_SELECTED_HOTLIST`
   (the recorder ALREADY exists in ds4_metal.m:2238/2309 — we can record our own).

Related upstream: **#500/#609/#611** (slow tool rounds — continuation axis), **#676**
(DSpark slower on q2-imatrix M3 Max 128 GB — confirms our dspark A/B), **#931** (GLM long
tool-session stalls), **#629** (CUDA host-RAM expert tier — the big architectural lever).

## Fixes (each env-gated, default = current behavior)

- **F1 decay horizon**: env `DS4_METAL_STREAM_EXPERT_HOTNESS_DECAY_TOKENS` (default 16);
  test 512 / 2048. Makes hotness a real LFU signal that survives append bursts.
- **F2 decode-only hotness gating** (PLAN-DECODE-HOTLIST §1): `DS4_STREAM_HOTNESS_DECODE_ONLY=1`
  skips routing credits during prefill phase in `note_selected_hotness` (+ decide
  frequency-note handling; gate aging on token-major prefill if cheap).
- **F3 decode-biased hotlist for V4.1**: record `DS4_MOE_RECORD_SELECTED_HOTLIST` across a
  soak (decode-phase weights up), feed via `DS4_METAL_STREAMING_EXPERT_HOTLIST` (file-based
  path exists; verify FLASH41 acceptance; extend variant check only if needed).
- Combinations: F1, F2, F1+F2, F3, F1+F2+F3.

## Harness (reproducible, deterministic)

`soak.py`: seeded turn mix replaying our real profile — session start (30–45k mixed prefill),
then ~26 turns of {tool append 300–2k tokens code/JSON/CJK + 400–1200-token thinking decode},
≈90 min at baseline rates. Logs per turn: wall rate + `decoding chunk` buckets;
`DS4_METAL_STREAMING_EXPERT_LAYER_STATS[_DELTA]` + hotlist recorder armed.
**Acceptance**: end-of-soak decode floor ≥ 8 t/s (baseline 3.1), curve monotone-free (no
collapse below 6 t/s mid-soak), no correctness change (temp 0 determinism spot-checks,
`make test` subset, 250k needle recall at the end).

## Progress log (2026-11-08)

- 05:0x Recon DONE: note-site map confirmed — DeepSeek credits at `routed_moe_one`
  43966 (per-token, both phases) + `prepare_selected_batch` 18448 (sweep frequency);
  seed path 18942 untouched. Server drives `ds4_gpu_set_decode_phase` in the shared
  decode loop (`decode_again:` label, ds4_server.c 16259) -> F2 works for V4.1.
  Hotlist seeder `metal_graph_seed_streaming_expert_cache_from_hotlist` runs from
  `ds4_session_create` (77816) variant-agnostic; `DS4_METAL_STREAMING_EXPERT_HOTLIST`
  file path bypasses builtin (.inc) and applies to FLASH41 -> **F3 is env-only**.
- 05:1x F1+F2 patched in ds4_metal.m (env `DS4_METAL_STREAM_EXPERT_HOTNESS_DECAY_TOKENS`
  default 16; `DS4_STREAM_HOTNESS_DECODE_ONLY` gates selected+frequency credits on phase,
  seed credits preserved). `make -j6` rc=0 (patched binary staged).
- 05:15:12 BASELINE SOAK started on UNPATCHED build (restart-v41 plain, hotlist recorder
  armed -> /tmp/ds41_hotlist_base.txt on session release). 24 deterministic turns,
  ~60-90 min. Runner: ds41_run_leg.sh <leg>; comparison: ds41_compare_legs.py.
- Queue after baseline: legs d1024 -> do -> do_d1024 (sequential, background), then decide
  d4096 and hl (F3 using best-leg decode-biased recorded hotlist).

## Leg results (rolling)

- **d1024 (decay horizon only): NO EFFECT** vs baseline — late turns equal-or-worse
  (t22: 608.7s vs base 597.5s; recall 94.3 vs 90.3). Interpretation: longer decay
  preserves the WRONG winners — prefill frequency-credits still dominate.
- **ORACLE CAPACITY ANALYSIS (from hotlist_base, 3.85M selections)**: 90% coverage
  needs 5,538 slots (median 133/layer, max 209) vs 8,577 budget => NOT capacity-bound.
  Policy failure confirmed. Mechanism: `prepare_selected_batch` credits the FULL
  frequency vector per sweep (~50 counts/expert in one append) -> prefill experts
  permanently outrank decode set in LFU. F2's phase gate on `note_frequency_hotness`
  targets exactly this. `do` leg running (08:33+); `do_d1024` = flagship combo.
- Hypothesis if do_d1024 wins: end-soak blended turn-rate >= 10 t/s vs 2-7 baseline;
  floor >= 8. If do_d1024 also flat: suspect eviction ignores hotness under
  per-layer quota pressure -> instrument per-layer hits via REPL run (memory-report
  path) before shipping anything.
- **DSPARK RETAINED (operator, 08:4x)**: overnight steady-state avg ~26 t/s with
  burst chunks (max 748) = block-5 commits working; earlier falsification was a
  warm-up-window artifact. Queue extended (runner ds41_run_leg_dspark.sh):
  `do` (running) -> `do_d1024` -> `dspark_base` -> `dspark_dodo1024` -> optional
  `dspark_dodo1024_hl` (F3 seed from prior leg's recorded hotlist). Ship candidate:
  restart-v41-dspark + winning env combo.
- **Interpretation rule (operator, 2026-11-08): dspark gains show on REAL traffic,
  not benchmarks/soak** (drafter predicts the model's own coherent output; synthetic
  seam-heavy corpora + cold-cache short evals under-read acceptance). Soak legs score
  the POLICY fix only; dspark acceptance test = accept_rate + burst stats from the
  user's real session post-shipping. Runner updated: graceful `stop` at leg end so
  DS4_DSPARK_STATS session-release lines (accept_rate/avg_accept) actually flush.

## Upstream PR map (swept 2026-11-08)

Zero PRs/issues on hotness decay or contamination — Metal eviction unchanged since
June `3d65923`; our patch lane is novel. Adjacent: **#1321** hotlist seeding (=F3,
env-only on our tree), **#849+#848** router-lookahead prefetch + packed reads
(attack miss LATENCY; candidate follow-on leg post-policy-verdict, bit-identical,
inert unless `DS4_PF_LOOKAHEAD=1`), **#1168** dspark reject-backoff (burst-tail cost,
conflicting), **#1298** V4.1 step/layer timing (adopt for fallback instrumentation
if do_d1024 flat), **#1033** M2 slab-residency fix (different failure mode, not ours),
**#647/#605/#1180** CUDA getting real LRU+profiler (confirms Metal policy is stale).
Finished experiment = publishable upstream issue with first contamination data.

## Asymmetry finding (post-`do` null, explains F1/F2 alone being inert)

V4.1's two MoE exits on Metal have asymmetric bookkeeping:
- `ds41_moe` single-token (decode + token-major appends, ds4.c 42458) ->
  `ds4_gpu_routed_moe_one_tensor`: credits hotness (43966) + records hotlist (44013).
- `ds41_moe_batch` / sweeps (42442/43293/43301) -> `routed_moe_batch_tensor`
  (ds4_metal.m 45419-46926): **NO note_*/record calls at all** (batch_owned variant
  is CUDA-only). Batched traffic is invisible to hotness AND to the recorder.

Net effect at defaults: the hotness table contains only decode credits, which the
16-token *all-tokens* clock erases before the next turn -> eviction is effectively
pure LRU regardless of gating => `d1024` null + `do` null are both expected.
Prediction: only `do_d1024` (remove prefill churn from scoring AND survive appends)
can change outcomes; if it is ALSO flat, hotness is not load-bearing in this regime
-> pivot to instrumentation (batch-path counters / #1298 timing) before more patches.
Corollary for F3: recorded hotlists are token-major-only captures (decode-weighted!) -
actually convenient as decode-biased seed material.

## Static call-path audit (v2, brace-scoped) results

- Confirmed attribution: notes only inside `routed_moe_one_tensor` (44009 note,
  44013 record) + glm (40651/40654) + qwen4 (54580/54618) + cache-internal seed
  (18985) + prepare_selected_batch frequency (18491). `batch_tensor` (45419-46926)
  has NO notes but DELEGATES to one_tensor when n_tokens==1 (45579) - so batched
  rows 2-8 (ds41_moe_batch, dspark verify) are credit-blind on Metal; batch_owned
  is `#ifndef __APPLE__` (CUDA/ROCm TP only) - linking consistent.
- CORRECTION: recorder fires in BOTH phases (ungated) -> hotlist files are
  prefill+decode merged, NOT decode-weighted as earlier claimed. F3 seed quality
  caveat applies.
- **BUG FOUND & FIXED (seed-defense inversion)**: seed_selected credited via the
  F2-gated function -> under decode-only mode, hotlist-seeded experts got ZERO
  defense and would be first eviction victims (F2xF3 self-sabotage). Fixed via
  _gated(...,respect=0) for seeding; rebuilt. Running do_d1024 unaffected (no
  seed calls exist for FLASH41 without a hotlist source).
- Consequence for dspark legs: batched verify passes never credit hotness, so on
  the dspark lane the hotness table evolves only during single-token fallback
  cycles - decode-only gating has less to skip there; combo value on that lane
  will be read empirically.
- RE-RUN RULE: winning config gets ONE confirmation leg on the seed-fix binary
  before shipping (identical soak; expect same-or-better).

## Independent code review (fresh-context reviewer, pre-ship gate)

Verdict: **no BLOCKER/MAJOR; hard invariant (env-unset == upstream byte-identical)
verified at call-site, ordering, and arithmetic level.** Three MINORs, all fixed:
1. Empty-string env (`VAR=`) previously ENABLED decode-only gating -> now requires
   non-empty value != '0'.
2. REPL/CLI design trap (phase never set => all credits silently skipped when env
   set) -> gate now honored only after the host has driven the phase flag at least
   once (g_gpu_decode_phase_ever_set tri-state, set inside ds4_gpu_set_decode_phase).
3. ds4-server.sh lookup_dspark_model array-desync crash risk -> `${DSPARK_PATHS[$i]:-}`
   degrades to empty specp, which the gate rejects with the helpful message.
Reviewer-confirmed call-site audit: wrapper callers only GLM+one_tensor; _gated
callers only wrapper+seed; frequency gate direction correct on Qwen4 decode path.
Accepted-as-documentation (no change): multi-slot phase precision (our lane is
single-slot by design); long-horizon saturation >1e5 (our lane 1024); partial env
parse "5abc"==5.
Compat note for running do_d1024 leg: server sets phase 0 BEFORE first token
credits -> new tri-state semantics match the old binary for the leg's env value;
leg result remains valid. Post-combo dspark legs run on review-fixed binary anyway.

## PIVOT (11:45): policy axis dead on arrival of data

Legs: base 1.5-4 / d1024 ~same / do ~same / do_d1024 ~same blended late-turn rates.
Static audit ruled out the escape hatches (no per-request hotness reset for V4.1;
take_reusable does use the global hotness scan). => hotness is not the load-bearing
signal for THIS degradation on Metal.

**Found the actual twin problem**: upstream issue #1297 (ROCm V4.1 SSD streaming):
3-5 t/s floor with DISK IDLE and 88% experts resident; bottleneck = routed-MoE
vec kernels (`ds4_mmq_iq2_xxs_moe_gate_up_mid_vec` + `q2_K_moe_down_sum6_vec`)
at ~3ms/layer; cache size 100-160 GiB changes nothing (their control = our null
result). Our Metal difference: fresh decode is 20+ t/s, so the vec path is NOT
always the cost - something MOVES decode into the slow regime (or inflates its
per-call cost) as session ages. #1298 timing patch applied clean to ds4.c.

**Instrumented leg `instr` running** (plain lane + DS4_V41_STEP_TIMING +
DS4_V41_LAYER_TIMING + iostat sidecar): timeline of total/submit/sync/engram/
logits per token, fresh->collapse. Decision matrix:
- engram/sync grows + iostat reads rise   -> I/O axis (cache/routing, revisit with new lens)
- submit grows, iostat flat               -> kernel/queue axis (vec path; matches #1297)
- logits/upload grows                     -> host-side bookkeeping axis (matches #1057)
Timing patch is diagnostic-only; ds4.c will be reverted after analysis (working
tree keeps F1/F2/seed + script changes only).

## INSTRUMENTED LEG RESULT (13:13): MoE-ENCODE GROWTH — not I/O at all

Windowed layer timing (PR #1298; layer brackets reset per window = valid; step
prints are cumulative averages = re-derived by differencing):
- `pre` ~0.77ms and `post` ~0.11ms FLAT all session; `sync` 0.7->5ms, `engram` ~0.
- **`moe` per-token: 104 -> 343ms, near-LINEAR in cumulative tokens (~+19us/token),
  STILL CLIMBING at leg end (no saturation at 352/384 unique-expert level).**
- => degradation is CPU-side command-encode cost inside ds41_moe/routed_moe_one
  staging, growing with session state. Explains: F1/F2/combo nulls (policy never
  touched encode path), page-cache innocence, restart folklore, AND the dspark
  "gain on real traffic" (5-row verify = 5x amortization of per-token encode).
- iostat: SSD was busy early (cold prefill; MB/s samples 1.5-4.4 GB/s) — but I/O
  per decode token is flat-to-down while encode cost climbs 4x.

**SCOPE PROBE running (ds41_probe.py)**: fresh process -> conv A cold+3 append
turns (~2.2k cumulative tokens, moe should climb ~+40ms) -> conv B cold in SAME
process. Decoding-window moe at B: ~104 => per-session/graph accumulation;
~140+ => process-global statics. Candidate classes: (a) per-graph lists (dspark
capture, carry rows, pending-load queue), (b) process statics (completion cache,
addr-table staging arrays, Metal buffer allocator degradation).
Timing patch to be reverted from ds4.c after analysis.

## Probe v2 (scope split) + encode-growth candidates

v1 probe flawed (2-token completions). v2 running (~13:2x): 7 heavy-decode turns
in conv A (count-to-200, temp 0) -> conv B cold same process. Readout: B's
post-switch moe windows vs A's start.
Suspects screened so far: completion cache = NSCache cap-256 (not it). Watch-list:
dspark capture note_checkpoint fires per token-major prefill token even with
speculation OFF (sync loop ~81752/34300 region) -> if its list is scanned during
encode, matches unbounded linear growth. Also pending_load queue, addr-table
bookkeeping. Probe scope answer halves: per-session => dspark capture/checkpoint
lists; process-global => cache-adjacent statics/staging.

## Static accumulator audit + probe3 status

Column-0-scoped sweep of the whole encode bracket (one_tensor 42769-45437, all
cache-internal fns, ds41_* step chain): NO loop/alloc/memop over a growing bound.
Model-views build once at boot. Engram = fixed-size hashed table (not a store).
Prime suspect found structurally: g_transient_buffers + g_pending_cbs = global
NSArrays with per-thread-filtered reaps (`release_transient(NO)` only removes
calling thread's entries) scanned O(count) on every batch end; barrier/mix-key
caches (%p:%llu keyed NSStrings, never pruned in grep) leak dictionary entries.
Any class of retain whose reap thread mismatches => list grows one entry per
batch forever => linear per-token scan = exactly our 19-25us/token slope.

probe2 verdict (process scope): moe series 38ms (fresh boot) -> 160ms, monotone
ACROSS the conversation-B cold switch (134->160 continued climbing) =>
accumulation is PROCESS-GLOBAL, not session/graph state. Slope matches instrumented
leg (~20us/token). Content axis noted too: greedy number-lists = pathological
(2.48 t/s even fresh) -> probe3 (content/age separation) still queued but lower
priority now that scope is settled: hunt the process-global list first.

probe4 = direct measurement: DS4_METAL_CB_TIMES=1 prints "transient buffers this
batch: N" at EVERY batch end -> if N trends upward across 4 steady thinking turns,
the leak is proven live and the owner-thread mismatch is the fix target.

## ROOT CAUSE IDENTIFIED + PATCHED (13:4x)

`ds4_gpu_signal_batch_and_wait_event` (per-layer selected-id event drain, hot for
our quant combo via q4_selected_shared_event) and its clone
`ds4_gpu_commit_and_wait_selected_readback` were MISSING the batch-end
`ds4_gpu_release_transient(NO)` on their SUCCESS return — the call exists on
their error branches and on every non-event drain (finish_command_buffer).
Consequences chain:
1. g_transient_buffers (global NSMutableArray, per-thread ownership filter)
   grows +2 entries per layer batch (HC mix completion + barrier retains,
   47771/47961/47982) — measured 3 -> 2,266 in 2 minutes of probe4, ~129k/hr.
2. Every retain/release scans the full list => per-token cost linear in batch
   count => exactly the 20us/token slope, monotone, never saturating.
3. Process-global: explains survival across conversation switches (probe2
   climbed through cold B) and instant recovery on restart (fresh process).
4. Speculation amortizes it ~5x (batch per 5 tokens) -> operator's "dspark
   better on real traffic" is THIS bug's shadow, not draft acceptance.
5. All policy knobs (F1/F2/combo) null because eviction was never the cost axis.
   NOTE: post-fix contamination A/Bs should be re-run once — the hotness policy
   may still matter underneath; envs remain available, defaults unchanged.
Patch: +release_transient(NO) on both success paths with rationale comment.
Fix-probe running (CB_TIMES list must stay flat, moe window ~38-50ms sustained).

## Post-fix leg queue (operator note 13:5x)

After confirmation soak: dspark legs with transient fix in place, comparing
sampling modes - the EXACT path (--mtp-exact-sampling, DSPARK_EXACT=1 default)
carries resample+rewind+replay overhead (43 truncation rewinds/3h observed);
non-EXACT (DSPARK_EXACT=0, opportunistic legacy) accepts drafts without the
correction rule. Caveat to state in results: non-EXACT changes output
distribution vs target (script's own collect-mode warning: seed-batch era
data). Legs: dspark_base(exact) vs dspark_noexact vs plain, same soak.
NOTE: the earlier "dspark better on real traffic" observation may partly
redistribute to the transient-reap fix story; these legs re-baseline it.

### Leak layers, corrected after code re-verification (13:5x)

- ACTIVE leak = the three V4.1 raw `addObject:` insertions only. Our measurements
  ran with DS4_METAL_Q4_SELECTED_SHARED_EVENT UNSET -> event drain not taken ->
  wrapper entries were always reaped by end_commands/finish. Owner histogram
  (all entries owner=0x0) proves raw-only.
- The two event-drain success-path reaps added earlier are a LATENT fix for
  opt-in-flag users (reap asymmetry is real, just not our lane). Keep.
- Round-1 "96k vs 129k improvement" was probe-length variance, not fix effect.
- Implication accepted: all prior contamination/policy interpretation operates on
  a substrate that had this leak layered on top; post-fix re-baselines (soak +
  optional policy/dspark legs) will tell what residue remains.

## Contribution hygiene (CONTRIBUTING.md read 2026-11-08) - closeout gate

FINAL GATE (operator standing order): after hygiene build + validation, run an
INDEPENDENT fresh-context code review over ALL final artifacts - scope must
include everything written AFTER the first review: (1) the 5 fix hunks
(3 raw-wrap sites + 2 event-drain success reaps) on the PR-branch diff vs
upstream main; (2) F1/F2/seed env patch (re-check in final tree state);
(3) ds4-server.sh v41-dspark wiring + ${arr[i]:-} hardening; (4) PR branch
self-containedness (no fork-lane contamination); (5) reports/draft accuracy
vs final code. No push/post until that review returns clean.

PR must include: commands run, machine/backend, model quant, notable failures.
Required evidence chain (all engine-stopped, quiet time):
1. make clean && make && make test (full ds4_test)
2. ds4_test --server / --logprob-vectors / --long-context / --tool-call-quality
   / --metal-kernels  (logprob-vectors = numeric-neutrality proof for retain changes)
3. ds4-bench before/after CSV pair: bug state = git-stash the 3 raw-wrap hunks +
   2 event-reaps, rebuild scratch, bench (prompt-file sweep 2048..65536,
   gen-tokens 128); fixed state = current build; compare prefill_tps + gen_tps;
   include plot_speed.py graph.
4. Title: "metal: reap transient buffers inserted by V4.1 HC helpers".
5. Branch fix/metal-transient-reap off upstream main with ONLY the 5 hunks
   (no F1/F2 env knobs - separate contribution if policy re-baseline shows value;
   no script/v41-lane changes; no diagnostics).
Sequence: acceptance soak -> battery 1-2 -> bench pair -> hygiene build -> PR
branch staged (push/post on operator ack) -> lane config -> reports close.

## FINAL REVIEW (fresh-context reviewer #2) - both BLOCKERs resolved

- BLOCKER-1 (mis-scope): wrapper pool is FORK-introduced (fc94f2e); upstream pool is
  raw+removeAllObjects -> 3 raw sites are NOT upstream bugs; true upstream defect =
  event-drain success returns skipping the clear (both helpers; commit_and_wait is
  default-path on IQ2/MXFP4 overlap + ROCm). Report rewritten with the two-artifact
  split; PR = 2 removeAllObjects lines, fork branch = 5 hunks.
- BLOCKER-2 (staged ds4.c): git apply --3way had auto-staged #1298; `checkout --`
  restored from index = no-op. Fixed: `git restore --source=HEAD --staged --worktree
  ds4.c`, clean rebuild rc=0, lane restarted (96906). Lesson: hygiene checks must
  read `git status --short`, never `git diff <file>` alone.
- MINORs 1-3 (function name, opt-in-only claim, site list/line drift) folded into
  the rewrite. NITs noted, no action.
- Verdicts: A ready (fork) / re-scoped (upstream) DONE; B ready; C ready (fork-ops,
  excluded from PR); E corrected.

## Execution order

1. Recon done (2026-11-08). Verify `ds4_gpu_decode_phase()` semantics; confirm hotlist file
   path works on FLASH41 today (no recompile — env-only test possible?).
2. Write soak.py + stats/curve parser. Baseline soak on CURRENT build (plain lane, dspark OFF).
3. Patch F1+F2 (small, isolated in ds4_metal.m), rebuild `make`.
4. A/B soaks (each ~90 min, run sequentially; notify-invisible time used for analysis).
5. F3: record hotlist from best-soak traffic; A/B with it.
6. Pick winner → set defaults in `ds4-server.sh` V41 lane + banner; full verification
   (make test, battery: context 250k, vision, tools-parity, first-token).
7. Close-out: update DS41-SMOKE report + PLAN-DECODE-HOTLIST status; prepare upstream
   material (data + patch notes for #500 and #1321) — post only after operator ack.
8. Restore engine in plain mode with winning config, `start-v41` live for the operator.

## Non-goals / guardrails

No changes to upstream-owned semantics when env unset (fork ships flag-gated patches; real
merge doctrine). No force-push. Secrets policy unchanged. Single-slot stays. DSpark lane
stays falsified-for-workload until #676-class evidence changes; untouched here.
[OVERRIDDEN 2026-11-09: dspark-on-server is PROVEN for the workload — the
"until" evidence arrived via per-request stats + 3 bench shapes + overnight
corewars (accept 67.2%, +6.7-13% decode bands, flat-in-age). Lane = standing
config at 512k. See HANDOFF-DECODE-FIX-DSPARK-20261108.md §3-5.]
