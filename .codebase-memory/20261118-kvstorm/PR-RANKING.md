# Adjudicating the four open evictor PRs offline

`evict_prs.py` replays upstream's scorer over a recorded store/lookup stream: **no model, no GPU, no prod window,
~1 s per policy.** Its reason for existing is that `antirez/ds4` has four open PRs changing the same score function,
none reviewed, and comparing them normally costs a build plus ~50 min of GPU plus a stopped production engine each.

**Validation first.** `upstream` reproduces the live arm exactly at both budgets (16 GiB 63.4 % / 2,459,409 tok /
636 evict / 34 files; 6 GiB 71.1 % / 3,390,964 / 903 / 11). The size model is fitted from each arm's own log
(`113.518 + 0.0300516*tokens`, max residual 0.0073 MiB), not hardcoded.

## The result that matters: the pathology is timescale-dependent

Dwell time = seconds between consecutive requests. A 50 min synthetic burst is ~3 s; a real cache sits for hours
(the fork's live cache has median file age **51.5 h**).

| policy | burst (~3 s) | 600 s | 1800 s | 3600 s |
|---|---|---|---|---|
| upstream b3f5018 | **63.4** | 52.6 | 54.8 | 57.7 |
| #986 age the `+1` | 63.4 | **23.6** | **16.8** | **15.9** |
| #711 marginal bytes | 52.6 | 39.8 | 35.2 | 33.5 |
| #814 anchor = COLD only | 36.1 | **20.2** | **13.1** | **11.9** |
| #1170 protect next load | 58.2 | 52.3 | 51.4 | 48.9 |
| this session's fresh-boost | **5.4** | 33.5 | 45.2 | 51.1 |

Cold-rebuild % of returning turns, 16 GiB, 430 requests. Best per column in bold.

- **#986 and #814 are the ones that work at realistic dwell**, taking 57.7 % -> 15.9 % and 11.9 %.
- **#986 looks completely inert in a burst** (63.4 -> 63.4). That is not a defect, it is arithmetic: it decays the `+1`
  on the existing 6 h half-life, and `2^(-3000/21600) = 0.91` over a 50 min run, so the term never moves. Judge it on
  dwell, not on a burst.
- **#1170 barely helps** (58.2 -> 48.9) and its deliberate budget overflow **never triggered** at 16 GiB or 6 GiB,
  because the protected entry is rarely the last candidate left.
- **The fresh-boost patch, which I presented as "the remedy", only works on bursts.** At hour-scale dwell it decays to
  300 s before the next request, so every entry looks stale, nobody is protected, and it converges on upstream (51.1 %).
  It is a burst-workload fix, not a general one.

## Combinations can be worse than either parent

At 6 GiB burst: `#986+#711` = **75.7 %**, worse than upstream's 71.1 %. At 16 GiB dwell 600 s, `#986`+`#814` = 10.2 %
but the triple `#986+#711+#814` = 8.0 % while `#711+#814` = 14.5 %. Stacking these changes is not monotonic and each
pairing needs its own measurement -- which is precisely what this harness makes cheap.

## Limits -- read before quoting

- **The dwell columns are UNVALIDATED.** Only the burst clock has live arms to check against. The stretched timeline is
  a uniform-gap caricature of a real cache, so treat those numbers as a hypothesis about PR ranking, not as measurement.
  A single live arm with realistic dwell (~50 min plus idle time, needs a window) is what would settle it.
- The replay cannot model reply-text divergence, tokeniser effects, concurrency > 1, vision/MTP, or the fork's delta
  chains. Entry identity is (conversation, token count), not prefix SHA, costing ~0.5 pt on divergent arms.
- The affine size fit is load-bearing: **zero the intercept and upstream moves 63.4 % -> 52.3 %; double it -> 67.9 %**
  (both re-measured here, not inherited). Re-fit per model/quant with `fit_size`, which reads any arm's own log, before
  trusting absolute percentages.
- #1170's protected entry is approximated as the longest-prefix entry of the conversation being stored; the real PR
  threads `protect_text` from the server.
- #986/#1170/#711/#814 are each simulated as a score-level change against `b3f5018`. All four bases are ancestors of
  `b3f5018` and their scorer is identical there (verified), but each PR also carries server-side plumbing this cannot see.

## Reproduce

```sh
cd .codebase-memory/20261118-kvstorm
python3 evict_prs.py runs/u16a/requests.jsonl 430 16384        # burst clock
python3 evict_prs.py runs/u16a/requests.jsonl 430 16384 1800    # 30 min dwell
python3 evict_policies.py                                      # random/LRU/MRU/size controls
```
