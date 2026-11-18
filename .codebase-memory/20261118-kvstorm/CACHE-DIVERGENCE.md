# Does the disk-KV path change the model's answer?

> **Short answer: nothing is corrupting your checkpoints. But greedy completions are NOT invariant to prefill
> chunk schedule, and the KV cache's own bookkeeping decides that schedule — so the same prompt can return
> different answers depending on server config and on which checkpoints survived.** Established by the
> `disk ON` vs `disk OFF` test below: identical prompts, same binary, 6/6 differ, with no checkpoint involved
> on one side.
>
> The earlier framing of this finding ("a `continued` checkpoint restores bad state") is **refuted** by the
> `C == D` cell below — the checkpoint is faithful; it just sits on a different chunk boundary. See
> "MECHANISM ESTABLISHED". The "0 divergences" results further up are real but narrow: every resume in them
> landed on a cold chunk boundary, where the final chunk is unchanged by construction.

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
cold-vs-resume failure. Prompt sizes reach 20,329 tokens (the arms peaked at 20,502) with completions up to
512 tokens, and resume offsets were `10240`/`16384`/`18432`, e.g. `ctx=18432..20324` loads 91 % of the prompt
from disk and re-prefills 1,892 tokens. So cold-rebuild vs checkpoint-resume does **not** change the answer on
either build, and the engine is deterministic across processes (recomputed from `runs/crossproc-round{1,2}.jsonl`,
12/12).

> **CORRECTION, and it hollows out the result above.** An earlier version of this line claimed resume offsets
> `8192`/`10240`/`16384`/`18432`. `8192` never appears in the clean 16-prompt run — it came from a different,
> earlier log (`runs/cdiv-server-prev.log`). Checking the actual schedules (`runs/cdiv-server.log`) shows why
> that matters: cold prefill runs cumulative boundaries `8192 → 10240 → P`, `8192 → 16384 → P`, or
> `8192 → 16384 → 18432 → P`, and **all 17 resumes in that run landed exactly on one of those boundaries**
> (off-boundary: 0). A resume on the last cold boundary re-runs the identical final chunk, so bit-identity was
> structurally guaranteed: that test could not have failed. It is not evidence that resume is faithful in
> general, only that resume is faithful when it lands where cold already stopped — which is what the engine's
> own longest-prefix choice does whenever the deepest checkpoint still exists.


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

## REPRODUCED DELIBERATELY: mid-prefill (`continued`) checkpoints change the answer

`resume_depth_probe.py` + `run-resumedepth.sh` test this on purpose. Per prompt: **A** cold prefill,
**B** resume from the deep checkpoint, then **delete exactly the file the log named for B** and
**C** resume again from the next-shortest checkpoint. Every request is classified from the engine's own
`chat ctx=X..P prompt start` line, and a C that lands on `ctx=0` is scored uninformative rather than
counted as a shallow resume.

| build | A vs B — cold vs **deep** (`cold` store) | A vs C — cold vs **shallow** (`continued` store) | reply similarity B vs C |
|---|---|---|---|
| fork (production build) | **0 / 20 differ** | **8 / 8 differ** | median 0.243, min 0.095 |
| upstream `b3f5018` | **0 / 8 differ** | **7 / 8 differ** | median 0.250, min 0.091 |

Both builds resume from `ctx=8192` against an 11,699-token prompt, and pin
`--kv-cache-continued-interval-tokens 8192`. (The earlier fork 8/8 figure ran on the fork's default
interval, so it is not perfectly like-for-like with the upstream run; the effect is unchanged.)

**Which checkpoint is at fault is named by the log, not inferred.** During one cold prefill the server writes:

```
tokens=8192   trimmed=0    reason=continued   size=359.73 MiB   <-- resuming from this changes the answer
tokens=10240  trimmed=1459 reason=cold        size=421.28 MiB   <-- resuming from this matches cold exactly
```

Both are valid text prefixes of the same prompt (SHA-matched, `key=token-text`), both load in ~20-30 ms,
and both are considered usable by the lookup. But only the `cold` one reproduces full prefill. The
divergence appears early — median first difference 166-227 characters in — and the replies are not
paraphrases of each other: median character-level similarity ~0.25, i.e. a substantively different answer.

### Why this is the significant result of the whole session

