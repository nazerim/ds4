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
   187 stores with `trimmed != 0`, 181 distinct files, mean 629 MiB ≈ 88+ GiB
   written that can never load; 41 observed discards, **41/41 traced to
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

1. **[DONE 2b06dc7] Continued-store length fix** (§2c.1) — makes doomed files loadable, frees
   ~88 GiB / ~35% of the 256 GiB budget.
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
  loop): up to ~1.16 s + 4.89 s of contiguous blackout in one request. Production
  saw 93 evictions in two days; my synthetic load did not reproduce that regime,
  so this is the one place where the 1.5% figure should not be trusted.
- **`fsync` before rename** — a correctness gap, independent of any locking work,
  and the only finding here that can serve wrong output.

Caveat on generality: the 1.5% comes from synthetic multi-turn prefills with 0
evict storms. Before closing S1 permanently, re-measure under real agent traffic
(compaction-driven evictions + cold reloads), where store+load land back-to-back
under the same lock.
