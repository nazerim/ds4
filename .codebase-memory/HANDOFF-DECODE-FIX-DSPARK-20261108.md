# HANDOFF — decode fix + dspark saga (canonical ledger)

**Date:** 2026-11-09 (post-overnight) | **State:** everything committed & pushed, engine up
**Predecessor for pre-fix numbers:** DS41-SMOKE-20261106.md

## 1. The solved problem

V4.1 SSD-streaming decode on M5 Max decayed **19→2.5 t/s over 1-3 h** of agentic
traffic; restart "healed" it. Root cause: **`g_transient_buffers` (ds4_metal.m)
never reaped on the success path of both shared-event drain helpers**
(`ds4_gpu_signal_batch_and_wait_event`, `ds4_gpu_commit_and_wait_selected_readback`)
+ three V4.1 sites inserting **raw `id<MTLBuffer>`** into a list whose fork-side
owner-scoped reaper (`fc94f2e`) can never reap (owner reads 0x0). List grew ~90
entries/token → O(list) scan per batch-end → linear-in-session decode wall.
Page cache / eviction / hotness knobs / swap all exonerated earlier (three null
policy legs on record — do not re-litigate).

Verified effect: deterministic 24-turn soak worst turn 597→137 s, transient list
129,440/2min → max 4, curve → flat ~19-23 t/s; ds4-bench 64k gen_tps 6.58→23.76.
**Still holds Nov 9**: 2¼ h corewars overnight run, decode flat-in-age end-to-end (§5).

## 2. What shipped (all on `nazerim/main`)

| Commits | What |
|---|---|
| `88920e1` | the 5 fix hunks + F1/F2 hotness envs + seed-gate + dspark-lane script wiring |
| `37eaea6` | evidence kit `.codebase-memory/decode-ab-harness/` + telemetry-niggles comment on #1330 |
| `65c35c4`+`5f4ae9f` | `ds4_session_dspark_stats_begin/line` — per-request `dspark req:` lines, zero-cycles printed positively |
| upstream **#1330** / **PR #1331** (`f8a454a`, branch pushed, worktree `/tmp/pr-upstream-eventdrain`) | 2× `removeAllObjects` success-path reap. Both OPEN, no antirez response yet (checked Nov 9) |
| `c759147` + `cbef637` | **tool-less chat speculation**: DSML suppression threaded through `ds41_session_speculate` (mask/restore at the 4 sampling sites, `ds4_session_eval_speculative_ex`); review caught silent argmax-bias extension → gated tool-less spec to exact-sampling lanes |
| `ecccb55` | **`DS4_DSPARK_TOOLLESS_NOEXACT`** env knob (default off): opt-in tool-less spec on noexact lanes |

## 3. The dspark saga, final chronology (each step instrumented, not inferred)

1. "dspark lane seems faster" (operator, pre-fix era) — true but for the WRONG
   reason: measured on the decaying substrate; gains were leak-amortization +
   `chunk=` interval artifacts (9,174 t/s "burst" = prefill straddle).
2. Post-fix three-way A/B → "server never speculates" — **WRONG**: probes were
   all tool-less chat, and `dsml_token_id` (ds4_server.c:16249) is assigned only
   for toolLESS requests (DSML suppression id >= 0 -> old gate `dsml_token_id<0`
   failed). **has_tools traffic always speculated** (proof: `generation rewind`
   spec-boundary lines: 33 noexact leg / 20 exact / 0 plain).
3. Termbench legs, measured cleanly (avg=-field, artifact-proof):
   - tool-spec-only noexact: med 24.86 vs plain 23.3 (**+6.7%**), wall +13%.
   - all-spec (with tool-less knob): med 25.48, 30/30 requests speculating, accept 69.1%.
   - shape-3 deep bench, band-matched (walls not comparable — stochastic agent
     loops diverge): spec **+7.0/+13.3%** by depth band; plain decays 22.2→20.8
     with depth, spec holds ~flat (23.8→22.4): accepted blocks amortize
     deeper-context verify cost.
   - short-burst shape-2 was a wash (−3.1% med, small n; cold-draft opener cost).
4. **512k ctx raised + corewars overnight** (§4/§5): the lane's standing config now.

