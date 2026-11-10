# HANDOFF — decode fix + dspark saga (canonical ledger)

**Date:** 2026-11-09 (lane finalized; soak day closed) | **State:** pushed, engine idle-warm
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
**Still holds Nov 9** (full day: 5 tasks / 467 reqs / ~437k tok / 0 engine errors):
flat-in-age on corewars 2¼ h; flat-in-DEPTH too — doom R3 held 23.5 t/s and
74.3% accept above 262k (§5). The one wedge was kernel-side pread (§5), not this bug.

## 2. What shipped (all on `nazerim/main`)

| Commits | What |
|---|---|
| `88920e1` | the 5 fix hunks + F1/F2 hotness envs + seed-gate + dspark-lane script wiring |
| `37eaea6` | evidence kit `.codebase-memory/decode-ab-harness/` + telemetry-niggles comment on #1330 |
| `65c35c4`+`5f4ae9f` | `ds4_session_dspark_stats_begin/line` — per-request `dspark req:` lines, zero-cycles printed positively |
| upstream **#1330** / **PR #1331** (`f8a454a`, branch pushed, worktree `/tmp/pr-upstream-eventdrain`) | 2× `removeAllObjects` success-path reap. Both OPEN, no antirez response yet (checked Nov 9) |
| `c759147` + `cbef637` | **tool-less chat speculation**: DSML suppression threaded through `ds41_session_speculate` (mask/restore at the 4 sampling sites, `ds4_session_eval_speculative_ex`); review caught silent argmax-bias extension → gated tool-less spec to exact-sampling lanes |
| `ecccb55` | **`DS4_DSPARK_TOOLLESS_NOEXACT`** env knob (default off): opt-in tool-less spec on noexact lanes |
| `89e60af` | **lane finalization** (operator): the four boot envs + `V41_CTX=524288` + `V41_KV_SIZE=262144` became SCRIPT DEFAULTS, and all three lane KV budgets (ds4/qwen/ds41) aligned at 256 GiB — plain `start-v41-dspark` boots the validated lane (§4) |
| `e6b4c3c` + upstream **PR #1022 `8da90ed`** | **F1 vision embed-cache sizing** `SERVER_IMAGE_CACHE` 32/128MiB → 128/2GiB matched to KEEP=128 (extract-r2 thrash: 3.6 encodes/turn, encoder duty ~2/3 of span at 30 images, KV reuse 99.4% throughout). Live-proven: repeat-15-image 15-encode/187.3 s → 0-encode/47.3 s; post-warm 159 hits / 0 re-encodes. Independent review caught the upstream-comment adaptation (no fork-script refs) + `size_t` cast (§5 F1 block) |

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

## 4. Live system state (verified Nov 10 10:20; defaults finalized Nov 9 ~14:45)

- Engine **PID 26079** (relaunch 1110 10:16:33 — first plain-default boot, §5
  circuit; formerly 1763 post-reboot): `--ctx 524288 --dspark --mtp-model gguf/DeepSeek-V4.1-Flash-DSpark.gguf
  --dspark-confidence 0.45`, env `DSPARK_EXACT=0 DS4_DSPARK_STATS=1
  DS4_DSPARK_TOOLLESS_NOEXACT=1 DS4_DSPARK_SCHEDULER_WINDOW=12`
- **CONFIG FINALIZED 2026-11-09 (operator):** these four values are now the
  SCRIPT DEFAULTS (`V41_CTX=524288`, `V41_KV_SIZE=262144`=256 GiB,
  `DSPARK_EXACT=0` default, `DS4_DSPARK_TOOLLESS_NOEXACT` + `DS4_DSPARK_STATS`
  exported =1). Plain `./ds4-server.sh start-v41-dspark` boots the validated
  lane; BOOT TRAP retired (the old default was 262k — any restart without the
  env silently shrank the lane). Supersedes the morning "env override stays
  authoritative" decision. Live engine unaffected (already ran these values);
  defaults take effect on next boot. KV budgets now 256 GiB for ALL THREE
  lanes (ds4 KV_SIZE / qwen QWEN_KV_SIZE / ds41 V41_KV_SIZE = 262144 MiB
  each) — ceilings on a blade with ~483 GiB free: never run all three hot.
