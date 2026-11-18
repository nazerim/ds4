# Does the disk-KV path change the model's answer?

Controlled measurements, 2026-11-18 afternoon. Question raised by the eviction A/B: arms `u16a` and
`u400` ran the **same binary and same schedule** yet agreed on only 247 of 440 replies, so cache state
appeared to influence output. That would be a correctness problem, not a performance one.

Harness: `cachediver_probe.py` + `run-cachediver.sh` / `run-crossproc.sh`. Same process, same binary,
temperature 0, thinking off; the only variable is whether a prompt's prefix is recomputed or restored.
Every run is gated on a **preflight that proves a disk load actually happened** — see "how this harness
failed twice" below.

## Verdict: the engine's own resume path is bit-faithful

Counted from the run logs, excluding the nine `compared: 1` preflight lines (they are gate checks, and
one of them is separately cited below as evidence).

| test | build | prompt tokens | comparisons | divergent |
|---|---|---|---|---|
| cold vs resume, 32-token gen | fork | 2.3k–9.0k | 12 | 0 |
| cold vs resume, ~17-token gen (ceiling never bound) | fork | 2.3k–10.4k | 16 | 0 |
| cold vs resume, median 297 / max 595 tok gen | fork | 2.3k–10.4k | 12 | 0 |
| cold vs resume (undersized run, CPT 3.4) | fork | 8.7k–14.3k | 9 | 0 |
| cold vs resume (undersized run, CPT 2.0) | fork | 3.8k–7.2k | 9 | 0 |
| cold vs resume, **but pass 1 was cached by the preflight** | fork | 11.6k–20.3k | 9 | **1** |
| cold vs resume, preflight isolated, max_tokens 512 | fork | 11.6k / 17.4k / 20.3k | 16 | 0 |
| live-session continue vs disk resume, 5 turns × 3 convs | fork | ~3.0k–3.4k | 15 | 0 |
| cold vs resume single-turn | upstream `b3f5018` | 2.3k–10.4k | 12 | 0 |
| live vs resume multi-turn | upstream `b3f5018` | ~3.0k–3.4k | 15 | 0 |
| within-round cold vs resume, each process | fork | 2.3k–10.4k | 24 | 0 |
| **two separate engine processes vs each other** | fork | 2.3k–10.4k | 12 | 0 |

**161 comparisons, 1 divergence** — and that single divergence is the checkpoint-depth case below, not a
cold-vs-resume failure. In the clean arms-scale test (16 prompts at 11,646–20,329 tokens, generations up
to 512 tokens, resume offsets `8192`/`10240`/`16384`/`18432`) the fork resumes up to 91 % of the prompt from
disk — `ctx=18432..20324` loads 18,432 of 20,324 tokens and re-prefills 1,892 — and reproduces the cold
answer exactly. So cold-rebuild vs checkpoint-resume does **not** change the answer on either build, and the
engine is deterministic across processes (recomputed from `runs/crossproc-round{1,2}.jsonl`, 12/12).

## The one real anomaly: checkpoint DEPTH changes the answer

Found in a run whose design was broken (see below) and therefore never reproduced on purpose. Same prompt
(11,650 tokens), same binary, same process — but the two requests resumed from **different checkpoint
depths**, `ctx=10240..11650` and `ctx=8192..11650`:

- identical for the first **298 characters**
- then part substantially — `difflib` ratio 0.633 over the whole reply; 172 vs 189 completion tokens
- the same engine's preflight had compared **cold vs resume@10240** on that same prompt at
  `max_tokens=32` and found them equal — so the weak link in `cold == deep == shallow` is the shallow
  resume, not the deep one. That preflight comparison is short (32 tokens), so it is a hint, not proof.

So: `cold == resume@10240`, but `resume@10240 != resume@8192`. The plausible reading is that resuming from
a shallower checkpoint re-prefills a different tail span, which reorders floating-point reductions
(upstream's own comment, `ds4.c:1052-1053`: *"changing chunk boundaries also changes floating-point reduction
order"*), and greedy decoding then compounds a near-tie flip. This is FP-level, not a corrupted context —
the model still conditions on the same tokens.

**Status: unexplained and unreproduced.** It arose because a preflight shared the engine and pre-cached
item 0, producing an unusual checkpoint state (the deeper entry apparently got retired, so the next lookup
fell back to 8192). No clean test yet forces two depths deliberately.

## Does this explain the arm divergence? Partially, and it is not settled

- Cascade arithmetic fits: the arms' 61 conversation-level first divergences, each propagating through the
  remaining turns of that conversation, can produce 193 of 440 mismatched replies from a low per-request
  rate. `u16a` usually lacked deep checkpoints (evicted) and so resumed at different depths than `u400`.
- But it is **not demonstrated**: 119 clean comparisons show 0 divergence whenever both sides use the
  engine's normal choice. A cause for the arms is still missing — either depth sensitivity at a rate my
  clean tests under-sampled, or something about heavy interleaving across 12 conversations that
  single-turn tests do not exercise.

**Decisive experiment (~1 window, ~10 min):** for a fixed ~11.6k prompt, deliberately produce one run that
resumes deep and one that resumes shallow — prime the conversation, remove the deepest `.kv` file from a
scratch KV dir, then repeat — and compare. Repeat 20× to get a rate. If shallow vs deep diverges at a few
percent, that plus the cascade explains the arms, and the residual risk to production is "answers depend
on which checkpoint survived", which is a reportable behaviour rather than a silent numeric curiosity.

## How this harness failed twice, and what each failure would have hidden

Both failures produced **100 % agreement**, which is the flattering result — the mechanism, not the summary
line, is what caught them.

1. **Prompts below the store step.** Sizes were computed from an assumed chars/token constant (3.4, then
   2.0). This corpus is repeated prose and compresses to **6.37** chars/token, so a "24,576-token" target
   produced 14,264 and the corrected run produced only 3.8k–7.2k. Every early run silently tested about a
   third of the intended size, i.e. nothing near the regime where the arms diverged. Fixed by `calibrate()`
   (one measured request sets the ratio) plus a hard warning when achieved size lands >25 % off target.
2. **Preflight contaminated the measurement.** The preflight shared one engine and one KV dir and reused
   corpus item 0, so the measured run's "cold" pass 1 was already cached. Both compared requests turned out
   to be resumes — which is how the depth anomaly surfaced at all. Fixed with a per-pass `--unique-prefix`
   tag; a checkpoint can no longer match a prompt it was not meant for.

Related, already retired elsewhere: two earlier "the cache path changes output" hypotheses were disproved
by measurement — the fork's drift-patch flags (`hc_stable`/`norm_unify`) are identical in the upstream arm
logs, and cross-process execution is deterministic.

## What this means for production

Nothing observed degrades correctness: the fork's normal cold and resume paths agree bit-for-bit at
production-scale prompt sizes. The open item is whether **checkpoint survival** — which the eviction
findings say is arbitrary, driven by a fixed 113.5 MiB payload cost rather than usefulness — can change an
answer by forcing a shallower resume. That connects the performance defect to a behavioural one, and is the
reason to run the depth experiment rather than close this out.