## 4. Live system state (verified Nov 9 post-reboot 06:19)

- Engine **PID 1763** (rebooted 06:15 after §5a incident; prior boot 4624 was the
  zombie): `--ctx 524288 --dspark --mtp-model gguf/DeepSeek-V4.1-Flash-DSpark.gguf
  --dspark-confidence 0.45`, env `DSPARK_EXACT=0 DS4_DSPARK_STATS=1
  DS4_DSPARK_TOOLLESS_NOEXACT=1 DS4_DSPARK_SCHEDULER_WINDOW=12`, launched via
  `V41_CTX=524288 DSPARK_EXACT=0 DS4_DSPARK_STATS=1 DS4_DSPARK_TOOLLESS_NOEXACT=1 ./ds4-server.sh restart-v41-dspark`
- **BOOT TRAP**: script default `V41_CTX=262144` — any restart WITHOUT the env
  silently drops the lane back to 262k. Flip the script default or always pass it.
- 512k memory accounting: static ctx buffers 10.5→13.5 GiB (+3.0), expert cache
  auto-trimmed 73.96→70.96 GiB, engine RSS flat ~81-83 GiB, swap flat 0.8 GiB.
  Engine hard ceiling: `ds41_graph_alloc` rejects ctx > 1048576; model trained
  natively to 1M (rope_orig_ctx=1048576, no extrapolation). 1M = +31.7 GiB
  buffers — unattempted (would re-trim expert cache further).
- KV disk: `/Volumes/FireCuda520/ds4-kv-v41` (483 GiB free; ~8.2 KiB/tok stored).
  Post-reboot: drive was physically reconnected by operator; boot line binds the
  path AND a live store (15.09 MiB, 30.8 ms) landed on the external volume —
  external-KV proven, no internal fallback (only ds4_kvstore.{c,h,o} matched).
- auth_proxy **PID 1897** :8100 via `start-proxy` (pid-file auth_proxy.pid).
- Provider topology (opencode.json): `"ds41"` = ds4.c on THIS MBP :8003 — the
  lane all changes touch (context set to 524288); "DeepSeek V4.1 Flash (strata)"
  on nazryzen = different machine, still 262144, NEVER adjust for local state.

## 5. Corewars overnight (final run definition: from 22:32:53 anchor, 134 min)

148 reqs / 116,988 tok / 0 errors / 0 reboots. Weighted accept **67.2%**, 2.16
acc/cycle, 3.16 tok/verify, 37,028 cycles. Decode medians per 30-min age bucket:
21.8/22.9/19.3/21.9/20.5 — flat in age (leak-fix holds). Accept RISES with depth
(65.2→67.2→68.6% through 70-110k); decode at 110k+ still ~19.5 vs mid-band 22.1.
Peak depth reached 124k — **the 200k-512k band is still unexercised**. Verdict:
lane approved as standing config; monitor artifacts: /tmp/ds41_leg.snap.
### INCIDENT 2026-11-09 05:40:10 — pread-hang lane wedge (make-doom-for-mips aborted)

Request stuck mid-decode at gen=200 (ctx 37.9k, inside spec verify:
ds41_session_verify -> ds41_moe_batch -> stream_expert_pread_tasks): one worker
hard-blocked in pread() at ds4_metal.m:14355 on the MAIN GGUF (internal SSD),
coordinator waits task-completion forever; single-slot lane => total stall at
0.0% cpu, log frozen. Exonerated: sleep/TM/memory pressure/blade (volume ls OK,
DSpark gguf reads OK, kernel log clean). SIGKILL -> UNREAPABLE zombie (?Es,
thread wedged in-kernel); fresh-fd dd of main gguf still hangs AFTER the kill
=> vnode/driver-level wedge, userspace-unrecoverable. RECOVERY = system reboot,
then relaunch with the four envs (BOOT TRAP) + re-arm monitor. Evidence:
/tmp/ds41_stall.sample. First occurrence ever of this signature; upstream
streaming pool has no pread timeout/watchdog (candidate hardening + issue).
Monitor since gained a stall tripwire (--stall 180s: inflight>0 + log silence +
cpu<1% => snapshot !! STALL line with leaf frame + desktop notification).
**RECOVERED 06:15-06:19**: operator rebooted + reconnected blade (device node
disk4->disk5, mount point stable); engine 1763 booted with all four envs,
external-KV live-store proven, smoke request speculated 68.75% immediately.
Monitor re-armed: /tmp/ds41_leg_doom2.snap (anchor 06:19:55, cap 5h).

