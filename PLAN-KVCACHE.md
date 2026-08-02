# PLAN — KV cache management: keep multiple checkpoints, evict stale files

## 1. Problem restated

Current behavior (from live log + code):

- Checkpoints are written during prefill at fixed token boundaries
  (`20480, 40960, 61440, 81920, 102400, ...`, `reason=continued`), plus the full
  live checkpoint at a miss (`reason=evict`).
- Eviction score = `(hits+1) * tokens / file_size` → **keeps the largest, evicts
  the smallest**.
- `ds4_kvstore_find_text_prefix()` picks the **largest valid byte-prefix** checkpoint
  of the incoming prompt.

The failure: after any divergence (tool-call visible-text, or OpenCode compaction),
the *largest* checkpoints become stale (their text no longer prefixes the new
prompt), and the *smaller* ones that would still be valid prefixes were already
evicted to make room for the larger ones. So at a miss the disk holds only stale
large checkpoints → `find_text_prefix` finds nothing → **full prefill from 0
(120k tokens ≈ 6–7 min)**.

The 32GB budget is NOT the constraint — the policy discards the useful checkpoints.

## 2. Design goal & metric

**Goal:** bound the cost of a **tool-call divergence** rebuild to a small number of
tokens, regardless of where the divergence lands.

**Metric (tool-call divergence):** worst-case rebuild ≤ `KV_CACHE_ANCHOR_STEP` tokens.
With step = 8192 and prefill ~260–335 t/s, worst case ≈ 25–35 s. Target ≤ 60 s.

**Important: compaction is NOT bounded by this.** When the client rewrites the middle
of the conversation (OpenCode compaction), the rewritten region must be recomputed
regardless of cache. Rebuild ≈ `prompt_len − common` (irreducible), plus only the
anchor gap for the preserved head. Cache cannot make compaction fast; that is a
client-side behavior. See §4.

Budget math (from log: 102400 tokens ≈ 1367 MiB → ~13.3 KB/token of KV; the frontier of a
220k conversation alone is ~2.9 GB):
- step = 8192 → each anchor ≈ 109 MiB. A 128k conversation's **full** ladder ≈ 14.8 GB, a
  220k one ≈ 41 GB — **a full dense ladder does not fit** even for one long conversation.
  Halving is therefore mandatory, not optional.
- Capacity table — conversations fitting in 32 GB at each halving level `L`
  (L0 = full ladder, spacing `step×2^L`, bound `step×2^L` tokens):

  | conv len | L0 | L1 | L2 | L3 | L4 |
  | 16k   | 100 | 300 | 300 | 300 | 300 |
  | 32k   |  30 |  75 | 300 | 300 | 300 |
  | 64k   |   8 |  19 |  50 | 300 | 300 |
  | 128k  |   2 |   5 |  11 |  30 | 300 |
  | 220k  | 0.8 | 1.5 | 3.3 | 5.8 |  17 |

- Practical guidance: short conversations (≤32k) fit 30–300 at full ladder; ~64k hold ~8–19;
  only long (128k+) need halving to L2–L3 to hold ~10–30. The dominant cost is each
  conversation's **frontier**; keep the frontier always, halve the rest.

- **Verdict for the goal "3 × 150k + 1 × 220k" in 32 GB:** feasible with margin, **no budget
  increase needed**. Frontiers only = 8.6 GB. The cost is anchors, dominated by large ones:
  - Dense small ≤16k + tail=2 (frontier + 1 below) + middle every 128k → **~26 GB** (≈6 GB
    headroom).
  - Tail=3 (frontier + 2 below) pushes to ~34 GB — over budget; the 3rd tail anchor buys only
    ~8k extra divergence coverage for ~1.7 GB.
  - **Age-tiered (active full ladder, old downgraded):** old keep 3 largest + active full
    ladder = 25.9 GB (fits); old keep 2 largest = 20.8 GB; old frontier-only = 15.3 GB. For
    *cold* conversations frontier-only or frontier+1 is best (3 largest is still ~5.4 GB each).
  - "Keep N largest" across the board is both infeasible and wasteful (N=1 + halve-rest =
    52 GB; 3 largest ≈ 23.7 GB before the halved rest even counts).

## 3. Proposed design: keep all anchors, eject proven-stale at load

