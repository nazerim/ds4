# HANDOFF 2026-11-17 — what actually causes the 3-4 tok/s decode dips

Status: investigation complete, cause identified with a base-rate-controlled
measurement. One safe change shipped (observability). The fix itself is **not**
implemented — deliberately, see §6.

Live engine: PID 61218, started 1117 09:49 with `--mixed-prefill-quantum 2048`.
The revert to 512 (commit `1f9f3fa`) is **not in effect until the next restart**.

---

## 1. Headline

The dips are caused by **disk KV store/load executing while holding
`inference_mu`**, not by prefill interleaving and not by context depth.

Attribution over the whole 512-era log (`log/ds4-qwen.log.20261117-094911`,
46,536 decode samples, 101 of them <10 t/s), every candidate tested against the
base rate of the *other* 46,435 samples:

| Candidate | Slow samples near it | Base rate | Enrichment |
|---|---|---|---|
| **KV store/load event** [-3 s,+12 s] | **66/101 = 65.3%** | 8.9% | **7.32×** |
| Prefill chunk [-5 s,+30 s] | 101/101 = 100% | 86.6% | 1.15× |
| Context depth | none | — | flat/inverted |

Depth breakdown (slow %, mean t/s): `<50k 0.41% 59.1 | 50-100k 0.67% 80.0 |
100-200k 0.13% 70.8 | 200-300k 0.16% 68.2 | 300-400k 0.26% 62.2 | 400k+ 0.07%
57.9`. 400k+ is the *safest* band. There is no depth cliff.

Durations measured from the same log:
`KV store save ms: n=792 p50=258 p90=500 max=1156` ·
`KV load ms: n=81 p50=804 p90=2470 max=4893`.
**These store numbers are a floor, not the cost** — see §2.

## 2. Mechanism (verified in code, not inferred)

- Store: `ds4_server.c:12722` locks `inference_mu`, unlocks at `12927` — the file
  write happens inside.
- Load: `ds4_server.c:13187` locks `inference_mu`, `kv_mu` nested at `13196`,
  `ds4_kvstore_try_load_text()` runs inside both, unlock `13199`.
- An evict-routed request pays **store then load back-to-back**: `15928`
  (`kv_cache_store_current(...,"evict",...)`) → `15931` (`kv_cache_try_load`, in a
  2-try loop). Worst case ≈ 1.16 s + 4.89 s + retry of a multi-file chain.

A decoding slot needs `inference_mu`, so it parks for the whole disk op. That is
the 0.3–4.9 s hole, and it explains why the dips look "prefill-adjacent":
evictions and cold rebuilds happen around the same requests.

### 2b. Corrections after the follow-up audit (same day, later)

Both statements above are understated, and the line numbers shifted after
`cbc4db6` (store now `12733`→`12927`, load `13198`→`13205`):

1. **`save=` times only the tail of the store.** The timer starts at
   `ds4_kvstore.c:3301`, immediately before `fopen(tmp,"wb")`. Everything above
   it — `ds4_kvstore_evict()` at `:3296` (a directory scan), the two
   `kv_cache_refresh()` calls, `pick_parent` and its parent-chain walk with
   payload reads — is also I/O under `inference_mu` and is **unmeasured**. So
   p50 258 ms is a floor; do not size the locking win from it.
2. **`inference_mu` is `PTHREAD_MUTEX_RECURSIVE`** (`ds4_server.c:18797`;
   `kv_mu` is plain). That is why stores nested inside a prefill hold never
   deadlocked and went unnoticed: `server_progress_cb` calls
   `kv_cache_maybe_store_continued` (`14969`, `15010`) from inside
   `ds4_session_sync`, which `server_prefill_enter/leave` (`14609`/`14612`)
   already holds the lock for. Two consequences: the `cbc4db6` hold telemetry
   already measures this without new instrumentation, and any change that
   releases the lock must **skip the release when `s->prefill_hold_t0 > 0`** and
   keep the lock count balanced, or the recursive mutex leaks.

### 2c. Two store-path bugs found in that audit — bigger than the locking

Both verified by direct code reading; both are one-line-class fixes in
`kv_cache_maybe_store_continued` and were more costly than the lock:

1. **Continued stores passed the snapped-down grid `target` as the store length**
   while the payload is always the live session (`ds4.c:66475`,
   `rows = checkpoint.len`), so the header/payload length check at
   `ds4_kvstore.c:3871` rejected every such file. The invariant is stated in a
   comment 24 lines above the violation, and honoured by both divergence paths
   ("Store at the live length") — the continued path was the outlier. Measured:
   187 stores with `trimmed != 0`, 181 distinct files, mean 629 MiB = **113.62 GiB
   written** that can never load (the *resident* set at last-store-per-sha is 143 files
   / 88.78 GiB — two different quantities; the same sentence previously fused them); 41 observed discards, **41/41 traced to
   `trimmed != 0`**, none to `trimmed = 0`. Commit `969bc97` (2026-08-08) fixed
   this exact class for divergence anchors and missed the continued path.