- 512k memory accounting: static ctx buffers 10.5→13.5 GiB (+3.0), expert cache
  auto-trimmed 73.96→70.96 GiB, engine RSS flat ~81-83 GiB, swap flat 0.8 GiB.
  Engine hard ceiling: `ds41_graph_alloc` rejects ctx > 1048576; model trained
  natively to 1M (rope_orig_ctx=1048576, no extrapolation). 1M = +31.7 GiB
  buffers — unattempted (would re-trim expert cache further).
- KV disk: `/Volumes/FireCuda520/ds4-kv-v41` (483 GiB free; ~8.2 KiB/tok stored).
  Post-reboot: drive was physically reconnected by operator; boot line binds the
  path AND a live store (15.09 MiB, 30.8 ms) landed on the external volume —
  external-KV proven, no internal fallback (only ds4_kvstore.{c,h,o} matched).
- auth_proxy **PID 26097** :8100 via `start-proxy` (pid-file auth_proxy.pid).
- Provider topology (opencode.json): `"ds41"` = ds4.c on THIS MBP :8003 — the
  lane all changes touch (context set to 524288); "DeepSeek V4.1 Flash (strata)"
  on nazryzen = different machine, still 262144, NEVER adjust for local state.

## 5. Run history on the fixed substrate (corewars overnight, then Nov 9 daytime)

### COREWARS OVERNIGHT 2026-11-08/09 (final definition: from 22:32:53 anchor, 134 min)

148 reqs / 116,988 tok / 0 errors / 0 reboots. Weighted accept **67.2%**, 2.16
acc/cycle, 3.16 tok/verify, 37,028 cycles. Decode medians per 30-min age bucket:
21.8/22.9/19.3/21.9/20.5 — flat in age (leak-fix holds). Accept RISES with depth
(65.2→67.2→68.6% through 70-110k); decode at 110k+ still ~19.5 vs mid-band 22.1.
Peak depth 124k — at the time the top of measured depth; 200k-512k band closed
later by doom rounds 2-3 (below). Verdict: lane approved as standing config;
monitor artifacts: /tmp/ds41_leg.snap.
### INCIDENT 2026-11-09 05:40:10 — pread-hang lane wedge (doom round 1, aborted mid-decode)

Request stuck mid-decode at gen=200 (ctx 37.9k, inside spec verify:
ds41_session_verify -> ds41_moe_batch -> stream_expert_pread_tasks): one worker
hard-blocked in pread() at ds4_metal.m:14355 on the MAIN GGUF (internal SSD),
coordinator waits task-completion forever; single-slot lane => total stall at
0.0% cpu, log frozen. Exonerated: sleep/TM/memory pressure/blade (volume ls OK,
DSpark gguf reads OK, kernel log clean). SIGKILL -> UNREAPABLE zombie (?Es,
thread wedged in-kernel); fresh-fd dd of main gguf still hangs AFTER the kill
=> vnode/driver-level wedge, userspace-unrecoverable. RECOVERY = system reboot,
then relaunch (lane config is script default since finalization; pre-that era:
four envs, BOOT TRAP) + re-arm monitor. Evidence was /tmp/ds41_stall.sample —
cleared by the reboot's tmp sweep, so the description here is canonical. First
occurrence ever of this signature; upstream
streaming pool has no pread timeout/watchdog — FILED upstream as antirez #1335
(2026-11-09; pool functions verified byte-identical to origin/main b3f5018; no
duplicate found: closest #238 GPU-level killable wedge closed unmerged, #1125
open PR covers pread-ERROR paths only).
Monitor since gained a stall tripwire (--stall 180s: inflight>0 + log silence +
cpu<1% => snapshot !! STALL line with leaf frame + desktop notification).
**RECOVERED 06:15-06:19**: operator rebooted + reconnected blade (device node
disk4->disk5, mount point stable); engine 1763 booted with all four envs,
external-KV live-store proven, smoke request speculated 68.75% immediately.
Monitor re-armed: /tmp/ds41_leg_doom2.snap (anchor 06:19:55, cap 5h).