### DOOM ROUND 2 2026-11-09 06:25:50-07:55:10 (89.3 min, anchor = operator's KV-hit resume line; harness-error end)

Mainline to 07:25:14: 32 reqs / 96,295 tok / 0 errors / wall 27.0 t/s
(prefill-inclusive). Then the fork leg (§6 note): +1 req / +65,536 tok.
**Round-2 true totals: 33 reqs / 161,831 tok / 0 engine errors; ended by
harbor/termbench harness error** after two consecutive 64k finish=length
megaturns. Weighted accept 85.1% (mainline) = 31 normal tool-loop turns at 65.0%
(3.18 tok/verify, same profile as corewars) + ONE megaturn that rewrote the
records: gen=65,536 (=client 64k cap, finish=length), 1,607 s, depth 125k->
**190,638** (new lane peak), **96.78% accept / rejects=0 / 4.654 acc-cyc /
5.65 tok-verify / 40.8 t/s cumulative FLAT across 65k tokens of depth growth**
(machine-generated doom code = speculation's best case; prose-like turns stay
~65%). Within-turn avg= is artifact-free depth evidence: depth beyond 124k has
NO decode decay and NO accept collapse. Prefill at depth fine (3,116 tok @122k
= 17.5 s, ~178 t/s). KV anchors tracked to 190,638 (1.0 GiB stores ~0.3 s).
RSS 81.6G flat. Monitor learned: false-QUIET on >20-min single turns (fixed
`0ec30f2`: quiet now requires inflight==0); band-accept attribution is by
request-END ctx pos on the dspark line, so the 96.78% lands in the 110-150k
band — don't read bands as position-resolved for megaturns.

### DOOM ROUND 3 2026-11-09 07:55:12-10:30:28 (155.3 min; CLEAN end, finish=stop at 319k)