2. **Divergence anchors fired on every chunk while climbing to their target.**
   The clearing block is guarded by `tokens->len >= div_target` but the store is
   guarded only by `div_target > 0`, so below the anchor the slot field is never
   cleared and a full store happens per chunk. Measured: one anchor → **342
   stores / 44.4 GiB in 12 minutes**, 341 with `tokens < target`; it also builds
   a 342-link un-retirable chain behind 14,352 deferral lines.

**Why these survived:** 52 `test_kv_*` functions exist and **none round-trips a
payload**. `test_kv_cache_continued_uses_aligned_frontiers` asserts the snap-down
target but says nothing about the length passed to the store — the green suite
documented bug (1) as intended behaviour. `--kv-delta` is the only real parity
test and always stores exactly at the live frontier, the one case that works.

## 3. Corrections — things said earlier today that are false

All three of these were stated to you as findings. Do not reuse them.

1. **My own** "92 of 101 dips in long-prefill windows, 9× enrichment". Wrong: no
   base-rate control (prefill lines are ubiquitous — 86.6% of *healthy* samples
   are near one too). Real enrichment is 1.15×.
2. **Mechanism subagent: fabricated evidence.** Its "quantum A/B at 1117
   02:50–03:07" and "0.44 t/s at ctx=488387" do not exist. Verified: the log has
   **zero** lines in 1117 02:50–03:10 (11-17 coverage is hours 00 and 09 only),
   `ctx=488387` appears nowhere, and there is exactly **one**
   `mixed_prefill_quantum=2048` startup (1117 09:49:13 — the idle live engine),
   so 2048 has served **no traffic ever**. Its `ple_prefetch_window` symbol does
   not exist either. Its structural read (one hold spans the engine's internal
   chunk loop; PLE join inside it, `ds4.c:80816-80846`) did check out — but treat
   every number in that report as unverified.
