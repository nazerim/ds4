# KV eviction A/B under storm density -- results (2026-11-18 overnight)

> **CORRECTION (2026-11-18 ~11:00): the numbers below were computed with a flawed attribution and are SUPERSEDED.**
> They assigned log lines to requests by a +/-1 s time window; consecutive requests are often <2 s apart, so windows
> overlapped and lines were double-attributed (found by an independent review, then verified). Re-computed by
> finish-line segmentation (`kvparse.py`; see `runs/COMPARE-561.txt`): cold rebuilds are **fork 32.8 % vs upstream
> 71.1 %** (not 23.5 vs 76.5), rebuilt tokens 1.78 M vs 3.39 M (1.9x, not 2.4x; after a second review fixed live-reuse prefill counting, both totals match an independent derivation), **same-arm repeats identical**.
> Two claims below are RETRACTED: "the fork is ~25 % faster per rebuilt token" (prefill is 1311-1408 vs 1340 tok/s: no
> difference) and "79 first-turn disk hits unexplained" (true count is 0; it was the window artifact). The direction
> and the capacity/inconclusive-RFC reading stand; read the final section appended at the end of the follow-up run.

## FINAL (read this first; everything below the rule is the superseded first pass)

All numbers by finish-line segmentation (`kvparse.py`, reviewed twice; 0 attribution warnings in every arm).
Qwen3.8-Flash-Next-Q4, ctx 65536, M5 Max, sequential, temperature 0, thinking off, seeded schedule.
Raw: `runs/*/{requests.jsonl,server.log.gz,meta.txt}`; tables: `runs/COMPARE-561.txt` (6 GiB),
`runs/COMPARE-16G.txt`, `runs/COMPARE-FIX.txt`; re-derive with `python3 -I compare.py runs N arm...`.

| budget | arm | cold % (returning turns) | rebuilt tokens | mean s | files at end | evictions |
|---|---|---|---|---|---|---|
| 6 GiB (561 req) | fork F1 / F2 | 32.8 / 32.8 | 1.78 M | 3.5 / 3.4 | 29 | 832 |
| 6 GiB | upstream U1 / U2 | 71.1 / 71.1 | 3.39 M | 6.2 | 11 | 902 |
| 16 GiB (430 req) | fork f16a / f16b | 7.7 / 7.7 | 0.42 M | 1.6 / 1.7 | 76 | 453 |
| 16 GiB | upstream u16a / u16b | 63.4 / 63.4 | 2.46 M | 6.5 / 6.7 | 34 | 635 |
| 400 GiB (never evicts) | upstream u400 | **0.0** | 0.15 M | 1.2 | 447 | 0 |
| 16 GiB | upstream + fresh-entry boost `u16fix` | **5.1** | 0.31 M | 1.6 | 34 | 430 |

**Pre-registered reading, now resolved**
- Capacity is NOT the explanation (the earlier leading hypothesis is refuted): upstream goes 71 % -> 63 % cold with 2.7x the
  budget, and the fork at 6 GiB (29 files) beats upstream at 16 GiB (34 files).
- **Eviction is the whole explanation for upstream's misses**: with eviction removed (`u400`) upstream is 0 % cold. Its storage
  and lookup design is fine; the victims chosen are not.
- **Mechanism (measured + code):** upstream's score is `(effective_hits + 1) * tokens / file_size`
  (`ds4_kvstore_entry_eviction_score`) with a 6 h hit half-life and no recency term for never-hit entries. Entries hit even
  once keep ~2x a fresh entry's score for hours, so every new checkpoint is the lowest score and goes first. Measured
  (`lifetime.py`): 98-99 % of `disk-cache-full` victims had zero hits (u16a, u16b, u1), **median store->evict lifetime 5 s,
  75 % evicted within 10 s of being written**; the cache freezes on old content (70 disk-hit events in u16a).
- **Remedy demonstrated on this workload:** a 20-line experimental boost for never-hit entries (x9 at birth, 300 s half-life,
  `ds4-ab-evictfix`, constants untuned) takes upstream from 63.4 % to 5.1 % cold, rebuilt tokens -87 %, mean latency 6.5 s ->
  1.6 s. With it, zero-hit victims fall 98 % -> 37 %, median lifetime 5 s -> 54 s, disk hits 70 -> 273.
- The fork's retirement ladder is NOT what the data supports as the lesson: upstream shows no retirement class at all.
  The pre-registered criterion 2 (retirement-class clustering on upstream) is not met; a different, simpler mechanism is.
- Same-arm repeats were bit-identical in every pair run (F1/F2, U1/U2, f16a/b, u16a/b), so run noise is ~0 under this
  deterministic workload. `u400` and `u16fix` were single runs.

**Does NOT license**
- Any production claim. Prod runs the fork at 256 GiB (resident 255.95 GiB, 636 retained-log evictions, 98.6 % `conversation-retired`)
  with grace 3600; nothing here measures that regime. Upstream's default budget in real use may never enter this regime.
- "The fork's evictor is better": the fork differs from upstream in delta chains, anchors, small-dense and retirement; none isolated.
- The boost constants (8.0, 300 s) as a recommendation: they are guesses, tested at ONE budget on ONE synthetic workload
  (12 live conversations, Zipf 1.1, median revisit 7 steps, concurrency 1, text-only, one novel). No sweep, no real traffic.
- Anything about Linux/CUDA, vision, MTP, or concurrency 2.

