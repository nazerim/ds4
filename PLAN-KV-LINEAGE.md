# PLAN: KV Retention Lineage by Prefix-Chaining (Option B)

Status: PLANNED — not started. Written before /compact so context survives.
Repo state: branch `main`, clean tree at `9149525`.

## How we got here (completed work — all committed)

1. `99c497f` — cross-session poisoning fix: `conv_id` hash cap raised 512B → 131072B
   (`DS4_KVSTORE_CONV_ID_MAX_BYTES`, ds4_kvstore.h:13); `stale` layer fully
   decommissioned (`mark_stale_at_load` is an intentional no-op; Phase-A eviction is
   redundant-only; stale header byte kept inert for forward compat).
2. `9149525` — full-review hardening:
   - HIGH-1: store compat check now rejects/replaces files with a different
     `model_fp` (weight-swap zombies) — `ds4_kvstore_existing_compatible` exported.
   - MEDIUM-1: `ds4_engine_effective_prefill_chunk()` (ds4.c) feeds the KV anchor-grid
     alignment (explicit chunk → `DS4_METAL_PREFILL_CHUNK` env → Metal variant default
     4096/8192). Banner now shows `prefill_chunk=4096`, not 0.
   - MEDIUM-2: lookup skips `payload_bytes == 0` (agent /strip'd) files so they can't
     abort the load-fallback chain.
   - MEDIUM-3: tool-map trailer loader returns negative on corruption; load path logs a
     WARNING ("kv cache trailer corrupt, continuing without it").
   - LOWs: stored-size log +24B v2 header; orphaned `*.kv.tmp.*` reaped at open (1h age
     guard); hits/recency refreshed on store revalidation; race-free watermark view copy
     (`kv_cache_slot_continued_target`); watermark reset on tool-canonicalization
     rewrite/rebuild; stale-byte pass-through on hit touch; agent NULL-err guards;
     agent `/strip` preserves v2 headers; help docs for all retention knobs.

## Verified live behavior (log evidence)

- Cross-session switches hit real frontiers: `51474 consumed` (80-token prefill),
  `74775 consumed` (18-token prefill); shared-head hits (24576/16384) work across
  sessions/subagents. Anchor ladder lands on 8192 grid. No stale/redundant evictions
  at 32GB budget (no pressure).
- Subagent continued-store gap (49217→67320 with no 57344/65536 stores) is INTENTIONAL:
  decode loop suppresses continued stores inside tool calls (ds4_server.c:11534,
  `saw_tool_start || in_tool_call`) — mid-tool-call checkpoints would contain partial
  DSML the client never replays. Switch-time `evict` store catches the frontier.
- `cold_max` one-shot consumption (>30000 tokens unlinked after one load) is accepted
  design; continued 8192-grid anchors remain as fallback.

## Known non-bug: midnight date rollover

Session 1 missed deep on Aug 4 01:05 because OpenCode embeds `Today's date: ...` in an
`<env>` block inside the conversation history (byte 123800 ≈ 27k tokens; the
Scratch-workspace subagent system prompt). Old vs new 32768 checkpoints: identical
138056-byte texts, exactly 4 differing bytes (`Mon→Tue`, `03→04`). Cache behaved
correctly: hit deepest true prefix (24576), rebuilt ladder in one prefill. One-time cost
per affected session after rollover; inherent to byte-exact caching of volatile prompts.
Do NOT special-case dates (OpenCode-specific, mutates model input).

## The open problem: conv_id window is a cliff

`conv_id = sha1(text[0, min(len, 131072))) ^ model_fp-fold`. Requirements conflict:
- LOWER bound: cap must exceed the shared preamble or distinct sessions merge into one
  lineage → cross-session redundant-eviction returns (the original bug, worst with 3+
  merged lineages: parent + session + subagent — third frontier unprotected).
  Observed: session1↔session2 shared head 29270 tokens ≈ 123.5KB (cap clears by ~4KB!);
  subagent diverges at 23423 tokens ≈ 99KB (shared preamble across ALL lineages).
- UPPER bound: cap must stay below anchor text sizes we want ladder-pruned, or anchors
  become unclusterable singletons (kept forever until LRU-retire).

Any fixed cap has a cliff; growing AGENTS.md/MCP schemas march toward it silently.

Decision (user-confirmed direction): **Option B — lineage by prefix-chaining.**
Option A (bump cap to 256KB) deliberately SKIPPED — it only moves the cliff and would be
immediate churn once B lands. (A remains the 5-minute fallback if B balloons.)

## Option B design

Replace conv_id-based grouping in retention with actual byte-prefix lineage. Lineage ==
"one entry's text is a byte-prefix of the other's" — exactly the relation the load path
uses for reuse, so grouping can never disagree with loadability. No cap, no cliff,
model/client-agnostic.

### Mechanics

1. **Text cache**: files are content-addressed (`<sha>.kv`) and text is immutable
   (`ds4_kvstore_touch_file` only rewrites header bytes). At `kv_cache_refresh`, read
   each entry's text once per process, cached keyed by sha. Memory: worst case
   ~50MB (250 files × ~200KB text; payloads are the GB part). Steady-state refresh I/O
   stays header-only after first read.
2. **Chain membership**: entry X same lineage as Y ⟺
   `sha(X.text) == sha(Y.text[0..X.text_bytes])` (X prefix of Y) or symmetric.
   Cheap with cached text. Shared ancestors (head anchors) belong to EVERY chain that
   extends them — kept if ANY chain keeps them.
3. **Active chain** (evict-time exclusion): entries whose text is a byte-prefix of the
   incoming store text (ancestors of the frontier being stored; descendants can't exist
   yet). Replaces `active_conv` computation.
4. **Retain `conv_id` in the header format** (forward compat) but it no longer drives
   retention. `compute_conv_id` may stay for diagnostics/logging only.

### Code touched (all ds4_kvstore.c unless noted)

- `kv_cache_refresh` (~550): add sha-keyed text cache.
- `kv_cache_conv_is_kept` (~616): chain-based keep-set (frontier = longest chain member,
  tail = top-`tail_anchors`, sparse-middle window-largest per chain; small_dense and
  legacy kept as today).
- `kv_cache_find_redundant` (~700), `kv_cache_find_lru_conv` (~725),
  `kv_cache_conv_recency`, `kv_cache_conv_can_halve`, `kv_cache_retire_conv`,
  `kv_cache_count_distinct_convs`: conv_id comparisons → chain membership/recency.
- `ds4_kvstore_evict` (~860): Phase B/C active exclusion via active chain.
- Store path: level-inheritance scan (`conv_id ==` loop in
  `ds4_kvstore_store_live_prefix_text` ~1490) → chain membership.
- Tests (ds4_server.c test section): rewrite conv-stub retention fixtures to
  prefix-relation fixtures.

### Edge cases to handle/test

- Legacy v1 (`conv_id==0`) files: participate by prefix relation like everything else
  (their old behavior: always kept unless budget/LRU — keep equivalent semantics).
- Identical text ⇒ same sha ⇒ same file (no duplicate-chain hazard).
- Entries shorter than min_tokens / payload-less entries already skipped by lookup;
  retention still sees them (they occupy budget) — keep current treatment.
- Degenerate configs: tail_anchors=0, mid_spacing=0, anchor_step=0 (warnings exist).
- Date-rollover-style mid-history divergence ⇒ new branch; dead branch ages out as a
  unit (recency = max last_used of members) — same as observed today, cap-free.

### Test plan (add/rewrite in ds4_server.c)

1. Three lineages sharing a head, budget pressure → ALL THREE frontiers survive
   (the case that breaks under conv merging).
2. Shared-head ancestor kept while any descendant chain lives; pruned only when all
   chains that contain it drop it.
3. Diverged branch (prefix-shared, body differs) forms its own lineage: halving/retire
   target the idle branch, never the active chain.
4. Active chain exclusion: incoming store's ancestors never halved/retired.
5. Keep existing: keep-set geometry (frontier/tail/window), halving termination,
   min_anchors, max_conversations cap (now counting lineages), legacy-LRU.

### Verification & live stress plan

- `make ds4_test && ./ds4_test --server`, then full `make test` (ds4_agent_test,
  ds4-eval extractors, layer_pack 97, mgpu 98, gpu_args CLI 44).
- Live multi-session stress (after B lands): run ds4-server with a SMALL budget
  (`--kv-disk-space-mb 4096` or 8192) so halving/retire/redundant actually fire;
  exercise parent + session + subagent switches; expected log signature: no frontier
  lost on switch-back (deep `consumed`/hit, small prefills), `reason=redundant`
  pruning only middle anchors, whole-branch retirement of the idle lineage,
  `kv cache reaped orphaned temp` only for crash leftovers.

## Key facts for later

- Server binary must be rebuilt+restarted to pick up changes (one server process serves
  all sessions; log at `log/ds4.log`, rotates to `ds4.log.YYYYMMDD-HHMMSS`).
- KV dir `/tmp/ds4-kv`; budget default 4096MB (`--kv-disk-space-mb`), live tests used
  32768. `cold_max=30000`, `anchor_step=8192`, `small_dense=16384`, `tail=2`,
  `mid_spacing=131072`, `min_anchors=4`, `max_conversations=0`.
- Token→byte ratio ≈ 4.2 for these prompts. Shared head session1↔session2 = 29270
  tokens; subagent↔session2 = 23423 tokens.
- Priority (user): protect the CURRENT session's ladder first; other sessions age out
  gracefully. Budget pressure is the only trigger for eviction — no pressure, no
  eviction (which is why the stress test needs a shrunken budget).
- /tmp/ds4-kv does NOT need clearing across these changes (load is conv-agnostic;
  conv_id splits from earlier fixes are harmless).