3. **My commit message for `676eca7`** claimed 2048 means "continued checkpoints
   still land on their rungs". Overstated: slice positions are `frontier +
   N*quantum`, so 2048 reaches 1-in-4 of the 8192 rungs vs 15-in-16 at 512;
   snap-down (`ds4_kvstore.c`, `DS4_KV_CONTINUED_SNAP`) softens this but the claim
   as written was wrong. Corrected in `1f9f3fa`.

The design subagent's numbers I spot-checked were accurate
(store/load percentiles above match its report exactly).

## 4. Shipped

- `2b06dc7` — **FIX 1 + FIX 2 (§2c) shipped**, with the store decisions extracted
  into pure helpers and 5 new tests plus one corrected test. Proven by mutation,
  not by assertion: reinstating either bug fails named asserts, and reinstating
  the early-return branch with the helper left fixed still fails the driving test,
  so the suite catches call-site regressions rather than its own stub
  bookkeeping. Independent review: SHIP-WITH-CHANGES, both findings closed (the
  sibling `covered` wiring is now pinned; fixtures assert `e.ok` so a broken
  `/tmp` cannot make the suite vacuously green). Not in this commit: the store
  split, `fsync`, the raw-unlink sites, and the §7 vision cadence.
- `cbc4db6` — **observability**. `server_prefill_leave()` now takes slot + slice
  window and accounts hold count/total/max under `inference_mu`; holds ≥
  `DS4_PREFILL_HOLD_WARN_SEC` (default 2.0 s, 0 = silent) log slot, tokens,
  remaining, quantum. Observability only, no behaviour/numerics change. This is
  what lets a future fix be validated; previously a multi-second blackout left no
  direct trace.
- `1f9f3fa` — **revert quantum 2048 → 512**. Independent of the dip attribution:
  the quantum is the unit of executor *holdback* (one `ds4_session_sync()` holds
  `inference_mu` across every internal engine chunk — verified `ds4.c:80816-80846`,
  server release `ds4_server.c:14612`), it changes FP reduction order
  (`ds4.c:1059-1062`) and MTP prep boundaries, it thins ladder coverage, and it
  served zero traffic. So it was an unmeasured numerics change that doubles
  worst-case holdback. `QWEN_MIXED_QUANTUM=2048` still A/Bs it.

`make` clean, `make test-server-logic` → ok. Pushed to `nazerim/main`.

## 5. Explicitly NOT recommended

**Serialize-prefill (design "C": gate + block + cap)**, from the design agent's
~90–120 line plan. Its premise was that co-residency causes the dips. It does
not (1.15×). Blocking a decode slot behind a whole multi-hundred-second load
would remove the 1.7× prefill-recovery upside, lengthen every hold, and leave the
real cause untouched — and under a hold, §2 disk ops would block even harder
(the design itself flags this at its §6.7). Its structural findings about the
rewind exemption and the `quantum=0` spin (`done < target` is strict,
`ds4_server.c:14616`) are still correct and worth keeping in mind.

## 6. The fix, and why it isn't in yet

**Reordered by the follow-up audit (see §2b/§2c).** The locking change is still
worth doing but it is no longer first: two one-line-class store bugs were
consuming more I/O than the lock ever did. Sequence:

1. **[DONE 2b06dc7] Continued-store length fix** (§2c.1) — makes *new* doomed files
   impossible. **Frees nothing:** the fix is prospective, and the 143 existing files /
   88.78 GiB (34.7 % of the 255.95 GiB cache) are still resident until the loader meets
   them or eviction retires them. Measured 2026-11-17, `20261117-kv/census-doomed-kv-files.md`.
2. **[DONE 2b06dc7] Divergence-anchor firing fix** (§2c.2) — removes ~342 stores / 44.4 GiB per
   anchor event, i.e. ~97% of cold-store bytes.
3. **[DONE 2b06dc7, via helper+hook seam -- not a payload round-trip] Test coverage at the production call sites** (§2c, "why these survived") —
   otherwise the suite keeps encoding the wrong contract.
4. **Baseline the `cbc4db6` hold telemetry on real traffic** — needed because
   `save=` is only the tail (§2b.1), so no under-lock cost is known yet.
5. **[RESOLVED f90433f — no guard change needed] Loader `unlink` without the
   `children > 0` check.** Enumerating every unlink site inverted the finding:
   `kv_cache_unlink_entry`'s deferral exists to protect a file that is still
   *loadable* but has dependents, whereas the loader reaches its raw unlinks only
   for a file the chain walk just proved unreachable -- and `path` there is the
   **candidate, the child end of the chain**, not the missing ancestor. `children`
   is a refcount re-derived from a directory scan by `kv_cache_refresh()`, which
   runs at the top of every load and every eviction pass, so a raw unlink cannot
   strand a parent. The measured orphan was **a symptom of FIX 1**: parent
   `25c52da8` stored `tokens=81920 trimmed=1 reason=continued`, a child chained off
   it, then the loader discarded it -- deleting it was correct. Routing the loader
   through the helper would have *worsened* things (keeps a permanently useless
   file, re-invalidates the live session on every future load of that prefix, and
   `memmove`s `kc->entry` while `try_load_text` retries keyed on entry indices).
   Shipped as comments at the five sites + two model-free tests; zero production
   statements changed (verified by comment-stripped diff and `nm`).
6. **[DEPRIORITISED — see §9] S1 — split the store at the staging boundary.**
   Superseded by measurement: every continued store is nested inside a prefill
   hold on a recursive mutex, so the release frees nothing, and stores are 1.5%
   of hold time. Revisit only if a real-traffic re-measure (with evict storms)
   contradicts §9. If it is ever done: release `inference_mu` after staging, keep
   `kv_mu` for format work, **never re-acquire** (re-acquiring risks ABBA against
   every load, since the order today is `inference_mu → kv_mu`), and skip the
   release when `prefill_hold_t0 > 0` or the recursive mutex leaks.
   Precedent: `.codebase-memory/adr.md:317`.
6b. **[ALSO A DEAD END] Load path.** Promoted to replace S1, then killed by the
    same standard: `ds4_session_load_payload_span(session, fp, ...)`
    (`ds4_kvstore.c:3831`/`3856`) streams rows **from the file into the session**,
    so the disk read *is* the mutation -- there is no "read outside, swap inside"
    seam for the payload. Only `kv_cache_refresh()` and the candidate header
    probes could move out, and p50 821 ms for a ~380 MiB payload is the stream
    (~463 MB/s), not the probe. The back-to-back evict store+load (`15928`→`15931`)
    remains the worst single stall in principle and is **unmeasured under real
    traffic** -- that is the one open item here, not a code change.
7. **[IN PROGRESS] `fsync` before `rename`.** There is none today (`fsync` appears
   only at `ds4_agent.c:7193`) and **the payload has no integrity check at all**:
   the `sha` in the store log and the "hash/prefix mismatch" comment are the sha1
   of the *prompt text* (also the filename) -- nothing checksums the KV rows. So a
   renamed file whose bytes never left the page cache loads stale/zeroed rows that
   still pass magic, size, token count and tokenizer fingerprint. This is the only
   path found that can serve *wrong output* rather than a cache miss.
   The earlier "do it last so its cost lands outside the lock" rationale is now
   **void** -- S1 is dead, so the barrier lands inside the lock; measured cost is
   ~2.4 ms per store against a 104-175 ms tail, which is why it is still worth it.
   `F_FULLFSYNC` (the media-forcing barrier) returned EFAULT from a Python probe on
   *both* internal and external APFS, i.e. a calling-convention artifact, not
   ENOTSUP -- being settled in C, and its absence would mean this closes the panic
   window and the device-cache window but not a drive with volatile cache.

**Status as of the unattended run later the same day:** steps 1-3 shipped
(`2b06dc7`, `f90433f`), 4 was banked from live load (§8), 6 and 6b were killed by
measurement (§9, above), and 7 is in progress. What genuinely remains undone is
not code: re-measure under **real agent traffic** -- compaction-driven evictions
and cold reloads, the one regime my synthetic load never produced -- to confirm
the 1.5% store-tail figure and to settle whether the back-to-back evict
store+load is a real stall. And re-confirm that the ~35 non-KV-adjacent dips do
*not* move, which is how we tell "fixed the KV cause" from "fixed nothing".

## 7. Open

- **Vision rung cadence — separate scope, deliberately not addressed here.**
  FIX 1 (§2c) has a side effect on image sessions: the vision gate
  `okv = vs->valid && store_len == tokens->len` (`ds4_server.c:12798-12802`)
  used to fail for every snapped rung store, and the failure path is
  `if (!okv) { log(vrej = "not-frontier"); return false; }` — the store was
  **skipped entirely**, not written text-only, so no correctness hazard either
  way. `server_progress_cb` calls `kv_cache_maybe_store_continued` with **no
  multimodal guard**, so with the fix, deep Vision-Exp prefills now write rungs
  they never wrote: ~7-9 stores per 100k tokens of growth (`anchor_step` 8192,
  doubling above 48k) at ~630 MiB mean ≈ **4.5-5.5 GiB per 100k tokens**, each
  currently under `inference_mu`, on a KV dir at 255.9/256 GiB. Benefit is real
  (resumable rungs instead of a full ladder re-append), but it is derived
  arithmetic, not measurement, and the path is untested without a model. Zero
  vision stores in the two-day window (`reason=turn` = 0, `not-frontier` = 0), so
  this is latent until image traffic. Options if it costs too much: widen the
  vision rung step, or gate the behaviour on a deep-session test — do **not**
  re-introduce the skip as FIX 1's exception.
- 35 of 101 dips unexplained. Candidates, **none yet verified**: MTP/DSpark
  verify stalls, generation rewind rebuilds (`~350 t/s` per the design agent's
  log read), the vision encoder if it runs under `inference_mu` (unconfirmed —
  the subagent's `11144` anchor is actually `server_inference_lock()`'s
  definition, so re-derive this one), and KV ops whose log line falls just
  outside the ±3/+12 s window.
- `DS4_VISION_KEEP_IMAGES=256` at 512k: `/Volumes/FireCuda520` 83% full (331 GiB
  free), KV dir at its 256 GiB budget.
- Upstream still frozen at `b3f5018` (Nov 3); no new server-PR movement.

## 10. Unattended run, later on 2026-11-17 — what shipped and what was disproved

Commits: `2b06dc7` (store length + anchor firing), `f90433f` (unlink verdict +
tests), `bf64327` (durability barrier). All pushed to `nazerim/main`; engine
restarted onto the final build (PID 73570, `--batched-session 2 --ctx 524288
--mixed-prefill-quantum 512`, no debug env, 0 errors).

**Live verification of the barrier.** With `DS4_KV_DEBUG=1` temporarily exported
(then removed by a clean restart), 6 of 6 stores executed it: `store sync=` mean
**4.05 ms**, max 4.40 ms — within prediction from the C probe. The honest cost
figure is that `sync=` line, **not** a `save=` mean comparison: this window's
`save=` averaged 166.2 ms vs 106.2 ms earlier, but these were bigger rungs
(114688/131072 tokens → 647 MiB files), and `save=` includes the payload write.
0 discards, 0 errors.

**Cross-restart reuse demonstrated.** A 96,883-token prompt that took 75.6 s cold
completed in 12.1 s after the restart, with `cached=81920` served from a checkpoint
written before the restart.

**Still not exercised live:** a `continued` store crossing a rung at an off-grid
live length. Every crossing landed grid-aligned because prefill advances in
2048-aligned chunks from grid-aligned resume points. Off-grid stores did appear
(`tokens=109199`, `121497`, both `trimmed=0`) but with `reason=evict`, a path that
always stored at the live length. So FIX 1's specific branch rests on code reading
plus the mutation battery, while its aggregate effect is visible: 24% of stores
were `trimmed != 0` historically, and every store since has been `trimmed=0`.

**Two locking projects closed on measurement, not opinion.** S1 (store split) and
the load-path change are both dead — §9 and §6b. The 3-4 t/s dips are prefill
compute holding `inference_mu`, which the hold telemetry showed directly
(1.55 s mean, 3.92 s worst, at 522-890 t/s per 2048-token chunk).

**Errors caught in this run that were nearly shipped as facts:** a Python
`F_FULLFSYNC` probe returning EFAULT was almost reported as "the bridge cannot
media-flush" — it failed identically on internal APFS, so it was a calling
convention; and the barrier's first cut silently compiled the media flush out
because `<fcntl.h>` was missing, with a clean warning-free build. `nm` for
`_fcntl` caught it; the `#error` guard now makes that a build failure. Lesson
recorded in the code comment: a clean build is not evidence a preprocessor-guarded
path exists.