We can't know up front whether a divergence is compaction or tool-call, so we keep a
**spread of checkpoint sizes** and let the incoming prompt tell us which are stale.
The `byte_prefix_match` test at load already classifies the divergence implicitly:
compaction fails the high anchors, tool-call fails only the anchors past the tool-call
message. No heuristic guess needed.

### 3.1 Retention rule: dense small + dense tail + sparse middle

Observation from the 13:15 compaction log: the server stores checkpoints every
`~20480` tokens and, as each newer one lands, evicts the older ones of the **same
conversation** (`tokens=20480,40960,61440,81920,102400` all `reason=disk-cache-full`).
So it keeps only the **frontier**. That is right for compaction/growth (older anchors are
strict text-prefixes = redundant), but collapsing *every* older anchor is fatal for a
**tool-call divergence** (truncate near the frontier + diverge → the only anchor below the
point was evicted → full prefill).

**Cost structure (real numbers, ~13.3 KB/token KV):**
- Small anchors are cheap: 8k≈0.11 GB, 16k≈0.21 GB, 32k≈0.42 GB.
- Large anchors dominate: 128k≈1.7 GB, 144k≈1.9 GB, 216k≈2.8 GB. The **frontier** of a
  long conversation is the bulk of its cost.
- Keeping 3 near-full checkpoints of a 150k conversation (128k/136k/144k, only 8k apart) is
  **~3× the frontier** for ≤16k of extra divergence coverage — waste. Keep them sparse.

**Retention per conversation (non-uniform, age-tiered):**
- **Dense small:** keep ALL anchors ≤ `KV_CACHE_SMALL_DENSE` (e.g. 16k) — negligible cost,
  good cheap coverage.
- **Dense tail:** keep the frontier **plus one anchor just below it** (the `tail_k` largest,
  e.g. 2) — this bounds the common **tool-call divergence** (truncate near the end + diverge)
  to ≤ `step`, because the nearest retained tail anchor sits within `step` of the divergence.
- **Sparse middle:** between small_dense and the tail, keep every `KV_CACHE_MID_SPACING`
  (e.g. 128k). Deep divergences here are rare and mostly compaction (irreducible), so loose
  spacing is acceptable.
- **Age tier:** an **active** conversation keeps the full ladder above. An **old/LRU**
  conversation is downgraded to a few largest checkpoints only (frontier + `tail_k` below);
  it is cheap insurance if reactivated, so a 3rd tail anchor there is still mostly waste
  (≈5.4 GB per 150k conv for ~8k coverage vs 1.9 GB frontier-only). Prefer frontier-only or
  frontier+1 for cold conversations.
- Under budget pressure, first **halve the middle/large anchors** of the LRU conversation
  (increase `mid_spacing`, e.g. 128k→256k); retire a conversation when its ladder drops to ≤
  `KV_CACHE_MIN_ANCHORS`.
- Never evict a small anchor merely because a bigger one exists; never halve the **active**
  conversation's frontier/tail below the floor; never strip one conversation for another's
  budget.

### 3.2 Staleness handling (how we know what to drop)
Staleness is **detected at load time**, when we know the actual new prompt:

1. In `ds4_kvstore_try_load_text`, for each candidate examined (matching
   model/quant/ctx, `text_bytes <= prompt_bytes`), if it **fails**
   `byte_prefix_match(prompt_text, cached_text)` → it is stale **for this prompt**
   (its text includes content the client no longer sends). Mark `stale=true`.
