# KV eviction A/B under storm density -- results (2026-11-18 overnight)

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

## Pre-registered verdict: INCONCLUSIVE for the RFC premise
- Criterion 1 (upstream worse by more than same-arm spread): **met**, by a wide margin.
- Criterion 2 (cold rebuilds cluster after `conversation-retired`-class evictions in the upstream log): **not
  applicable and not shown** -- every one of upstream's 903 evictions is `disk-cache-full`; it has no retirement class.
  The RFC's mechanism ("cross-session retirement dominates") was therefore **not observed on upstream**.

## What the data does support
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

## To isolate the evictor (next experiment, needs a stop window of ~2 h)
Raise the budget until BOTH trees can hold the working set (about 12 conversations x ~450 MiB = 5.4 GiB means 16 GiB
gives upstream ~2x headroom; the fork needs far less), rerun upstream and fork once each plus one repeat of the one that
looks surprising. If upstream's cold % falls to the fork's level, the 6 GiB result was capacity and the RFC premise is
dead on this workload. If it stays high, then ordering matters and the retirement analysis is worth doing.
Worktree `~/Projects/ds4-ab-upstream` (b3f5018, built) is kept for that run.
