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

Durations measured from the same log, matching the stall magnitudes:
`KV store save ms: n=792 p50=258 p90=500 max=1156` ·
`KV load ms: n=81 p50=804 p90=2470 max=4893`.

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

Stage the payload/trailer under `inference_mu`, then **release the lock before
`fwrite`/`fflush`**, and do the file read outside it, re-taking it only to swap
the session. The fork already has this exact precedent for `tool_mu`
(`.codebase-memory/adr.md:317`). Expected effect: removes the dominant cause of
the dips and shrinks holds for any future policy. Effort ~1–1.5 d.

Left undone on purpose: this is lock-ordering surgery on the **durability path**
(a race here silently loses or corrupts checkpoints), on a production engine
serving a live 512k session, arrived at via a research chain that included
fabricated evidence, and cannot be validated without a restart I will not do
unattended. It needs your sign-off. Two things I'd want before shipping it: run
it behind a flag and A/B with the new hold counters, and re-confirm on traffic
that the 34/101 non-KV-adjacent dips don't move (so we know what we fixed).

## 7. Open

- 35 of 101 dips unexplained. Candidates, **none yet verified**: MTP/DSpark
  verify stalls, generation rewind rebuilds (`~350 t/s` per the design agent's
  log read), the vision encoder if it runs under `inference_mu` (unconfirmed —
  the subagent's `11144` anchor is actually `server_inference_lock()`'s
  definition, so re-derive this one), and KV ops whose log line falls just
  outside the ±3/+12 s window.
- `DS4_VISION_KEEP_IMAGES=256` at 512k: `/Volumes/FireCuda520` 83% full (331 GiB
  free), KV dir at its 256 GiB budget.
- Upstream still frozen at `b3f5018` (Nov 3); no new server-PR movement.