**Open / unexplained**
- Replies diverge between arms with different cache behaviour: fork vs upstream agree 137-161/430-561, **and upstream vs itself
  (u16a vs u400) agrees only 247/430** while u400 vs u16fix (both mostly hits) agree 401/430. Same binary, same prompts, same
  temperature 0: output depends on whether prefix state was recomputed or loaded. Probably prefill-chunk/numerics, but
  UNVERIFIED and correctness-relevant. Prompt totals still agree within 0.002 %, so workload size is equal.
- With grace 3600 at 16 GiB (g16, 130 req): evictions were only `frontier-superseded` (34), 57 grace-pin lines, 119 files left:
  the budget is advisory while every entry is younger than the grace. Prod at 256 GiB is evicting, so this is a configuration trap.
- `prefill_t/s` includes cache-save stalls, so it slightly understates the fork; not used for any conclusion.

---

## SUPERSEDED FIRST PASS (kept so the reasoning trail is auditable; do not quote)

Protocol: `PROTOCOL.md` (written before results). Data: `runs/*/{requests.jsonl,server.log.gz,summary.txt,meta.txt}`,
comparison: `runs/COMPARE-561.txt` (re-derive with `python3 -I compare.py runs 561 fork-g0 f2 u1 u2`).
Machine: Apple M5 Max, Metal, Qwen3.8-Flash-Next-Q4, ctx 65536, `--kv-disk-space-mb 6144`, sequential, temperature 0, thinking off.
Order F1, U1, U2, F2 (A/B/B/A). Upstream arms hit their 60-minute cap at 561/562 of 700 requests, so every
comparison is on the **first 561 requests** (460 returning turns).

| arm | returning mean s | median | p90 | cold-rebuild % | rebuilt tokens | rebuilt % of prompt | cold tok/s | evictions | wall min |
|---|---|---|---|---|---|---|---|---|---|
| fork F1 | 3.55 | 1.08 | 9.80 | 23.5 | 1,513,597 | 31.3 | 1757 | 833 | 40.0 |
| fork F2 | 3.35 | 1.06 | 9.22 | 23.3 | 1,505,662 | 31.1 | 1840 | 833 | 37.7 |
| upstream U1 | 6.16 | 6.47 | 12.04 | 76.5 | 3,619,676 | 74.8 | 1449 | 903 | 60.0 |
| upstream U2 | 6.16 | 6.57 | 12.09 | 76.5 | 3,612,888 | 74.7 | 1446 | 903 | 60.0 |

**Same-arm spread is small** (cold% 0.2 pts fork, 0.0 pts upstream; rebuilt tokens 0.5 % fork, 0.2 % upstream), so the
between-tree gap (cold% 23 vs 76; rebuilt tokens 2.4x) is far outside run noise. Fork-vs-fork and upstream-vs-upstream
replies agree 561/561; fork-vs-upstream agree only 161/561, but returning-turn prompt tokens total 4,839,773 vs
4,839,615 (0.003 % apart), so the workloads are equal in size.

## (Superseded) Pre-registered verdict at 6 GiB: INCONCLUSIVE for the RFC premise
- Criterion 1 (upstream worse by more than same-arm spread): **met**, by a wide margin.
- Criterion 2 (cold rebuilds cluster after `conversation-retired`-class evictions in the upstream log): **not
  applicable and not shown** -- every one of upstream's 903 evictions is `disk-cache-full`; it has no retirement class.
  The RFC's mechanism ("cross-session retirement dominates") was therefore **not observed on upstream**.

## (Refuted) What the first pass thought the data supported
**Capacity at a deliberately tiny budget, not eviction ordering, is the leading explanation -- unproven.**
The fork stores delta chains (962 chained saves avg 0.162 GiB vs 395 full avg 0.332 GiB); upstream stores full
checkpoints (428 MiB avg for the `evict` reason). The 6 GiB budget held **29 files in the fork vs 11 in upstream**, and the
schedule keeps 12 conversations live, so upstream cannot hold one checkpoint per live conversation under *any* eviction
order. That is a statement about storage efficiency at 6 GiB, not about which victim is chosen.

The fork is also ~25 % faster per rebuilt token (1757-1840 vs ~1448 cold tok/s), which explains part of the latency gap
independently of cache behavior; token-denominated metrics (cold %, rebuilt tokens) are the speed-independent ones.

## Does NOT license
- Any claim about production (256 GiB budget, grace 3600, concurrency 2, real agent traffic). A first fork run with
  production's grace 3600 produced **0 evictions in 129 requests and a 35 GB directory against a 6 GiB budget** -- the
  grace window pins every candidate -- and was aborted; its data is not used, but it is a real finding that the budget
  is advisory under grace.
- Posting the ladder RFC, or an upstream PR claiming the fork's cache is "better": the comparison bundles fork-only
  features (delta chains, small-dense, anchors), so it is "fork as configured vs upstream", not an evictor swap.
- Anything about Linux/CUDA (nothing here ran there).
- The 79 first-turn disk hits in F1 are unexplained (probably a shared `Session kvstorm-...` token prefix); it is
  equal across arms by construction but unverified.

## (Done) To isolate the evictor -- ran as u400 and u16fix above
Raise the budget until BOTH trees can hold the working set (about 12 conversations x ~450 MiB = 5.4 GiB means 16 GiB
gives upstream ~2x headroom; the fork needs far less), rerun upstream and fork once each plus one repeat of the one that
looks surprising. If upstream's cold % falls to the fork's level, the 6 GiB result was capacity and the RFC premise is
dead on this workload. If it stays high, then ordering matters and the retirement analysis is worth doing.
Worktree `~/Projects/ds4-ab-upstream` (b3f5018, built) is kept for that run.