2. Skip (don't test) candidates with `text_bytes > prompt_bytes` — they may match a
   future longer prompt; handle those via budget eviction.
3. On the selected hit, clear `stale` on it.
4. A stale checkpoint is evicted first on the next eviction pass (or immediately
   unlinked after the load). This is safe because OpenCode only moves forward: a
   checkpoint that fails to prefix a forward-moving prompt will never match a later one.

This is **agnostic to compaction vs tool-call** — the prefix test does the work.

### 3.3 Budget eviction (stale → halve LRU middle → retire conversation)
Only when over budget (or a stale pass didn't free enough), in order:
1. Evict stale-flagged entries (failed prefix match at load, same namespace).
2. If still over budget, take the **least-recently-active conversation** and **halve its
   middle/large anchors**: double `mid_spacing` (e.g. 128k→256k), keeping the dense small +
   dense tail (frontier + `tail_k`) intact. Repeat on the next-LRU conversation until under
   budget. Do not halve the active conversation's tail/frontier.
3. When a conversation's ladder is ≤ `KV_CACHE_MIN_ANCHORS` (2 or 4) checkpoints, **evict the
   whole conversation** (it is the LRU one being retired).
4. Never strip one conversation's frontier/ladder to satisfy another's budget — eviction is
   conversation-scoped, not global keep-largest.

## 3A. Multiple conversations & model routing

Two cases change the retention/staleness model from a single-conversation ladder to a
multi-namespace, multi-conversation one. The server supports `--batched-session`
(concurrent sessions) and `ds4_engine_routed_quant_bits()` can switch 2↔4 at runtime.

### 3A.1 Namespaces (model / quant / ctx)
Checkpoints are already keyed by `(model_id, quant_bits, ctx_size)`; `find_text_prefix`
filters on them. **Stale-detection and retention must be per-namespace**:
- Never mark a different-model/quant/ctx checkpoint stale for the current prompt.
- Never let retention evict all anchors of one namespace to satisfy another's budget.
- Each namespace keeps its own ladder + floor.

### 3A.2 Multiple concurrent conversations (batched_sessions)
The cache has no explicit conversation id today; conversations are separated only by
content (text-prefix). "One ladder per token bucket" is therefore wrong — it would keep
only one conversation's anchor per bucket. Fix:
- Add a **conversation/session key** to each entry, written on store (the server has a
  `server_slot` per session; use a stable id, or a content-derived fingerprint where the
  API carries none).
- **Note on stable ids:** OpenCode and the OpenAI Chat Completions API provide **no**
  stable conversation/session id — it is a stateless protocol; the client re-sends the full
  transcript each request, and there is no `conversation_id`/`thread_id` field. So a
  reliable conversation key cannot come from the request envelope. Use **prefix-clustering**:
  two prompts belong to the same conversation iff one is a text-prefix of the other (exactly
  what `ds4_session_common_prefix` measures). Assign each new checkpoint to an existing
  conversation lineage when it shares a long common prefix; otherwise start a new lineage.
- Retention groups by `(namespace, conversation_key)`: keep the **frontier + hedge**
  per **active** conversation, plus a `min_anchors` floor per active conversation.
  Redundancy collapse (§3.1) applies per conversation.
- Under budget, evict stale first, then redundant, then the **least-recently-active
  conversation's** anchors (LRU across conversations, oldest bucket first within a
  conversation). Do not strip one conversation's hedge/floor for another's budget.

### 3A.3 Conversation routed to a different model / quant
KV compatibility depends on **weights + tokenizer + quant + ctx**, NOT the protocol model
name or sampling settings (temperature/reasoning_effort never touch KV). The current cache
keys by `model_id`, which is a **compile-time constant** (`DS4_MODEL_VARIANT`) — it cannot
distinguish two weight builds (e.g. 0731 vs v2). Observed: 0731/v2 KV is byte-compatible and
works seamlessly; a prefill was only forced because the client started a new session/different
prompt, not because KV was incompatible.
- **Replace `model_id` keying with a weight/model fingerprint** (SHA of weights + tokenizer +
  arch) stored in the checkpoint header and the engine. Filter/select on that fingerprint.
- Same fingerprint, different name/temp → **reuse KV, no forced prefill**.
- Different fingerprint (real weight change) → reject, full prefill (correct and unavoidable).
- Keep quant/ctx in the namespace; default `reject_different_quant=true` (cross-quant KV is
  invalid).
- Do **not** mark old-namespace (old fingerprint) checkpoints stale for a new prompt — valid
  if routed back; evict only via budget/LRU.
- Scoped stale-marking (3A.1) makes this automatic: only same-namespace candidates are examined.

## 4. Behavior under each divergence class

- **Tool-call visible-text divergence** (`common ≈ live`, divergence at the end):
  all ladder anchors are valid prefixes (conversation unchanged up to the tool-call).
  Largest anchor below the tool-call message → rebuild ≤ one step. **Bounded & fast.**
  This is the common case (every tool call) and the primary target of this plan.
- **OpenCode compaction** (rewrites the middle, `common` far from live): the rewritten
  middle (tokens `common..prompt_len`) must be recomputed — irreducible. Anchors above
  the compaction point become stale → evicted; the largest valid head anchor below it
  saves only the preserved head. Rebuild ≈ `prompt_len − common` (e.g. 48k ≈ 2.5 min
  for the 77k/33k case) — a real but partial improvement. Not fixable server-side;
  requires OpenCode not compacting mid-tool-loop.
- **New session / different agent** (`common=1`): no checkpoint matches → full prefill
  (unavoidable; correctly detected).

## 4a. Concrete check — the 77k/33k case (live=78863, prompt=78888, common=30737)

Compaction rewrote tokens 30737..78888 (48,151 tokens). Dense 8k anchors: 32768+ are
stale (extend past 30737); largest valid = 24576. Rebuild = 78888−24576 = 54,312
(~3.3 min vs ~6–7 min from 0). Even an ideal anchor at 30737 still rebuilds 48,151
(~2.5 min). So compaction cost is irreducible; the ladder's bounded-step guarantee
applies only to tool-call divergence.

## 5. Config knobs

- `--kv-cache-anchor-step` (default 8192) — base anchor spacing (dense tail + small).
- `--kv-cache-small-dense` (default 16k) — keep ALL anchors up to this size (cheap dense small).
- `--kv-cache-tail-anchors` (default 2) — frontier + this many below kept dense (bounds the
  common tool-call divergence to ≤ step).
- `--kv-cache-mid-spacing` (default 128k) — large-middle anchor spacing; halve (double) on
  pressure.
- `--kv-cache-old-tier` (default 1) — checkpoints an **old/LRU** conversation keeps (frontier
  + this many below); active keeps the full ladder. 1 = frontier-only (best for cold).
- `--kv-cache-large-threshold` (default 128k) — conversations whose frontier exceeds this are
  treated as "large": they get the old-tier downgrade on aging instead of a full ladder.
- `--kv-cache-min-anchors` (default 4) — when a conversation's ladder is reduced to this many
  checkpoints, evict the whole conversation (2 or 4).
- `--kv-cache-max-conversations` (default derived from budget) — cap so budget is respected;
  over it, halve the LRU conversation's middle first, then retire conversations.
- Keep existing `min_tokens` (512) and `cold_max_tokens` (30000) semantics.
- Keep existing `min_tokens` (512) and `cold_max_tokens` (30000) semantics.

## 6. Implementation sketch (ds4_kvstore.c)

- Add `uint32_t bucket;` (from `tokens / step`), `uint8_t mid_level;` (halving level of the
  large-middle spacing), `uint64_t conv_id;` (conversation key), `uint64_t model_fp;`
  (weight fingerprint), `int64_t last_used;`, and `bool stale;` to `ds4_kvstore_entry`.
- **Stale detection at load:** in `ds4_kvstore_try_load_text`, after `find_text_prefix`,
  iterate the candidate entries **within the current namespace** (matching `model_fp`/quant/ctx,
  `text_bytes <= prompt_bytes`); any that fail `byte_prefix_match` get `stale=true`. Clear
  `stale` on the selected hit. Optionally unlink proven-stale entries immediately after load.
- **Retention:** in `ds4_kvstore_evict()`, remove pure keep-largest scoring. Group by
  `(namespace, conv_id)`. Per conversation keep: dense small (≤ `small_dense`), dense tail
  (frontier + `tail_anchors` below), sparse middle (every `mid_spacing`). Eviction on budget
  pressure: stale first; then double the LRU conversation's `mid_spacing` (halve its large
  middle); when a conversation's ladder ≤ `min_anchors`, retire the whole conversation. Never
  evict a small anchor merely because a bigger one exists, and never strip one conversation
  for another's budget.
