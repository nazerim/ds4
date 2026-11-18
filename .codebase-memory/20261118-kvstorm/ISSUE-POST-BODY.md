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

## Reproduction

Verified on a clean `b3f5018` checkout built with `make`, `Qwen3.8-Flash-Next-Q4.gguf`, macOS/Metal, shell with
`DS4_API_KEY` unset. Two servers, **one flag apart**, driven by `curl`. Nothing is read from disk on the second
server: its log contains zero `kv cache hit` and zero `kv cache stored` lines, while the first logs 2 hits and 4
stores.

Prompt, deterministic and corpus-free (76,011 chars, sha256 prefix `9dc9f22c81921e73`, `prompt_tokens = 13858`):

```sh
python3 - <<'PY'
import json
SENT = ("The catchment's baseflow is sustained by fractured bedrock rather than soil moisture, "
        "which decouples stream response from rainfall intensity. ")
doc = "Archive 0. " + (SENT * (76000 // len(SENT) + 1))[:76000]
open("prompt.json", "w").write(json.dumps({
    "model": "default",
    "messages": [
        {"role": "system", "content": doc},
        {"role": "user", "content": "In one paragraph, what single factor governs the effect described above, and why?"}],
    "temperature": 0, "max_tokens": 256, "stream": False,
    "chat_template_kwargs": {"enable_thinking": False}}))
PY

# A: disk KV on
mkdir -p /tmp/kva
./ds4-server --model MODEL.gguf --ctx 65536 --port 8079 --kv-disk-dir /tmp/kva &

# B: identical, minus --kv-disk-dir
./ds4-server --model MODEL.gguf --ctx 65536 --port 8079 &
```

Then POST `prompt.json` to each. The difference is visible in the logs without comparing any prose:

```
A:  chat ctx=0..13858:13858 prompt start
    prefill chunk 8192/13858 (59.1%)
    prefill chunk 12288/13858 (88.7%)        <-- cold-checkpoint sync; exists only with the cache
    prefill chunk 13858/13858 (100.0%)
    kv cache stored tokens=12288 trimmed=1570 reason=cold key=token-text size=482.82 MiB

B:  chat ctx=0..13858:13858 prompt start
    prefill chunk 8192/13858 (59.1%)
    prefill chunk 13858/13858 (100.0%)
```

The completions:

```
A  609 chars  sha256 9ecec8d5303ae277d66e1707231ae51e813748c8bfba3d3b70fac3bf9c8397a4
   "The single governing factor is the **geological structure of the bedrock**, specifically its fractured
    nature. This is because the fractured bedrock acts as the primary reservoir and conduit ..."

B  754 chars  sha256 417a4130d5e43f1536abcc9a1201e4452ae637c7e4881450f4986b7d430e8faa
   "The single governing factor is the **geological structure of the catchment**, specifically the presence
    of **fractured bedrock**. This factor dictates the hydrological pathway because it shifts ..."
```

A second prompt (`"Archive 1. "`) behaves the same way: **A** 707 chars `1be64c1daffd...`, **B** 615 chars `225c29c7f71b...`.

**Determinism is asserted, not assumed.** Each config was requested twice and the two replies within a config
were byte-identical. That is what rules out run order and noise, so it is worth repeating if you try this.

One extra observation from those repeats: in server A the second request did not re-run the prefill - it logged
`kv cache hit text tokens=12288` then `chat ctx=12288..13858:1570 prompt start`, a real disk resume - and produced
a completion **identical to the cold one**. The checkpoint is therefore fine; resuming at the cold boundary is
faithful. What changes the answer is the chunk schedule, however you arrive at it.

Any prompt where the cold boundary lands strictly inside the prompt reproduces this; the defaults
(`cold_max_tokens` 30000, `min_tokens` 512) already bracket 13,858 tokens, so no cache tuning is involved.

## Measured

| comparison | my fork (same base commit) | upstream `b3f5018` |
|---|---|---|
| cold **disk ON** vs cold **disk OFF**, real prose ~12k tokens | **6 / 6 differ** | **4 / 6 differ** |
| cold disk ON vs resume at the cold boundary (`ctx=10240`) | 0 / 28 differ | 0 / 8 differ |
| cold disk ON vs resume at a mid-prefill checkpoint (`ctx=8192`) | 8 / 8 differ | 7 / 8 differ |
| resume@8192 vs cold disk OFF (same merged tail chunk) | 6 / 6 **identical** | not run |

The top row is the headline and needs no checkpoint read at all: same prompt, same binary, one flag different.
Across the ten divergent pairs, median character-level similarity is **0.30** (range 0.059-0.757) and the
first difference appears ~100-230 characters in: substantively different answers, not different wording.

The 6-vs-4 difference between the two builds on the top row is **not** a claim that the fork is
worse. At n=6 the
Fisher exact two-sided p is 0.455; what is claimed is only that both builds change answers when the boundary
moves (pooled 10/12).

Upstream ran the *same* prompts (identical token counts, identical chunk schedules, calibration reproduced to
six decimals) and two of the six came back byte-identical. A moved reduction order is a chance of flipping the
argmax, not a guarantee of it - which is what makes this an amplification effect rather than corruption, and is
why the `resume@8192 == cold-disk-off` cell is the load-bearing one for "the checkpoint is faithful".

## Not claimed

* **Not a state-corruption bug.** The mid-prefill `continued` checkpoint restores engine state faithfully —
  the `resume@8192 == cold-disk-off` result is what establishes that, and I checked the checkpoint inventory
  (per-layer GDN recurrent and conv state, attention K/V and index keys, PLE history, mRoPE delta, position
  rows) before concluding it.
* **macOS/Metal only.** Every number here was measured on that backend; no claim is made about CUDA, ROCm
  or Linux, and no measurement was attempted there.
* **No claim that either completion is worse.** Both are fluent; the complaint is that output is a function of
  cache configuration and cache history, not quality.
* **Sample size is small and the prompt range narrow.** Twelve realistic prompts in total across both builds,
  all ~12k tokens of English technical prose. The repetitive synthetic corpus was tested first and showed the
  same effect (6/6), so this is not a corpus artifact; but short prompts are unmeasured, where the tail chunk
  is large relative to the 8192 prefix and the rate may well be lower.

## Why it matters

`--kv-disk-dir` is meant to be a transparent latency optimisation, so any deployment that enables it is
serving the split-schedule answers, for every prompt where the cold boundary lands inside the prompt
(with the defaults, 512-30000 tokens). A/B measurements over the disk cache are
also not comparable across cache states, which is how I found this: two runs with identical binaries and
identical schedules agreed on only 247 of 440 replies, because eviction left them different checkpoints and
therefore different resume depths. The eviction score is dominated by a per-checkpoint fixed payload cost
rather than by usefulness (for zero-hit entries it reduces to "evict the fewest-token file"), so which
checkpoints survive, and therefore which chunk schedule a later request runs, is decided by size rather
than by reuse value.

## Possible directions to discuss

1. Write the cold checkpoint from an already-computed boundary instead of forcing a new one (avoid the extra
   sync), if the state at an existing chunk edge is sufficient.
2. Fix a canonical chunk schedule per absolute prompt position so that boundary insertion/removal does not
   change reduction order for the tokens after it.
3. Prefer checkpoints that lie on the cold schedule when several are valid, making resume shape-stable.
4. Or document that enabling the disk cache can change greedy outputs, so nobody is surprised by it.

I would not attempt (2) without your view on the cost.