## 11. Evidence archive

Working artifacts, agent reports and reproduction drivers for this investigation are
in `.codebase-memory/20261117-kv/` — read **`INDEX.md` first**: it rates each report
for trust, lists which recommendations were later disproved, and enumerates the
figures from this chain that were fabricated or retracted. They were originally in
the session temp dir, which has already destroyed evidence once (two files still
cited by `.codebase-memory` docs no longer exist there).

## 8. First executor-hold baseline (2026-11-17 13:21-13:27, PID 69332)

Synthetic load after the 2b06dc7 restart: four multi-turn prefills of 55k-97k
tokens, `quantum=2048` idle / 512 mixed, box otherwise idle.

```
prefill hold slot=0 2.62s tokens=30720..32768 remaining=22490 quantum=2048
prefill hold slot=0 2.49s tokens=77824..79872 remaining=10595 quantum=2048
   (holds=105  total=163.0s  max=3.92s)
```

- **Mean hold 1.55 s, worst 3.92 s** against a store `save=` of 104-175 ms — so
  the executor is held **10-20x longer than the measured store tail**, which is
  exactly the §2b.1 blind spot. 163.0 s of hold in ~6 minutes of load.
- This is the "before" number for S1. Judge S1 on total hold seconds and worst
  hold at comparable token volume, not on `save=`.