- **Namespace guard:** key by `model_fp` (not the compile-time `model_id`), and default
  `reject_different_quant=true`; never mark a different-namespace entry stale.
- Keep `find_text_prefix` as-is (largest valid prefix in the current namespace).
- Store the current frontier checkpoint on miss exactly as today (`reason=evict`), tagged
  with its namespace + conv_id.

## 7. Testing (ds4_server.c unit tests)

1. **Retention of spread:** small anchors ≤ `small_dense` all kept; a small anchor is not
   dropped to make room for a bigger one.
2. **Tail density:** frontier + `tail_anchors` below are kept dense (bounds tool-call
   divergence to ≤ step); adding a 3rd tail anchor costs ~1.7 GB for ~8k coverage.
3. **Halving:** over budget, the LRU conversation's large-middle `mid_spacing` is doubled
   (large anchors thinned); the active conversation's tail/frontier is not halved.
4. **Age tier:** on aging, an LRU conversation downgrades from full ladder to
   `old_tier` largest (frontier + N below); a large conversation (frontier ≥ `large_threshold`)
   is downgraded instead of keeping a full ladder.
5. **Conversation retirement:** a conversation reduced to ≤ `min_anchors` checkpoints is
   evicted entirely.
6. **Stale detection at load:** tool-call divergence → only anchors past the tool-call
   message marked stale; largest valid tail anchor below it survives and is selected.
