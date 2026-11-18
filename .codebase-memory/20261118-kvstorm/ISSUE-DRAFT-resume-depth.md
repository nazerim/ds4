# Draft issue — NOT POSTED

Drafted 2026-11-18. Posting to `antirez/ds4` requires the owner's explicit go. Numbers are from this
repo's measurements; a maintainer reproducing them needs only the two flags shown.

---

## Title

Disk KV: resuming from a `continued` (mid-prefill) checkpoint produces a different answer than cold prefill

## Summary

For one prompt and one binary at `temperature 0`, resuming from a checkpoint stored mid-prefill
(`reason=continued`, written at the `--kv-cache-continued-interval-tokens` boundary) gives a
substantively different completion than a cold prefill of the same tokens. Resuming from the aligned
`reason=cold` checkpoint reproduces the cold completion exactly. So the same request can return different
answers depending on which of its own checkpoints is present on disk.

Reproduced on `b3f5018` and on a fork build carrying the same logic, so it does not appear to be a
fork regression.

## Reproduction

Engine: Qwen3.8 Flash Next Q4, `--ctx 65536`, Metal, macOS. Prompt ~11.7k tokens, `max_tokens 256`,
`temperature 0`, thinking disabled. Both checkpoint kinds must exist:

```
--kv-disk-dir <fresh dir> --kv-disk-space-mb 32768 --kv-cache-continued-interval-tokens 8192
```

1. Send the prompt. Log shows both stores during this cold prefill:
   `tokens=8192 trimmed=0 reason=continued size=359.73 MiB` and
   `tokens=10240 trimmed=1459 reason=cold size=421.28 MiB`.
2. Send an unrelated conversation (so the live session slot is released), then resend the prompt.
   It resumes `chat ctx=10240..11699` → completion is **byte-identical** to step 1.
3. Delete the `.kv` file that step 2 loaded (the log names it: `kv cache hit text tokens=10240 … file=…`),
   send the filler again, resend the prompt. It resumes `chat ctx=8192..11699` → completion **differs**.

Classification should come from the server's own `chat ctx=X..P prompt start` lines, and a resume that
lands on `ctx=0` is a cold rebuild, not a shallow resume, so it should not be counted.

## Measured

| comparison | fork | upstream `b3f5018` |
|---|---|---|
| cold vs `cold`-checkpoint resume | 0 / 20 differ | 0 / 8 differ |
| cold vs `continued`-checkpoint resume | **8 / 8 differ** | **7 / 8 differ** |

First difference appears a median 166–227 characters into the completion; median character-level
similarity between the two completions is ~0.25, i.e. different answers rather than different wording.
Raw data: `runs/resume-depth.jsonl` (includes full reply text), `runs/depth-server.log`.

## Not claimed

* **No mechanism is proposed.** The discriminator established here is only that a store taken mid-prefill
  and a store taken at the trimmed boundary do not restore equivalent state. Whether the cause is state
  that completes only at end-of-prefill, per-layer KV compression ratios disagreeing on an 8192 boundary,
  or something else was not investigated.
* No claim about safety-critical behaviour, and no claim about which of the two completions is "better":
  both are fluent. The complaint is nondeterminism with respect to cache contents, not quality.
* Not tested: vision sessions, MTP/speculative decoding, `batched-session`, concurrency > 1, non-Qwen
  models, contexts above ~20k tokens, and the `shutdown` store reason.

## Why it matters

Whether a `continued` checkpoint survives eviction is decided by the eviction score, which is dominated by
a per-checkpoint fixed payload cost rather than by usefulness. So for a long-lived session this is not an
exotic corner: the answer a client gets depends on cache history. It also means disk-KV A/B measurements
are not comparable across cache states, which is how this was first noticed.

## Possible mitigation to discuss

Refuse to *load* `continued` checkpoints as a resume source (keep writing them, but prefer `cold`/`evict`,
or mark them as requiring validation), or make the mid-prefill store capture the same state as the boundary
store. The first is cheap and strictly conservative; the second is the actual fix if the state is recoverable.

---

*Before posting, this text needs: (1) the mechanism question either answered or explicitly left open as
written; (2) confirmation the reproduction is minimal enough to run without this repo's harness;
(3) the owner's decision on posting.*