**FIX 2 confirmed live:** 3 anchor stores in this window vs **342** in the
historical episode, each firing once at the live length once its target was
reached (`divergence anchor stored tokens=67584 (target 66993)`).

**FIX 1 only partly confirmed live:** 23 stores, all `trimmed=0`, zero discards,
and 4 genuine disk loads including an off-grid one (`kv cache hit text
tokens=67584 ... chain=9 load=231.1 ms`, serving 74.7% of a 90,467-token prompt)
— so stored payloads really do round-trip. But **no off-grid `continued` rung
store occurred**: every rung crossing landed grid-aligned because cold prefill
advances in 8192-aligned 2048-token chunks, and pre-fix traffic produced
`trimmed=0` in that case too. That specific path stays proven by code logic plus
mutation tests only; a natural decode-advanced frontier in real traffic settles it.

Other health: 0 errors; decode mean 65.0 t/s (thin sample); KV dir still pinned
at 256 G, the ~89 GiB of pre-fix doomed files draining only as the loader meets
them or eviction retires them.

## 9. S1 (store split) is deprioritised — evidence, not preference

Two independent reasons, both measured after 2b06dc7 was live:

1. **It cannot work where it matters.** Both continued-store call sites are
   inside `server_progress_cb` (`ds4_server.c:15073`, `15114`), which the engine
   calls from within `ds4_session_sync` — so *every* rung store is nested inside
   a prefill hold. `inference_mu` is `PTHREAD_MUTEX_RECURSIVE` (§2b.2), so a
   "release after staging" there only decrements the nesting count and frees
   nothing. The audit's own guard ("skip the release when `prefill_hold_t0 > 0`")
   therefore applies to essentially all continued stores, i.e. S1 as designed
   delivers ~nothing for the dominant store class.
2. **The prize is small anyway.** Holds are 2048-token chunks measured at
   522-890 t/s, so a hold is prefill compute, not disk: 23 stores contributed
   **2.44 s against 163.0 s of total hold = 1.5%**. Even if the unmeasured
   pre-`save=` stages (evict scan, two `kv_cache_refresh` calls, parent-chain
   walk) tripled that, S1 caps out around 3-5% of hold time. Not worth
   durability-path surgery.

Note the trap in the historical aggregate: 238.3 s of store tail vs 96.3 s of
load tail looks like stores dominate, but that traffic predates FIX 2, which
removes ~341 of every 342 anchor stores, and FIX 1, which removes 41
discard→re-store cycles. Post-fix is the only interpretable baseline, and it is
the 1.5% above.

**What still stands, and is now the priority instead:**

- **The load path**, not the store path. Loads are fully under `inference_mu`
  (`13198`→`13205`), 3-5x longer per event (p50 821 ms, p90 2.47 s, max 4.89 s),
  and the file *read* — unlike the session swap that follows it — plausibly does
  not need the lock at all.
- **The back-to-back evict store+load** (`15928`→`15931`, with a 2-try reload
  loop): up to ~1.16 s + 4.89 s of contiguous blackout in one request. Across all
  retained qwen logs I counted **107 `evicting checkpoint` events / 18.92M tokens
  dropped**, so the regime is real and frequent; my synthetic load did not
  reproduce it, which is why the 1.5% figure should not be trusted outside it.
- **`fsync` before rename** — a correctness gap, independent of any locking work,
  and the only finding here that can serve wrong output.