### DOOM ROUND 2 2026-11-09 06:25:50-07:55:10 (89.3 min, anchor = operator's KV-hit resume line; harness-error end)

Mainline to 07:25:14: 32 reqs / 96,295 tok / 0 errors / wall 27.0 t/s
(prefill-inclusive). FORK LEG (corrected per operator — the 07:25:19-07:55:10
session was NOT a corewars resume): megaturn A hit the 64k cap, the harness
forked to a new chain file (58a9db63, 5,713 disk anchor), 119,424-tok re-append
to common=124,535, then its OWN 65,536-tok megaturn B (96.50% accept, ~40 t/s,
depth 190,673, finish=length 07:55:10). The two megaturns' IDENTICAL stats
(cycles=11,591, accepted=53,945 both) are explained: same conversation forked
-> regenerated ~identical doom-code continuation -> same drafter behavior.
**Round-2 true totals: 33 reqs / 161,831 tok / 89.3 min / 0 engine errors**;
ended by harbor/termbench harness ERROR after two consecutive 64k finish=length
messages (engine-side behaved to spec). Megaturn class reproduced twice.
Weighted accept 85.1% (mainline) = 31 normal tool-loop turns at 65.0%
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
not stall (§8 primer). Watcher: /tmp/ds41_leg_doom3b.snap FINAL.
### SHORTS 2026-11-09 11:27:44-11:48:25 (constraints + merge, sequential termbench tasks)

Combined 30 reqs / 17,145 tok / **0 errors**: constraints 9 reqs / 4,679 tok /
69.9% accept / dec med 24.3 / depth 13,076; merge 21 reqs / 12,466 tok / 67.2%
/ 22.1 / 22,780. Shallow-baseline reproducibility check — both land on the
lane's baseline band; monitor totals matched per-run sums (clean boundary).
### EXTRACT-MOVES-FROM-VIDEO 2026-11-09 12:17:33-13:51:26 (93.9 min of 11h attempt; clean stop end)

97 reqs / 62,615 tok / **0 errors** / 67.3% accept / 3.43 tok-verify / depth
131,560 / wall 11.1 t/s (LOW by design: 11 frame-appends >1500 tok, max
14,850 = biggest single append on the lane, prefill-heavy). FIRST VISION
WORKLOAD on the spec lane: all stats land inside the text-only envelope —
vision adds prefill load, no instability. CHAIN CORRECTION: this AND merge
hit cd9e2bf3/5497 => that chain is the SHARED termbench task preamble (free
5,497-tok disk-KV head start for every task), NOT doom-specific — earlier
"merge = doom phase" inference downgraded.

### TERMBENCH CIRCUIT 2026-11-10 10:23:21-11:20:05 (temp=0.3 test; single chain, clean stop end)

26 reqs / 80,199 tok / **0 errors** / wall 23.4 t/s / depth 91,686 / accept
72.4% / 3.60 tok-verify. TEMP-0.3 VERDICT: normal-turn (<=5k gen) weighted
accept **68.6%** = the standing 65-70% agent-traffic band => noexact accept is
TEMPERATURE-INSENSITIVE 0.3 vs 1.0-era (greedy-suffix drafts vs target argmax).
Band rise 64.2->73.4->85.0% CONFOUNDED by three ~16.5k turns, one 91.8%
machine-text ending in 64-96k band (§8 invariant (c) again). Decode
23.1/24.7/28.9 flat-up, age-flat, RSS 80.9G swap 0. First boot on finalized
defaults (PID 26079, plain `start-v41-dspark` — boot trap dead in practice).
NO disk-KV hit: new tasks = new chains; cd9e2bf3 preamble belongs to the old
task set only (invariant (b) scope note).
### EXTRACT LONG-RUN 2026-11-10 ~12:17-> in flight (12h watch extract12, temp 0.3)

