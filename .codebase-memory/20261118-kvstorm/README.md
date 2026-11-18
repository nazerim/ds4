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
| `evict_replay.py`, `evict_policies.py` | **Offline replay of the recorded store/lookup stream: no model, no GPU, <1 s per policy.** Validated to the digit on 3 live arms; read its limits block first |
| `check_marker_specials.py` | Reads a GGUF's `token_type`/vocab to say whether chat markers are registered special tokens |
| **`PR-RANKING.md`** | **The deliverable: ranks the four open upstream evictor PRs in ~1 s each, and shows the pathology is timescale-dependent. Read its limits section first -- the dwell numbers are unvalidated** |
| `cachediver_probe.py` + `run-cachediver.sh` | Cold-vs-disk-hit answer comparison on a live engine (needs a window; runner preflights that a disk hit really happened, then restores prod) |
| `evict_prs.py` | The four PRs as selectable policies (+ combinations), with per-model size refit and a dwell-time knob |
| **`REPRO.md` / `ISSUE-POST-BODY.md` / `repro_curl_only.sh`** | **The publishable repro: two servers one flag apart, curl only, with the exact chunk-schedule log lines and a completion pair pinned by sha256. ISSUE-POST-BODY.md is what would go public (8.4 KB); the draft file keeps the drafting notes** |
| **`chunkshape_probe.py` + `run-chunkshape.sh` / `run-shapereal.sh`** | **The decisive harness: same binary, same prompts, engine started with vs without `--kv-disk-dir` (real prose optional via `--corpus-file`, `MODE=upstream` for the b3f5018 build). No checkpoint read on one side, so it isolates chunk schedule from cache state** |
| **`resume_depth_probe.py` + `run-resumedepth.sh`** | Forces deep vs shallow resume by deleting the checkpoint the last load named; classifies every request from the log |
| **`ISSUE-DRAFT-resume-depth.md`** | **DRAFT, NOT POSTED** -- the `continued`-checkpoint correctness finding, with repro steps and explicit non-claims. Owner's go required |
| **`CACHE-DIVERGENCE.md`** | **Does the disk-KV path change the answer? cold==resume when the engine picks, and deterministic across processes -- BUT resuming from a mid-prefill `continued` checkpoint changes the answer ~90% of the time, on the fork AND upstream. That is the finding. Read its two harness-failure notes before trusting any "0 divergence" line here** |
| `runs/COMPARE-*.txt` | the three tables; re-derive with `python3 -I compare.py runs <N> <arms...>` |
| `runs/<arm>/` | requests.jsonl + server.log.gz + meta.txt per arm |
| `runs/chain*.log` | the operational record, including the killed-and-resumed chain |

**Arm names:** `fork-g0,f2,u1,u2` = 6 GiB budget (compare at 561 requests). `f16a,f16b,u16a,u16b` =
16 GiB; `u400` = upstream at 400 GiB (never evicts); `u16fix` = upstream + boost patch at 16 GiB;
`g16` = fork with production's grace-3600. All comparisons at 430 requests.

**Method debt this folder paid three times.** (1) Attribution by +/-1 s windows double-counted lines because sequential
requests are <2 s apart. (2) Live-reuse prefill was counted as zero. (3) Two cache-divergence runs were **silent no-ops that
reported success**: first the prompts were under the 2048-token store step so nothing was ever cached (cold compared against
cold, 100% agreement in 34 s); then `max_tokens=256` never bound because the question asked for one sentence, so replies stopped
at ~17 tokens and the long-generation case went untested. Every one was caught by an independent counter -- server-side `kv cache
hit` lines, `finish=stop` tallies, reply character lengths -- never by the pass/fail summary. **The lesson: an experiment that
finishes fast and reports perfect agreement is a prompt to verify the mechanism fired, not to celebrate.** Harnesses in this
folder now assert their own precondition (see the preflight gate in `run-cachediver.sh`).

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