Everything else measured here was benign: the cache does not change answers when the engine picks its own
checkpoint, and the process is deterministic. This says the engine will return a **different answer for the
same prompt depending on which checkpoint survived**, and the eviction findings say survival is decided by
a fixed 113.5 MiB payload cost rather than by usefulness. Performance and correctness are therefore the
same defect chain, not two separate issues.

It also explains the observation that started this: `u16a` and `u400` shared a binary and a schedule yet
agreed on only 247 of 440 replies — they had different checkpoints available, hence different resume depths.

**Mechanism: NOT established.** The discriminator is that the `continued` store happens mid-prefill, at a
chunk boundary, while the `cold` store is written at the trimmed boundary. A candidate is state that is only
complete once the prefill finishes, or per-layer compression ratios (`compress_ratios` has 0-ratio layers
and 4:1 layers) that do not agree on an 8192 boundary. Both builds show it, so it is not fork-specific.
Naming the cause needs a code-level read of what the continued payload captures versus the cold payload.

### MECHANISM ESTABLISHED: completions are not invariant to prefill chunk schedule

`chunkshape_probe.py` + `run-chunkshape.sh` settled H1 vs H2 without touching the engine's source. The lever:
the mid-prefill cold-store sync is gated on `s->kv.enabled` (`ds4_server.c:16207-16274`), which is set only when
`--kv-disk-dir` is passed (`ds4_server.c:18931`, `ds4_kvstore.c:2419`). So **removing that one flag removes the
chunk split and nothing else** — cold prefill then runs the shallow resume's shape with no checkpoint involved
at all. Byte-identical prompts across the two phases come from a shared `build_prompt()` and a pinned
`--cpt-file`, and the runner refuses any prompt whose token count differs between phases.

Same 11,697-token prompt, same fork binary, temperature 0, 6 prompts:

| schedule | route | result |
|---|---|---|
| `8192 + 2048 + 1457` | **A** cold, disk ON | baseline |
| `1457` | **B** resume@10240 | == A (matches cold exactly) |
| `3505` | **C** resume@8192 (`continued` checkpoint) | **A vs C: 6 / 6 differ** (median similarity 0.156, first difference ~100 chars) |
| `8192 + 3505` | **D** cold, **disk OFF** — no checkpoint anywhere | **A vs D: 6 / 6 differ**, **C vs D: 6 / 6 identical** |

`C == D` is the decisive cell. Two completely different routes — restoring a mid-prefill checkpoint versus a
plain cold prefill on a server with no disk cache — reach the same chunk schedule and produce the **same
completion every time**. Therefore:

- **H1 is refuted for this repro.** The `continued` checkpoint is a faithful snapshot of engine state at 8192;
  it is not losing recurrent/conv/position state. The static audit agreed (it found every buffer present).
- **H2 is confirmed.** The output depends on the **prefill chunk schedule**, exactly as upstream's own comment
  says it does (`ds4.c:1059`: *"changing chunk boundaries also changes floating-point reduction order"*).
- **The disk cache changes cold answers.** A vs D is not about resume at all: the same request to the same
  binary differs 6/6 depending on whether `--kv-disk-dir` was passed, because storing the checkpoint forces an
  extra sync boundary at `cold_store_len`.

So the honest title for the finding is not "a checkpoint is corrupt" but **"greedy completions are not
invariant to prefill chunking, and the KV cache's own bookkeeping picks the chunking"** — for a client that
means the answer depends on server config and on which checkpoints survived.

**Scope limit that matters:** every comparison here uses a deliberately repetitive synthetic corpus, which is
where near-ties are most likely to be amplified. The rate on realistic prose is **unmeasured**, so the correct
claim is "reproducibly sensitive", not "unreliable in production".

### Status versus upstream

Searched `antirez/ds4` (read-only) for "continued checkpoint", "mid-prefill checkpoint", "cache hit wrong
output", "checkpoint different result", "resumed session differs". Nothing addresses output changing between
checkpoint kinds — the closest, #1266, #1186 and closed #174, are all about continued checkpoints being
*evicted*. So the correctness aspect appears unreported, though I did not read every issue in the repo.

**Not posted. Drafting and posting need the owner's go.** See `ISSUE-DRAFT-resume-depth.md`.

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
