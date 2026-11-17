# KV eviction A/B under storm density -- protocol (written BEFORE results)

Written 2026-11-18 ~03:00, while arm 1 was running. Purpose: decide whether the ladder RFC's
motivating claim ("cross-session retirement dominates rebuild cost") reproduces against
**upstream's** score-based evictor, instead of only against the fork's lineage ranking. The
Aug-7 trace behind that claim is not retained, so this is a fresh synthetic measurement.

## Arms (all: Qwen3.8-Flash-Next-Q4, ctx 65536, own port 8003, own empty KV dir, no vision/MTP, `--kv-disk-space-mb 6144`)
| arm | tree | extra flags | order |
|---|---|---|---|
| `fork-g0` (F1) | fork `main` | `--kv-cache-small-dense 49152 --kv-cache-retire-grace-seconds 0 --kv-cache-max-divergence-anchors 8` | 1 |
| `u1` | upstream `b3f5018` | none (no fork flags exist) | 2 |
| `u2` | upstream `b3f5018` | none | 3 |
| `f2` | fork `main` | same as F1 | 4 |

Order is A/B/B/A so drift and the same-arm spread are measurable (`docs/PERFORMANCE.md`: one run
is not a result). Workload: `kvstorm.py`, seed 20261118, sequential, temperature 0, thinking off.
Compare arms on the **common prefix** of completed requests (arms may stop at different steps).

## Why grace is 0 (and what that means)
A first fork run with the production setting `--kv-cache-retire-grace-seconds 3600` produced
**0 evictions in 129 requests** and a 35 GB directory against a 6 GiB budget: the grace window pins
every candidate. That is a real finding about the fork (budget is advisory under grace) but it
makes the arm useless for an evictor comparison, so grace is 0 here. The aborted run's data is NOT
used. **No claim from this experiment applies to production's grace-3600 configuration.**

## Pre-registered reading of the result
Primary metric: median / p90 / mean latency of returning turns (turn > 1), and approximate rebuilt
prefill tokens, per `summarize.py`. Secondary: disk-hit vs cold-rebuild classification, evictions per
request, stores per request.

- **Premise supported** only if upstream's returning-turn rebuilt tokens or p90 latency exceed the
  fork's by **more than the larger same-arm spread** (u1 vs u2, f1 vs f2) *and* the cold-rebuild
  requests cluster after `conversation-retired`-class evictions in the upstream log.
- **Premise not supported** if the arms differ by less than the same-arm spread: then the
  retirement pathology is not demonstrated on upstream's evictor under this workload, and the RFC
  stays unposted.
- Anything between is "inconclusive", stated as such.
- Do not read a difference as the RFC's *cause* without checking `reply_sha` agreement between arms
  (temperature-0 divergence changes later prompts) and the empty/error counts.

## Known limits (state them when quoting anything)
- Synthetic traffic (one novel's text, 12 live conversations, Zipf 1.1 revisits, median revisit gap
  7 steps). Real agent traffic has tool calls, growing contexts to 100k+, and concurrency 2.
- Concurrency 1 and a single live-session slot: most requests are live misses by construction.
- 6 GiB budget is far below production's 256 GiB; it exists to force eviction, so absolute numbers
  say nothing about production hit rates.
- The fork arm exercises fork-only features (small-dense, divergence anchors) that upstream lacks;
  the comparison is "fork as configured vs upstream", not an isolated evictor swap.
- Log timestamps have 1 s resolution; per-request attribution uses a +/-1 s window and is approximate.
