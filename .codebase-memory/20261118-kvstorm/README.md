# 20261118-kvstorm -- KV eviction under storm density

One question: does the disk-KV evictor choose badly, and can it be shown on upstream's own code?
Started as the evidence hunt for the held ladder RFC; ended with a different, measured mechanism.

**Read first:** `RESULTS.md` section FINAL. Its 'Does NOT license' list is part of the result.
The first pass at 6 GiB blamed capacity; it is marked superseded/refuted in place -- keep it for
the trail, quote nothing from below the rule.

| file | role |
|---|---|
| `kvstorm.py` | seeded, deterministic workload generator (dry-run: `--dry-schedule`) |
| `run-arm.sh` | one arm: isolated server (port 8003, own empty KV dir, no vision/MTP), run, stop, summarize |
| `chain*.sh` | arm chains; each ALWAYS restores production on exit (trap), nohup-safe |
| `kvparse.py` | log->request attribution by `finish=` segmentation (no time windows) |
| `compare.py`, `summarize.py`, `lifetime.py` | token-denominated tables; store->evict lifetime analysis |
| `PROTOCOL.md` | pre-registered reading rules, written before results |
| `evict-fresh-boost.patch` | the 20-line experimental fix (applies cleanly to `b3f5018`) |
| `runs/COMPARE-*.txt` | the three tables; re-derive with `python3 -I compare.py runs <N> <arms...>` |
| `runs/<arm>/` | requests.jsonl + server.log.gz + meta.txt per arm |
| `runs/chain*.log` | the operational record, including the killed-and-resumed chain |

**Arm names:** `fork-g0,f2,u1,u2` = 6 GiB budget (compare at 561 requests). `f16a,f16b,u16a,u16b` =
16 GiB; `u400` = upstream at 400 GiB (never evicts); `u16fix` = upstream + boost patch at 16 GiB;
`g16` = fork with production's grace-3600. All comparisons at 430 requests.

**Method debt this folder paid twice (do not repeat):** (1) attribution by +/-1 s windows
double-counted lines because sequential requests are <2 s apart; (2) live-reuse prefill was
counted as zero. Both were caught by review, not by me -- re-verify any script here against raw
logs before quoting it.

**Independent verification:** the FINAL section was re-derived from raw logs by an adversarial review that did not reuse the
scripts here; it reproduced every headline number and returned 4 corrections (lifetime match-order sensitivity, score-tie
tie-break direction, u400-as-ceiling wording, and the 300 s half-life sitting at this workload's p90 revisit gap of ~315 s).
All four are folded into `RESULTS.md`.

**Read `PRIOR-ART-AND-CORRECTIONS.md` before doing anything upstream.** It records that #986/#1170 already report and fix this
mechanism (so do not file), retracts the tie-break claim in RESULTS.md, and lists two errors in a review-drafted issue text.

Worktrees still holding builds: `~/Projects/ds4-ab-upstream` (b3f5018), `~/Projects/ds4-ab-evictfix`
(patched). Prod untouched throughout (cache 753 files; restore via `start-qwen` is in the chain trap).