Caveat on generality: the 1.5% comes from synthetic multi-turn prefills with 0
evict storms. Before closing S1 permanently, re-measure under real agent traffic
(compaction-driven evictions + cold reloads), where store+load land back-to-back
under the same lock.

---

## Close-out — 2026-11-18 01:20 (this run ended here)

**Shipped publicly.** Upstream PR **antirez/ds4#1358** (durability barrier, `dcf77c4`,
+47/−0 in `ds4_kvstore.c`, base `b3f5018`, `MERGEABLE`) and a comment on **#1020**
re-opening the representation question. Both posted texts are committed in
`20261117-kv/post-*.md` and were read back hash-identical.

**Fork.** The review's errno fix was back-ported (`8ec3f2c`) and the owner restarted production at 01:15:39; engine PID 87193 verified
answering correctly with 0 log errors, running the 01:09 binary that carries it.

**Gate record.** `make clean && make` 0 warnings; `./ds4_test --server` passes; speed is
parity measured A/B/B/A ×2 reps (prefill +0.52 %, gen −0.27 %, both below the largest
same-arm spread); the barrier was **executed** 12/12 times on the upstream tree with
`fsync=0/0 fullsync=0/0`. Full `make test` remains unachievable here and is disclosed as
such — budget refusals on the V4.1 fixture plus DeepSeek V4-era files this build rejects
outright.

**Knowledge corrected, not just added.** Census measured our own unloadable-checkpoint
figure (143 files / 88.78 GiB resident / 34.7 %, nothing reclaimed); the fused
written-vs-resident arithmetic was split apart in both `adr.md` and this file; `INDEX.md`
carried a **false retraction** (the 93/17,463,570 figure was reproduced, not unverified)
and a **misattribution** (1,353/859 t/s belong to the design agent, unreproduced rather
than invented — which also taints §2.4's counterfactual); the "1.5 % with no evict storms"
caveat is false as written; the 84-87 % metric is a union and is circular unless the
triggering request is excluded.

**Deliberately NOT closed.**
- **The RFC is held.** Its motivating claim was reversed during review: the Aug-7 trace
  exercised our lineage ranking, not upstream's score-based evictor (`599e49d`/`ec6a82a`,
  May). It needs a re-derivation against their evictor at storm density — the same run
  that would reopen S1 — before it says anything honest.
- ~35 of 101 decode dips still unattributed; the parked scope items (in-place trailer
  truncate, payload checksum) remain parked.
- FIX 1's off-grid `continued` branch: still never observed live; code reading plus the
  mutation battery only.
- `F_FULLFSYNC` across the Thunderbolt bridge: unresolved. ret 0/errno 0 is not proof of a
  media commit, and the proof run used the internal volume.
- Upstream **#1331** (ours) unreviewed since Nov 8; deliberately not nudged as well, to
  avoid turning the inbox into noise.
- Two standing commitments made in public: rebase #1358 onto #1343 if that lands first
  (same function, adjacent hunks), and reconcile the `ftell` anchor between #1020 and
  #1343 if either merges.
- GitHub shows no Verified badge: the SSH signing key is not registered on the account
  (all our PRs show blank status). Operator action, cosmetic — see `20261117-kv/SIGNATURE-NOTE.md`.

**Housekeeping.** Baseline bench worktree and 2.1 GiB of probe checkpoints deleted; the
empty probe dir removed from the cache volume; production cache confirmed untouched at 753
files. Tree clean, 0 unpushed, 0 behind upstream.

### Resume cold from here (next session)

Read in this order, nothing else first: (1) this close-out, (2) `20261117-kv/INDEX.md`
trust table, (3) `20261117-kv/UPSTREAM-PR-barrier.md` gate record, (4) the three ADRs
dated 2026-11-17 in `adr.md`. Do not follow any path in an older doc without checking
`DOC-ROT-20261117.md`.

Live state: production engine PID 87193 since 01:15:39 on the binary built 01:09:23,
which carries the tolerated-`errno` fix (`8ec3f2c`). Fork `main` pushed; branch
`fix/kvstore-write-barrier` = `dcf77c4` on `nazerim`, worktree
`~/Projects/ds4-pr-kvstore-barrier` kept for rebase work. `origin/main` still `b3f5018`.

**If a maintainer replies on #1358:** the two commitments made publicly are (a) rebase
onto #1343 if it lands first -- same function `ds4_kvstore_store_live_prefix_text`,
adjacent hunks -- and (b) accept an `fsync`-only outcome without arguing, because the
finding is the missing barrier, not the choice of barrier.

**If #1020 gets a reply:** he may name a different representation than `ext_flags`; the
open decision is whether weight-identity invalidation stays out of that PR.

**The RFC blocker, stated accurately:** the Aug-7 743-request trace is **not retained**
-- `adr.md` cites no file for it and no matching artifact exists on this host (verified
2026-11-18; the only `743` hit is an unrelated log timestamp `074323`). So the
re-derivation against upstream's score-based evictor cannot be done by re-analysis; it
needs a **new** comparable workload at evict-storm density, which is a recording session
on the live engine, not an afternoon of grep. That is the honest cost of unblocking the
RFC, and it is the same run that would settle whether S1 stays closed.

