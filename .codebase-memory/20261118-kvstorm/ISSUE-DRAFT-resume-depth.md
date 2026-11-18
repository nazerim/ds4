# Draft issue — NOT POSTED

Drafted 2026-11-18, **substantially rewritten the same night after the mechanism test refuted my first
framing.** Posting to `antirez/ds4` requires the owner's explicit go.

Two framings were considered and rejected: (a) "a `continued` checkpoint restores bad state" — refuted by my
own data, and posting it would have been wrong; (b) "FP nondeterminism" as a standalone complaint — upstream
already documents that chunk boundaries change reduction order, so that framing invites "working as intended".
What survives is the part that is *not* documented anywhere: **enabling the disk cache changes completions.**

---

## Title

Greedy completions change when `--kv-disk-dir` is enabled, because storing the cold checkpoint inserts an extra prefill boundary

## Summary

For the same prompt, same binary, same build, `temperature 0`: requests to a server started with
`--kv-disk-dir` produce a different completion than requests to the same server started without it. Nothing is
being loaded from disk in the comparison — one side never touches the cache. The cause is that the cold
checkpoint write path syncs the session to `cold_store_len` *mid-prefill* so the checkpoint can be written,
which inserts an extra prefill chunk boundary; chunk boundaries change floating-point reduction order
(documented in `ds4.c`), and greedy decoding then compounds the flip.

The same mechanism makes completions depend on **which checkpoint survived eviction**: resuming from a
checkpoint that sits on the cold schedule's own boundary reproduces the cold answer exactly, while resuming
from a shallower mid-prefill (`reason=continued`) checkpoint merges the last two chunks and changes the answer.

## Reproduction (no harness needed beyond an HTTP client)

Model: Qwen3.8 Flash Next Q4, Metal, macOS, `--ctx 65536`.

1. Start the server **without** `--kv-disk-dir`. POST an ~11.7k-token prompt at `temperature 0`,
   `max_tokens 256`, thinking disabled. Note the completion. Log shows prefill chunks `8192 + 3505`.
2. Restart the same binary **with** `--kv-disk-dir <fresh dir> --kv-cache-continued-interval-tokens 8192`.
   POST the identical prompt. Log now shows prefill chunks `8192 + 2048 + 1457`. **Completion differs.**

Then the cache-history half:

3. With the cache enabled, let the prompt be served again after the live slot is released: it resumes
   `ctx=10240..11697` (chunk `1457`) → **byte-identical** to step 2.
4. Delete only the `.kv` file step 3 loaded (the log names it), release the live slot, resend: it falls back to
   the mid-prefill checkpoint, `ctx=8192..11697` (chunk `3505`) → **differs**, and matches step 1's output.

Step 4 matching step 1 is the point: two routes with no shared state, one via disk and one with the cache
switched off, agree with each other and disagree with step 2 whenever the chunk schedule agrees. Output
follows the schedule, not the route.

## Measured

| comparison | differ | note |
|---|---|---|
| cold **disk ON** vs cold **disk OFF** (same prompt, same binary) | 6 / 6 | no checkpoint read on either side |
| cold disk ON vs resume at the cold boundary (`ctx=10240`) | 0 / 28 | also 0/16 at 11.6k–20.3k tokens, 512-token generations |
| cold disk ON vs resume at a mid-prefill checkpoint (`ctx=8192`) | 15 / 16 | fork 8/8, upstream `b3f5018` 7/8 |
| resume@8192 vs cold disk OFF (same 3505 tail) | 0 / 6 identical | the checkpoint is a faithful snapshot |

Median character-level similarity of "differing" completions ~0.16–0.25: substantively different answers, not
different wording. First difference typically ~100–230 characters in.

## Not claimed

* **Not a state-corruption bug.** The mid-prefill `continued` checkpoint restores engine state faithfully —
  the `resume@8192 == cold-disk-off` result is what establishes that, and I checked the checkpoint inventory
  (per-layer GDN recurrent and conv state, attention K/V and index keys, PLE history, mRoPE delta, position
  rows) before concluding it.
* **No claim that either completion is worse.** Both are fluent; the complaint is that output is a function of
  cache configuration and cache history, not quality.
* **Rate on realistic prompts is not measured.** All figures above use a deliberately repetitive synthetic
  corpus, which is the most favourable case for amplifying a near-tie. Treat this as "reproducibly sensitive",
  not as "unreliable under normal traffic".
* Not tested: vision sessions, speculative decoding/MTP, `--batched-session`, concurrency > 1, non-Qwen
  models, prompts beyond ~20k tokens, and the `shutdown`/`evict` store reasons.
* Nothing about CUDA or Linux: measured only on macOS/Metal.

## Why it matters

`--kv-disk-dir` is meant to be a transparent latency optimisation. A/B measurements over the disk cache are
also not comparable across cache states, which is how I found this: two runs with identical binaries and
identical schedules agreed on only 247 of 440 replies, because eviction left them different checkpoints and
therefore different resume depths. The eviction score is dominated by a per-checkpoint fixed payload cost
rather than by usefulness, so the surviving set — and hence the chunk schedule, and hence the answer — is
close to arbitrary under pressure.

## Possible directions to discuss

1. Write the cold checkpoint from an already-computed boundary instead of forcing a new one (avoid the extra
   sync), if the state at an existing chunk edge is sufficient.
2. Fix a canonical chunk schedule per absolute prompt position so that boundary insertion/removal does not
   change reduction order for the tokens after it.
3. Prefer checkpoints that lie on the cold schedule when several are valid, making resume shape-stable.
4. Or document that enabling the disk cache can change greedy outputs, so nobody is surprised by it.

I would not attempt (2) without your view on the cost.

---

*Before posting, still wants: (1) one run on a realistic (non-repetitive) long prompt, to bound how often this
bites in normal use — the current numbers all come from repetitive synthetic prose; (2) confirmation the
repro works from a plain `git clone` + `make` without my scripts; (3) the owner's decision.*
