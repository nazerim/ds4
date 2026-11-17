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
5. **Loader `unlink` without the `children > 0` check** (4 raw unlink sites) —
   strands chain descendants; currently latent (1 orphan, 0.57 GiB, and 0
   `delta chain broken` lines so far).
6. **S1 — split the store at the staging boundary**, releasing `inference_mu`
   after staging and keeping `kv_mu` for format work, **never re-acquiring**
   (re-acquiring risks ABBA against every load, since the order today is
   `inference_mu → kv_mu`). Must skip the release when `prefill_hold_t0 > 0`
   (§2b.2). Precedent: `.codebase-memory/adr.md:317`.
7. **`fsync` before `rename` — last, deliberately.** There is none today
   (`fsync` appears only at `ds4_agent.c:7193`), so a power loss can leave a
   renamed file whose payload is zeroed/stale while magic, size, token count and
   tokenizer fingerprint all still pass — the only path found that can serve
   *wrong output* rather than a cache miss. It goes last so its cost lands
   outside the executor lock, not inside it.

Left undone on purpose: steps 4-7 are lock-ordering and durability surgery on a
production engine serving a live 512k session, arrived at via a research chain
that included fabricated evidence, and cannot be validated without a restart that
generates real traffic. Each needs sign-off. Before shipping, A/B with the hold
counters and re-confirm that the ~35 non-KV-adjacent dips do *not* move — that is
how we tell "fixed the KV cause" from "fixed nothing".

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