169 reqs / 165,842 tok / 0 errors / wall 17.8 t/s / cycles 42,144 /
weighted accept **73.9%** / 3.94 tok-verify / NO megaturns (med 382, max
14,058) = best bench-quality dataset on the lane. Depth bands: dec
17.8/21.0/24.3/23.2/**23.5** & accept 66.6/72.7/77.1/72.6/**74.3**% —
>262k band (n=982) EQUALS mid-band: decode does not decay, accept does not
collapse. Peak 319,646 (61% of ceiling). Age-flat 30m: 19.6->29.8 no decay.
RSS ~85.5G peak then back to 80.5. MIPS-sim client-side gaps = harness idle,
not stall (see §6 note). Watcher: /tmp/ds41_leg_doom3b.snap FINAL.

## 6. Open threads (priority order)

1. **Depth stress 320k-512k** — round 3 (07:55:12->10:30:28, 169 reqs / 165,842
   tok / 155 min / 0 errors, clean finish=stop end at 319,646 peak) validated
   EVERYTHING to 320k: >262k band dec 23.5 & accept 74.3% (n=982, normal
   turns), age-flat, stalls=0. Remaining: last 39% of ceiling + footprint
   (~93G est @512k-full vs ~95-100 jetsam zone) unproven at full depth.
   [was: 200k-512k; 200k-320k CLOSED by doom3 2026-11-09]
   SEGMENTATION (corrected per operator): the 07:25:19-07:55:10 session was NOT
   a corewars resume — it was **doom round 2's harness-side FORK** after
   megaturn A hit the 64k cap: new chain file (58a9db63, 5,713 disk anchor),
   119,424-tok re-append to common=124,535, then its OWN 65,536-tok megaturn B
   (96.50% accept, ~40 t/s, depth 190,673, finish=length 07:55:10) — at which
   point harbor/termbench ERRORED OUT (two consecutive 64k finish=length
   messages; engine-side: zero errors, behaved to spec). Round-2 true totals:
   33 reqs / 161,831 tok / 89.3 min, harness-error end. The two megaturns'
   IDENTICAL stats (cycles=11,591, accepted=53,945 both) are explained: same
   conversation forked -> regenerated ~identical doom-code continuation -> same
   drafter behavior. Megaturn class reproduced twice, same task, both ~96.5%+.
   A second opencode session (corewars) may rejoin the single-slot lane anytime
   — when analyzing, attribute requests by ctx-chain continuity, not time.
   NOTE (operator): the make-doom harness runs MIPS builds/simulation CLIENT-
   SIDE — engine legitimately goes quiet (no log lines) for long stretches
   with inflight==0. That is harness idle, NOT stall: STALL requires silence
   with inflight>0 (unchanged). Monitor quiet-exit raised to 2400s (doom3b)
   so sim gaps don't fake a round end.
2. **V41_CTX script default decision** — DECIDED 09:3x (operator): **env
   override is the standing mechanism**; script default stays 262k on purpose.
   The boot command (four envs, §4) is the single source of truth for lane
   config — BOOT TRAP mitigated by discipline, not by changing the default.
3. **Upstream #1330/#1331 watch** — rework via `/tmp/pr-upstream-eventdrain` if
   antirez comments. Branch `fix/upstream-event-drain-reap` = `f8a454a`.
4. Hook test for mask primitives (review finding 4): `sample_build_probabilities`
   masked-row == probs[X]=0 + dspark block run never emits X (both exact/noexact).
   Engine-stop window required (`make test` lock rule).
5. `make cpu` pre-existing link breakage (`ds4_gpu_set_decode_phase` no stub
   under DS4_NO_GPU; identical on HEAD — recorded, unfixed).
6. Chunk-rate telemetry artifact (shared progress state across prefill boundary)
   — filed in #1330 comment; one-field fix, unfixed.
8. HF token rotation (Nov 6 `--token` exposure) — operator-side.

## 7. Files to remember

- Ledgers: this file (state), `PLAN-DECODE-DEGRADE-20261108.md` (decision trail),
  `DS41-SMOKE-20261106.md` (pre-fix baseline + corrections history),
  `UPSTREAM-TRANSIENT-LEAK-20261108.md` (issue text), `PR-EVENT-DRAIN-REAP.patch`.
- Harness (`decode-ab-harness/`): soak `ds41_soak.py`, legs `ds41_run_leg*.sh`,
  analyzer `ds41_ab3.py`, **spec collector `ds41_collect_spec.py`** (per-request
  deltas + leg wall + depth-band medians + `--since HH:MM:SS` window),
  **leg monitor `ds41_watch_leg.py`** (long-run liveness/spec/band snapshots).
- Code seams: `ds4_metal.m` transient fix + stats neighbors; `ds4.c`
  `ds41_session_speculate` (~90270, exclude param + mask sites),
  `ds4_session_dspark_stats_*` (~77360), `ds4_session_eval_speculative_ex`
  (~91300); `ds4_server.c` gate ~16540 (tool-less clause removed, knob checked
  at ~16270 request-scope — do not getenv inside the token loop).
- Models: `gguf/DeepSeek-V4.1-Flash-1017-IQ2_XXS-Q2_K-imatrix.gguf` (=
  `ds4flash.gguf`; 2.36 bpw main, engram E4M3 188.8 GiB disk-only) +
  `DeepSeek-V4.1-Flash-DSpark.gguf` (drafter, 81/81 verify).

## 8. Cold-session primer

`./ds4-server.sh status` should show the all-spec 512k lane (PID 1763 at
06:19, or newer; check `--ctx 524288` + the four envs — see BOOT TRAP §4).
Invariants:
decode 19-23 t/s flat at ANY session age is the fixed behavior — if decay
returns, `DS4_METAL_CB_TIMES=1` list-growth check FIRST (everything else is
exonerated on record). Cross-leg comparison: use decode `avg=` medians banded
by context depth — NEVER raw wall for stochastic agent benches (legs diverge in
content). `dspark req:` prints per request when stats env is on; cycles>0 is
the positive proof, absence-of-evidence is banned reasoning here. Do not
re-litigate: eviction/hotness policy (3 null legs), "server never speculates"
(superseded §3), "dspark inert on tools" (superseded §3).