7. **Compaction scenario:** middle rewrite → high anchors marked stale and evicted; head
   anchor survives; selected anchor's rebuild is `prompt_len − anchor_tokens` (irreducible).
8. **Multiple conversations:** two conversations at the same token count keep separate
   frontiers+tails; eviction never removes all anchors of one conversation for the other's
   budget, and halving/retirement is conversation-scoped.
9. **Model/quant routing:** checkpoints with a different `model_fp`/`quant_bits` are never
   selected nor marked stale; a conversation full-prefills on a real weight change, and its
   old-namespace checkpoints survive for routing back. Same weights + different name/temp
   reuse KV (no forced prefill).
10. **Budget goal:** 3 × 150k + 1 × 220k fits under 32 GB (dense small ≤16k + tail=2 +
    mid every 128k ≈ 26 GB; age-tiered old=frontier-only ≈ 15 GB); assert total footprint ≤
    budget after eviction.

## 8. Risks / validation

- **Over-ejection:** a checkpoint that fails prefix match for one prompt is unlinked. Safe
  because OpenCode only moves forward; a non-prefix checkpoint can't match a later
  forward-moving prompt. Keep `KV_CACHE_MIN_ANCHORS` per conversation so we never empty one.
- **Sparse middle:** large anchors are kept every `mid_spacing` (128k), so a **deep** tool-call
  divergence far from the frontier can rebuild up to `mid_spacing` tokens. Acceptable because
  deep divergences are rare and mostly compaction (irreducible); the common tool-call case
  (near the frontier) is covered by the dense tail. Validate the 11:39 tool-call divergence
  keeps a tail anchor within `step` of the divergence point.
- **Conversation key stability:** OpenCode/OpenAI chat completions carry no id; use
  prefix-clustering (prompts of the same conversation are text-prefixes of each other) to
  assign a lineage key. Risk: unrelated conversations with a coincidentally long shared
  prefix could merge — mitigate by including `model_fp`/quant/ctx in the key and keeping
  content-SHA1 verification at load.
- **Cross-quant KV:** defaulting `reject_different_quant=true` is a correctness fix; confirm
  no existing path intentionally mixes quants.
- **Weight fingerprint:** adding `model_fp` requires hashing weights at load; keep it stable
  across the 0731/v2 byte-compatible builds so their KV reuses (do not hash the protocol
  name/temperature — those never affect KV).
- **Validation:** run the 11:39 tool-call divergence (120k), the 10:34 compaction (77k),
  and a batched-session + quant-route scenario; confirm rebuild bounds, stale eviction, and
  per-conversation retention.

## 9. Acceptance criteria

- A **tool-call divergence** at 120k context rebuilds in ≤ `step` tokens (not 120k): the
  dense tail (frontier + `tail_anchors`) keeps an anchor within `step` of the divergence.
- A **compaction** rebuilds `prompt_len − common` (irreducible middle), starting from the
  largest surviving head anchor — never worse than current.
- Each conversation keeps dense small + dense tail + sparse middle; a small anchor is not
  evicted merely because a bigger one exists, and eviction is conversation-scoped (halve the
  LRU conversation's middle, retire it at `min_anchors`).
- Stale checkpoints (failed prefix match at load, same namespace) are marked and evicted
  before healthy ones.
- Concurrent conversations keep separate frontiers+tails; eviction never strips one
  conversation to satisfy another.
- The goal "3 × 150k + 1 × 220k" fits in the 32 GB budget with ~6 GB headroom.
- Model/quant changes never reuse incompatible KV: cross-namespace (`model_fp`/quant/ctx)
  checkpoints are rejected, and old-namespace checkpoints survive for routing back. Same
  weights under a different protocol name/temperature reuse KV with no forced prefill.
- Eviction is O(entries), deterministic, and respects the budget.