**Smaller open items, in rough value order:** the `84-87 %` correlation needs the union
trigger and same-request exclusion before it can be quoted at all; ~35 of 101 decode dips
unattributed; FIX 1's off-grid `continued` branch still unobserved live; `F_FULLFSYNC`
across the Thunderbolt bridge unproven (ret 0 is not a media commit); #1331 unreviewed
since Nov 8 and deliberately left un-nudged; GitHub signature badge needs the SSH signing
key registered (operator action, `20261117-kv/SIGNATURE-NOTE.md`).

**Two process lessons from this run, because they are the kind that recur.** Editing
markdown by replacing a *paragraph* destroys every sibling bullet in it -- a bullet list
is one block (this deleted five entries from the trust table). And porting a patch to
another tree requires re-verifying every noun in its *prose*: the fork's comment about
tokenizer fingerprints is true here and false upstream, and the port nearly shipped it.

### Addendum 2026-11-18 (overnight A/B run) -- first pass; its capacity conclusion is REFUTED by Addendum 2 below

The "record a new workload" blocker is **done**: `20261118-kvstorm/` holds the generator, protocol (pre-registered),
four arms (A/B/B/A) and `20261118-kvstorm/RESULTS.md`. Verdict by the pre-registered rule: **inconclusive for the RFC premise.**
Upstream returned 76.5 % cold rebuilds vs the fork's 23.5 % at a 6 GiB budget (same-arm spread <= 0.2 pts), but upstream
evicted only as `disk-cache-full` (no retirement class) and the 6 GiB budget held 29 fork files vs 11 upstream, so
**capacity (delta chains vs full checkpoints), not eviction order, is the leading explanation.** The RFC stays unposted.
Next experiment: rerun at a budget both trees can fit (~16 GiB) -- see `20261118-kvstorm/RESULTS.md`. Also found: with production's
`--kv-cache-retire-grace-seconds 3600` the budget is advisory (0 evictions, 35 GB in a 6 GiB budget over 129 requests).
Production was stopped 02:26-06:23 for this and restored via `start-qwen` (PID 92879), cache untouched at 753 files.
`~/Projects/ds4-ab-upstream` (b3f5018, built) is kept for the follow-up.

### Addendum 2 -- 2026-11-18 14:30 (follow-up arms; this is the current state of the RFC question)

Read `20261118-kvstorm/RESULTS.md` section FINAL. Short form: the 6 GiB result was NOT capacity. Upstream stays at 63 % cold
rebuilds with 2.7x the budget (16 GiB), but with eviction removed (u400, 400 GiB) it is **0 % cold** -- so eviction is the whole
explanation. Mechanism, measured and read in code (`ds4_kvstore_entry_eviction_score`, score `(hits+1)*tokens/size`, 6 h hit
half-life, no recency term for zero-hit entries): 98-99 % of upstream's victims had zero hits, median store->evict lifetime 5 s.
A 20-line experimental recency boost (`~/Projects/ds4-ab-evictfix`, diff vs b3f5018) takes it to 5.1 % cold (rebuilt tokens -87 %).
Earlier analysis bugs, all fixed and re-reviewed: overlapping +/-1 s attribution windows (now finish-line segmentation),
live-reuse prefill counted as 0, prompt cross-check only covered cold requests.