NOTE (operator, verified in log): extract-class sessions RE-ENCODE images every
turn — 401 `vision embedding encoded` lines / 110 reqs (~3.6/turn) while
`multimodal live kv hit images=30 cached=104269 prompt=104862` shows 99.4% KV
token reuse. `cache=` bytes oscillate ~127.5 MB => fixed-size vision embedding
cache + eviction churn: each turn re-encodes frames the previous turn evicted.
Steady-state cost, not new-frame cost — refines the video verdict's "prefill
load" mechanism. TODO (vision-cache workstream): check encoder cache keying/
sizing vs harness re-posting full image history.

### F1 DEPLOYED 2026-11-10 14:27-14:28 + LIVE PROOF (operator ran gap block step 1)

Vision embed-cache 32/128MiB -> 128/2GiB built into PID **31549** (boot 14:28:01,
plain `stop` -> `make ds4-server` -> `start-v41-dspark`). The "cut" of extract-r2
cost NOTHING: stop landed on a turn boundary (last finish 14:25:13) and the
shutdown persisted the frontier — client resumed **14:33:13 with `kv cache hit
tokens=125465 load=171.5 ms`** (disk restore, ADR survival path proven in anger,
no cold re-prefill). F1 acceptance: same-15-image R1/R2 probe = 187.3 s/15
encodes vs 47.3 s/**0 encodes** + ~56 cache-hit lines. Attribution window:
smokes = ctx=0..38 (14:29:17) + ctx=0..8708 x2 (14:30:52, 14:33:35) — exclude
from run verdict. Real traffic now runs deep-vision turns on the FIXED binary =
live before/after (expect encodes/turn -> ~new-images-only vs pre-restart 3.6).
CONFIRMED: post-warm turns = 1 encode each (the new frame), kept-set hits ~92:1.
STATUS 15:26: run alive at 199k depth (~74k/hour climb); extract12 watch fired a
FALSE QUIET (log-rotation fd blind spot — README note, verdicts from log files);
re-armed extract12b. ETAs: 320k ~17:15, 512k wall ~20:00 (or compaction first).
STATUS 15:30 + KEEP decision: images=66 @205.9k, climbing 64/h => 129th image
~16:45 at ~270k depth — compaction does NOT arrive first; the reduce cliff is
REAL tonight. INTERIM (this gap): restart with DS4_VISION_KEEP_IMAGES=256
env-only — reduce never fires (drop=count-256=0), resilience holds (attestation
cap is actually SERVER_MAX_IMAGES=256, dynamic; adr.md AMENDMENT 2), cost =
encoder creep (count-128)x3 s/turn from 129 (~+2 min at 170 imgs, survivable;
byte-cap binds ~170). TONIGHT NO cache-256 rebuild: test_server_image_embedding
_cache needs 2-byte-key rework + ds4_test run = not in a live-run window.
TOMORROW: batched-quantized drop + cache 256 entries (test rework) properly.
FORCED RESTART 16:56:12 (operator decision — agent stuck in loop; wall test was
NOT resolving on its own). Killed mid-decode at ~360k (new lane high-water;
images=91 last seen). KEEP=256 live on PID **36389**, gap script retired
(never fired — the loop had no sustained gap). Watch = extract12c.
RESUME PROVEN 17:01:58: harbor re-attached; **`kv cache hit text tokens=354751
load=489.0 ms`** — first deep-vision disk restore ever (no re-prefill), one-time
re-warm ~88 encodes to cache=1.05 GB (all images retained, entries=128 cap not
even binding), session live at 358.7k @ 32 t/s, RSS 80.5G swap 0.
DECISION (operator): **KEEP=256 is now the SCRIPT DEFAULT** (128 -> 256; help
text synced; rationale in the comment: reduce never fires < SERVER_MAX_IMAGES
256 = whole histories served unmodified + disk-resilient; compaction rewrites
history long before counts matter). Wall test continues with the 128-cliff
outcome (d) structurally retired; gap script deleted (temp, purpose served).
F1 COMMITTED fork `e6b4c3c`. UPSTREAM: added as 3rd commit to PR **#1022**
(`8da90ed`, comment-verified hunk: upstream-tied wording, (size_t) cast) with
evidence comment posted — PR mergeable clean vs b3f5018. Watch state
post-warm: ~160 embed-cache hits / 0 re-encodes, 30-image history turns.

## 6. Open threads (priority order)

1. **Depth stress 320k-512k — WALL TEST IN FLIGHT 2026-11-10** (extract-r2,
   353,286 @16:52, climbing ~90k/h => ETA ~18:45). Engine boundary behavior is
   designed-graceful: prompt >= ctx -> protocol-standard context_length_exceeded
   (ds4_server.c:7606); so the test resolves to WHICH outcome fires first:
   (a) CLIENT COMPACTION pre-empting the wall (opencode auto-compact near limit
   => rebuilt prompt, ctx resets low, image count resets with it => KEEP cliff
   also evaporates — the clean ending); (b) HARBOR ERROR END (error response
   treated as task failure; engine stays clean, same class as R2's megaturn
   death); (c) MEMORY FIRST — jetsam/pressure before context math (RSS @353k
   watch: slope flattening vs the 1G/25k doom-era estimate, swap must stay 0);
   (d) 128-image cliff racing the wall (gap-restart script pre-empts with
   KEEP=256; success = `image budget` line never fires).
   Decided outcome + depth + exact lines to be banked when it resolves.
   Prior R3 evidence to 320k: no decay (23.5 @ >262k band); tonight's deep
   turns run 31-32 t/s — the lane so far STRENGTHENS with depth (88.1% accept).
   Remaining: last 39% of ceiling + footprint
   (~93G est @512k-full vs ~95-100 jetsam zone) unproven at full depth.
   [was: 200k-512k; 200k-320k CLOSED by doom3 2026-11-09] A second opencode
   session (corewars) may rejoin the single-slot lane anytime — attribute
   requests by ctx-chain continuity, not time (§8 primer).
2. **V41_CTX default** — CLOSED by finalization: script defaults now ARE the
   validated lane (§4); no env prefix needed at boot.
3. **Upstream watch #1330/#1331 + #1335 + #1022** — rework via `/tmp/pr-upstream-eventdrain` if
   antirez comments. Branch `fix/upstream-event-drain-reap` = `f8a454a`.
   #1335 (pread wedge): offered a deadline+error-path PR — file only if the
   maintainer bites. #1022 (KEEP knob): F1 embed-cache sizing landed on it as
   `8da90ed` 2026-11-10 (+evidence comment) — watch for review on the caps.
4. Hook test for mask primitives (review finding 4): `sample_build_probabilities`
   masked-row == probs[X]=0 + dspark block run never emits X (both exact/noexact).
   Engine-stop window required (`make test` lock rule).
5. `make cpu` link breakage — FIXED 2026-11-09 (fork-only symbol, no upstream
   issue warranted): `ds4_gpu_set_decode_phase` no-op stub for DS4_NO_GPU
   builds in ds4.c; worktree-verified make cpu green (pre-fix: undefined
   symbol x8 from generate_job_inner).
6. Chunk-rate telemetry artifact (shared progress state across prefill boundary)
   — filed in #1330 comment; one-field fix, unfixed.
7. HF token rotation (Nov 6 `--token` exposure) — operator-side.
8. **F2 vision skip-encode (KV-attested spans)** — principled follow-up to F1
   (named in the #1022 code comment + adr.md amendment): image whose span is
   fingerprint-attested + fully below live frontier -> placeholder from
   recorded token_count, NO encode call. Reuse the ADR's verification helpers/
   tests; needs 40-image growing-history soak (assert encoded-lines ≈ new/turn,
   cached covers image spans). Removes the F1 residual (O(n) memcmp + per-hit
   11.6 MB copy) and the post-restart re-warm cost. MAINAGENT-FIXBRIEF has the
   full design (§Q1/Q2 + order-of-ops).
9. **Vision staging checkpoint gap** — 1110 13:06: `KV payload staging failed:
   session has no valid checkpoint to stage` + boundary `token-mismatch` forced
   full cold re-prefill (76,204 tok / 125.7 s). Frontier-store gap; separate
   future ticket candidate (post #1335/#1022, not F1/F2 scope).

## 7. Files to remember

- Ledgers: this file (state), `PLAN-DECODE-DEGRADE-20261108.md` (decision trail),
  `DS41-SMOKE-20261106.md` (pre-fix baseline + corrections history),
  `UPSTREAM-TRANSIENT-LEAK-20261108.md` (issue text), `PR-EVENT-DRAIN-REAP.patch`.
- Harness (`decode-ab-harness/`): soak `ds41_soak.py`, legs `ds41_run_leg*.sh`,
  analyzer `ds41_ab3.py`, **spec collector `ds41_collect_spec.py`** (per-request
  deltas + leg wall + depth-band medians + `--since HH:MM:SS` window),
  **leg monitor `ds41_watch_leg.py`** (long-run liveness/spec/band snapshots).
- Harvest/analysis (outside repo): `~/Projects/termbench/harvest/extract-r2/` —
  mid-run container-workdir snapshot (179 MB, read-only), frame-read-manifest
  (57 image-visibility entries), HANDOVER.md, REPORT-EXTRACT-R2.md +
  MAINAGENT-FIXBRIEF.md (F1/F2 design + rejected knobs) + parse_extract_r2.py /
  reqs_extract_r2.json — the full F1 evidence chain (investigation-agent product).
- Code seams: `ds4_metal.m` transient fix + stats neighbors; `ds4.c`
  `ds41_session_speculate` (~90270, exclude param + mask sites),
  `ds4_session_dspark_stats_*` (~77360), `ds4_session_eval_speculative_ex`
  (~91300); `ds4_server.c` gate ~16540 (tool-less clause removed, knob checked
  at ~16270 request-scope — do not getenv inside the token loop).
- Models: `gguf/DeepSeek-V4.1-Flash-1017-IQ2_XXS-Q2_K-imatrix.gguf` (=
  `ds4flash.gguf`; 2.36 bpw main, engram E4M3 188.8 GiB disk-only) +
  `DeepSeek-V4.1-Flash-DSpark.gguf` (drafter, 81/81 verify).

## 8. Cold-session primer

`./ds4-server.sh status` should show the all-spec 512k lane (PID 26079 at
1110 10:16, or newer). Post-finalization `./ds4-server.sh start-v41-dspark` alone
boots the validated lane (§4). Invariants:
decode 19-23 t/s flat at ANY session age is the fixed behavior — if decay
returns, `DS4_METAL_CB_TIMES=1` list-growth check FIRST (everything else is
exonerated on record). Cross-leg comparison: use decode `avg=` medians banded
by context depth — NEVER raw wall for stochastic agent benches (legs diverge in
content). `dspark req:` prints per request when stats env is on; cycles>0 is
the positive proof, absence-of-evidence is banned reasoning here. Do not
re-litigate: eviction/hotness policy (3 null legs), "server never speculates"
(superseded §3), "dspark inert on tools" (superseded §3).
Three more invariants (Nov 9): (a) HARNESS-IDLE != STALL — engine silence with
inflight==0 (last event `finish=`) is client-side idle (make-doom runs MIPS
builds/sim locally); STALL = silence WITH inflight>0 + cpu<1% (watch `--stall
180`); quiet-exit 2400s absorbs sim gaps and inter-run gaps. (b) A `kv cache
hit` on chain cd9e2bf3 (5,497 tok) is the SHARED termbench task preamble —
every task gets that disk-KV head start, so chain reuse is NOT a session
resume; segment runs by ctx-chain continuity. (Scope note 1110: a genuinely
NEW termbench task does NOT hit it — circuit ran fully cold, so cd9e2bf3 is
the doom/merge/video family preamble, not universal.) (c) MEGATURN class: machine-text
turns (dumps) run ~96.5% accept / ~40 t/s and skew band-accept (it attaches at
request-END pos — within-turn avg= is the position-true evidence); two
consecutive 64k finish=length messages kill the harbor/termbench harness while
the engine stays error-free.