**NOT to be filed -- prior art confirmed** (`gh` check 2026-11-18): **#1170** already states the mechanism, **#986** already implements the fix
(age the score's constant `+1` with the existing 6 h hit decay), #444/#711/#814 cover density/cold-vs-live, #550 was a closed earlier attempt.
Nothing merged, so it is real and unfixed but not novel: at most a corrected measured-evidence comment on #986/#1170, owner's call.
My tie-break claim from an earlier pass is RETRACTED (exact-double ties are 0.14 % of zero-hit entries, measured on the live cache); the surviving
claim is only "with hits==0 the score has no age term". See `20261118-kvstorm/PRIOR-ART-AND-CORRECTIONS.md`.
**Cache-dependent outputs: benign FP numerics** (upstream's own comment `ds4.c:1052-1053` says chunk boundaries change reduction order; payload load is a
raw byte dump; first divergence at request 53 with all 78 turn-1 replies agreeing). **Tokeniser suffix risk: MEASURED PASS** 2026-11-18 15:57 in a 97 s
window -- `tests/test_kv_prefix_stability.c`, 8/8 cases stable, raw output in `20261118-kvstorm/PREFIX-STABILITY-RUN.{txt,err}`. Scope is narrow: it covers
the invalid-tool-call path (cut mid malformed tool call, where the code actually runs), and the Qwen spaced markers were seen tokenising as plain text, so
the merge hazard was live and did not fire. **Still untested: normal turn continuation**, which cuts at a completed assistant turn -- my omission, and it
needs only a case-table extension, no window.
UPDATE 2026-11-18 late: that extension is largely moot -- `append_rendered_suffix_to_live_session` has exactly ONE caller
(`continue_after_invalid_dsml`, ds4_server.c:14990), which is the boundary the probe already covers. Normal continuation never uses the
helper. Also settled from GGUF metadata (`20261118-kvstorm/check_marker_specials.py`, v3/vocab 248320/27 CONTROLs, no added_tokens key):
the SPACED markers the Qwen error builder emits are NOT special tokens (they tokenise as text), so the hazard was live and the PASS is
meaningful. Probe exit codes de-clashed from the engine's own `exit(2)`: 0 PASS / 1 FAIL / 3 INVALID / 4 usage / 5 could-not-run.

CACHE-PATH OUTPUT DIVERGENCE -- measured; see `20261118-kvstorm/CACHE-DIVERGENCE.md`. 161 controlled comparisons,
1 divergence. Cold rebuild vs disk resume is bit-identical on the fork AND upstream b3f5018 at arms-scale prompts
(16/16 at 11.6k-20.3k tokens, generations to 512 tok, resume loading up to 91% of a prompt from disk), and the
engine is deterministic across processes (12/12). The single anomaly: the same prompt resumed at checkpoint depth
10240 vs 8192 matched for 298 chars then diverged (difflib 0.633) -- found by accident, never deliberately
reproduced. That is the live lead: if checkpoint survival (arbitrary, per the density finding) picks resume depth,
then eviction can change answers, rarely.

DECISIVE TEST RUN, same evening, on PURPOSE (`20261118-kvstorm/resume_depth_probe.py`): resuming an 11,699-token prompt from the mid-prefill
`reason=continued` checkpoint at ctx=8192 changes the completion vs cold prefill -- fork 8/8 differ, upstream b3f5018 7/8
differ (median similarity ~0.25, first difference ~170-230 chars in) -- while resuming from the aligned `reason=cold`
checkpoint at ctx=10240 is byte-identical to cold in 28/28 comparisons. So: a checkpoint the engine treats as a valid
SHA-matched prefix does not restore equivalent state. Both builds => NOT a fork regression. Mechanism NOT established
(candidate: state that completes only at end-of-prefill, or per-layer KV compression ratios disagreeing on an 8192
boundary). This also explains the u16a-vs-u400 reply divergence that the earlier cache-stability results could not.
Prior art searched (read-only): #1266/#1186/#174 are about continued checkpoints being EVICTED; nothing on output
changing. Draft issue written but NOT POSTED -- `20261118-kvstorm/ISSUE-DRAFT-resume-depth.md` needs owner's go.

MECHANISM, corrected same day by an independent Opus pass I then verified myself: the harm is the DENSITY term, not the missing age term.
`size(MiB)=113.518+0.0300516*tokens` (max residual 0.0073 over 1342 lines, re-fit by me), so a positive fixed payload intercept makes
`tokens/size` strictly increasing in tokens => for `hits==0` upstream simply evicts the fewest-token file. Proof in the log: u16a wrote 309
`reason=cold` checkpoints and all 309 were evicted. A new offline replay (`20261118-kvstorm/evict_replay.py`, no model/GPU, <1 s/policy,
validated to the digit on all three live arms) shows upstream is the WORST of seven policies at identical 16 GiB capacity -- worse than random
(13.6%) and worse than adversarial MRU (44.9%) -- which supplies the same-budget control the earlier 'u400 is a ceiling' note said was missing.
Recommendation now: the finding is third-hand, the METHOD is the contribution; do not run another live eviction arm. INDEPENDENTLY VERIFIED after writing: an adversarial re-review on a different model re-derived every headline number
from raw logs without reusing the scripts here (SURVIVES) and returned 4 corrections now folded into RESULTS.md -- lifetime percentages are
match-order dependent (FIFO 75%<=10s vs LIFO 58%), zero-hit entries TIE so the `last_used` tiebreak picks the OLDER victim, u400 is a ceiling not an
isolation, and the untuned 300 s half-life sits at this workload's p90 revisit gap (~315 s), so the remedy is validated at its fitness boundary.

**Open:** replies differ between arms with different cache behaviour even on the SAME binary (u16a vs u400 agree 247/430) --
recomputed vs loaded prefix state gives different temperature-0 output; unexplained and correctness-relevant. Boost constants
(8.0 / 300 s) are guesses; sweep needed before proposing numbers. Prod was stopped 02:26-06:23, 10:36-11:15(killed by a shell
kill; chain resumed), 11:16-13:36 and 13:38-14:17; each time restored by `start-qwen`, cache untouched at 753 files.
Worktrees kept: `ds4-ab-upstream` (b3f5018 built), `ds4-ab-evictfix` (patched, built).
