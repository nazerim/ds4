# ADR: KV Cache v2 — retire-grace, divergence anchors, 128 GiB budget

## Status
Accepted (implemented, PLAN-KV-REWRITE.md; committed with phases 1-5 + regression).

## Context
A 743-request trace (Aug 7) showed the dominant KV-cache rebuild cost was cross-session retirement, not the anchor-hole: at session switches, a just-live conversation's whole ladder (incl. the frontier persisted seconds earlier) was `conversation-retired` because LRU recency ranked by file touches (max last_used among exclusive members) rather than slot activity. The 23420-token divergence class (subagent tool-list re-render) also rebuilt from 16384/24576/32768 because no anchor sat between the deepest surviving head anchor and the divergence point. Disk sat at 63/64 GiB so every store ran an eviction pass.

## Decision
1. **Retire-grace (frontier pinning):** `--kv-cache-retire-grace-seconds` (default 3600). A lineage whose leaf was touched within grace is exempt from PHASE C retirement and over-cap retirement; PHASE B halving is grace-agnostic (non-destructive). Stops switch-back deep rebuilds.
2. **Divergence anchors:** on a miss loading anchor A < common, set a per-slot pending target; once the live session reaches it, store a reason=cold anchor at exactly common (grid-aligned targets skipped — covered by continued store). `--kv-cache-max-divergence-anchors` (default 8, 0=off).
3. **Budget:** KV_SIZE default 65536 -> 131072 MiB so two ultra-long conversations coexist.

## Consequences
- Session switch-back should hit the prior frontier (<100-token prefill, 16:54-style 287166-token hits).
- Genuinely idle lineages (>grace) are still retired under pressure; halving preferred over retiring.
- Divergence-class misses rebuild from `common` instead of the older anchor.
- New knobs are config-overridable; tests: 3 new unit tests in `--server`, `tests/kv_policy_harness` in `make test`.
- Live model-backed verification remains a follow-up server run (phase 6).

## Supersedes
- The LRU-retire ranking in PLAN-KV-LINEAGE (unchanged except grace).
- The 65536 MiB budget default from the Aug 6 fix.

---

# ADR: KV Cache — double continued-store grid above 48k tokens

## Status
Accepted (implemented in ds4_kvstore.c / ds4_server.c).

## Context
Continued anchors write every `anchor_step` (default 8192) tokens at all
lengths. At long context each anchor payload is large, so the 8192 grid
produces heavy disk write traffic while the re-prefill an extra anchor saves
shrinks in relative value.

## Decision
Above `DS4_KVSTORE_WIDE_STEP_ABOVE_TOKENS` (49152 = 48k) live tokens the
effective continued-store step doubles (default 8192 -> 16384), via
`kv_cache_step_for()` used by `ds4_kvstore_continued_store_target` and both
divergence-grid dedup sites (kvstore and server). New public API
`ds4_kvstore_continued_step_at(kc, live_tokens)`.

Grid properties: doubling keeps the wide grid a subset of the base grid, and
49152 is a multiple of both, so the crossing fires exactly once and sessions
restored from legacy odd-8192 anchors resume cleanly at the next 16384
multiple. Divergence targets at odd 8192 multiples above 48k are no longer
"on the grid" and correctly keep their own anchor.

## Consequences
- Half the anchor writes above 48k; worst-case re-prefill after a miss there
  rises from 8191 to 16383 tokens.
- Retention ladder above 48k thins accordingly (fewer files per lineage).
- `--kv-cache-anchor-step` semantics unchanged below 48k; above, the
  user-set step doubles too (grid nesting preserved).
- Regression coverage: `test_kv_cache_continued_uses_aligned_frontiers`
  (crossing, odd-multiple suppression, legacy-anchor resume).

# ADR: Thinking-replay bridge for tool conversations (root cause of the ~30k live-KV drift)

## Status
Accepted (403914c, 0950ada; live-verified against pi.dev sessions Sep 3).

## Context
After the Vision-Exp switch, long tool conversations produced repeated
"live kv cache miss ... reason=token-mismatch" with the common prefix stuck
at the first non-canonical assistant turn (~30k) and drifting right each
turn, forcing multi-minute cold re-prefills at 100-280k context. Trace
diagnostics (TRACE_PATH, first-mismatch token window) pinned it: tool
conversations render replayed assistant turns as think-open + reasoning +
think-close + content, but plain (no tool-call) thinking turns were excluded
from the thinking-live checkpoint bridge by the has_tools gate. The live
graph therefore held sampled reasoning while every faithful replay rendered
a different token stream, and BPE re-merges across block boundaries mean
even identical bytes can tokenize differently (sampled autoregressive vs
whole-string re-tokenize).

## Decision
1. should_remember_thinking_checkpoint() accepts the reasoning text; tool-
   context / prompt_preserves-reasoning turns remember a PRESERVED-visible
   key: prompt + sampled reasoning + think-close + content + eos, built by
   build_preserved_thinking_visible_text(). The next replay hits the
   thinking-visible path and rebuilds the effective prompt from the exact
   live token prefix + re-tokenized suffix, which is immune to boundary
   re-merge drift. finish=length turns remain excluded by design.
2. Image markers ("\x1e DS4_IMAGE_<24 hex nonce> \x1f") are fixed-length,
   request-random sentinels: visible-prefix matching now treats the nonce at
   equal offsets as a wildcard (byte_prefix_match_visible) and refuses the
   bridge when the unmatched suffix contains markers. Without this the
   thinking bridge was silently dead for every image conversation.
3. KV model_fp now folds the vision-encoder file fingerprint
   (path+size+mtime) so encoder-only model swaps invalidate disk snapshots.
   fp = fingerprint(llm) ^ (fingerprint(encoder) * GOLDEN_RATIO_MIX).
4. Vision encoder results are cached by 128-bit hash of the encoded bytes
   (LRU, 64 slots, 512 MiB): requests previously re-ran the ViT for every
   history image on every turn even when live KV hit.

## Consequences
- Production drift misses eliminated: tool sessions show cached = live
  frontier - O(1) token continuations; verified on real 100k-140k sessions.
- The bridge only ever fires on byte-verified replays; mismatches degrade
  to the pre-existing paths, never to wrong KV.
- Truncated (length) turns still cold-rebuild once: their replay cannot be
  attested (unclosed think), and Metal cannot rewind the compressor
  frontier mid-stream (see rewind note below).
- Tests: preserved-canonical-matches-future-prompt, marker wildcard,
  vembed cache, gate matrix.

# ADR: Multimodal disk KV snapshots with verified identity trailers

## Status
Accepted (17d334d, b8ff49e, 8f24741..c1381e7; live-verified Sep 3).

## Context
Image-conditioned KV never reached the disk cache (upstream stance: "disk
payloads intentionally carry no image identity", b0982a1), so any restart
or slot handover in a vision session paid a full cold re-prefill
(minutes at 200-300k). Two facts make a safe design possible: marker
nonces poison text keys, and every request carries its own images (they
are always re-encoded, and now embed-cached), so the payload can be
restored without persisting embeddings if the request can attest them.

## Decision
- KV files gain a magic-tagged section chain after the payload (server-
  owned hooks): KVTM (existing tool map) + new KVV1 vision section of
  [token_start u32 | token_count u32 | 32-byte embedding fingerprint]
  records; KV_EXT_VISION flags files carrying it. Old binaries parse such
  files' tool-map section and ignore the rest.
- Multimodal stores are frontier-only: the request arms a slot_vision_store
  (normalized transcript = marker nonces replaced by fingerprint hex, plus
  identity records); post-generation "turn" stores write payload + trailer.
  Partial-frontier anchors stay text-only (they cannot attest which rows
  depend on which image).
- Load (kv_cache_try_load_vision) verifies before trusting: trailer present
  and intact, every record maps to an identical request span, every span
  fully inside the frontier is attested, no span crosses the frontier, and
  either the request token vector shares the snapshot prefix exactly
  (effective prompt = request tokens verbatim - placeholders must never be
  produced by text tokenization) or all images are attested below the
  frontier (then the loader's exact-prefix + tokenized-suffix prompt is
  safe against BPE re-merge). On success the session's checkpoint
  identities are re-seeded via the new engine API
  ds4_session_set_vision_identities() (ordered, non-overlapping, fully
  inside the checkpoint).
- A load is refused only when the live session is newer unsaved state of
  the SAME conversation (durable_conv = FNV over the normalized base);
  foreign (text or other) live state may be discarded with text-path
  parity. Slots stamp durable_frontier_tokens/durable_conv on each frontier
  store.
- Compaction interacts cleanly: keys are prefix-based, so a compacted
  conversation simply misses every old snapshot, rebuilds, and re-snapshots
  with fresh positions; old files die via conv retirement + LRU.

## Consequences
- Vision sessions survive slot handover and restarts; measured restore
  hits: 607/621 and 690/692 tokens cached post-takeover, ~0.5 KB trailer
  per 10 images against ~30 MB+ payloads.
- Disk budget churn grows with image-session frontiers (one ~GB-scale
  snapshot per turn at large context; the 8k/16k grids plus LRU handle it,
  watch the 128 GiB budget with many concurrent big sessions).
- Image-count request cap raised 16 to 128 (fail-fast DoS/OOM guard before
  encode work; matches trailer capacity and the embed-cache slot count).
  Sessions beyond it serve but skip disk caching; compaction and the 384k
  context window are the practical bounds.
- Deliberately NOT done: shutdown persist of vision sessions (slot ctx
  dies with the request), multimodal partial anchors, and general
  mid-stream live rewind - the Metal DS4 compressor frontier cannot be
  reconstructed at arbitrary positions; GLM-only ds4_session_glm_mtp_rewind
  remains the sole rewind. A real rewind requires frontier snapshots,
  which is engine work beyond this scope.
- Tests: trailer roundtrip, normalize, wildcard, record caps, plus live
  handover/restart scenarios (recipes in this ADR's commits).

## ADR 2026-09-06: Checkpoint self-healing tiers (fingerprint stamp + reload-loop guard)

### Context: three poisoning incidents, one taxonomy
Long pi agent sessions (200-450k tokens, multimodal, tool loops) exhibited
repeated `live kv cache miss ... reason=token-mismatch` at *fixed* token
positions (retained log evidence: common=118557 x43, 273833 x18, 451784 x7,
325177 x40; the Sep 6 ~10:26 stall was the 275958 frontier file reloaded
across turns until a manual abort discarded it, reason=prefill-failed).
Forensic method: log/ds4.trace first_mismatch_token + token_window lines
(the trace already decodes both sides); gguf id work via
tests/gguf_tokenizer.py - ids are gguf-specific, always dump against the
exact file the server loaded.

Root taxonomy (all three are "same bytes, different token ids" - but from
different causes):
1. **Harness history mutation** - pi's superpowers bootstrap injected into
   messages[1] in-memory only, toggling with the superpowers.ts
   `session_start/session_compact/agent_end` state machine; each flip
   rewrites the first user message (events 15:18/15:53 Sep 4). Client-side;
   fix brief written (~/Projects/PiScratch/PI-SUPERPOWERS-BOOTSTRAP-CACHE-FIX.md:
   persist-on-inject / append-only history). Cost: one bounded re-prefill
   per flip once client fixed.
2. **Tokenizer-generation skew** - checkpoint tokens from an engine or
   model file whose tokenizer differs from the running one (the Sep 6
   08:48:52 fingerprint-reject burst is the proven instance; earlier mixed
   provenance unobservable after the forensic purge - and NOTE an earlier
   "TQ/ESOTERICKARMA gguf switch" story was CONFABULATED from garbled
   compaction summaries; the DB shows no such path ever existed).
3. **Sampled-tail contradiction** - a checkpoint whose tail covers
   model-sampled tokens (thinking/DSML) whose bytes re-tokenize differently
   on replay (the pinned-common loops above; the live 325177 x40 storm was
   the benign sibling: per-turn prompt-extension misses WITH progress). This is legitimate
   by design (the preserved-thinking bridge KEEPS sampled tokens; live is
   authoritative) - the bug is only that such a file, restored via its
   text key forever, can never be progressed past via memory tiers.

### Decisions
- **Fingerprint stamp (f1ff74a, upstream #983):** engine computes
  `ds4_engine_tokenizer_fingerprint()` = hash(n_vocab + every token len/bytes
  in id order + merge-rank slots content + behavioral probe set run through
  the real tokenizer). PROBE SET IS APPEND-ONLY. Covers data AND code change
  with no human version constant. Stored as TOKFP section (first trailer
  section, ext bit 1<<5 auxiliary) - pre-checked in kvstore loader by a
  16-byte seek BEFORE payload; mismatch -> reject, session untouched,
  fallback chain walks to next candidate/cold, which rewrites fresh.
  Unstamped legacy files trusted (status quo). CRITICAL LESSON (found by
  @JordiPosthumus static review): first version hashed the raw ds4_str
  ARRAY (pointers!) - same-binary macOS restarts reproduce allocator layout,
  so the pointer bug PASSED live tests by luck. Hash content, never struct
  bytes; regression = two process launches must give identical fp.
- **Reload-loop guard (9d955ec, fork; PR pending evidence):** per-slot
  (disk_reload_path, disk_reload_frontier); second consecutive disk restore
  of the SAME file at the SAME depth = tail provably unreachable ->
  server-side unlink under kv_mu + retry load once (same mechanism the
  prefill-failed abort-discard proved in production twice; automated).
  Memory-tier continuation success clears the tracker. Detection cannot be
  unit-tested; needs a natural loop event or days of clean running.
- **Equivalence guard (bc1be25) was reverted by design**: load-time
  "stored tokens == whole-text re-tokenization" false-positives on ALL
  legitimate bridge-lineage files (sampled boundaries differ by design).
  Do not reintroduce; the two shipped discriminators (engine identity,
  behavioral loop) are the sound versions.
- Tier order (ds4_server.c ~13030-13200): memory-token exact -> multimodal
  exact-extension (#927) -> thinking-visible RAM bridge (text-match, keeps
  live tokens, prompt_for_sync=effective_prompt) -> memory-text (live tokens
  decoded to text, byte-prefix match) -> disk-vision (token-validated exact
  prefix + identity trailer) -> disk-text (text-prefix + [now] fp precheck +
  largest-first retry skip-list). `key=thinking-visible` disk hits = the
  trust-by-text path that re-imported stale variants; the guard + stamp
  cover it.
- Tool-map `DS4_TOOL_MEMORY_MAX_BYTES` 512MiB pruning degrades memory-text
  tier on write-heavy sessions (missing_ids=69) -> disk-tier demotion. Not
  fixed (documented; raise budget or persist map into turn-store trailers if
  it ever hurts).
- Case-insensitive APFS: `MAINTENANCE.md` == `maintenance.md` (inode 13649699
  linode repo). pi's `rm -f maintenance.md` cleanup deleted its own rewrite
  3x; the "stall" at 275958 was a 3338-token heredoc-regeneration turn. No
  engine involvement - model-logic loop, advisory given to pi.

### Review-practice knowledge (Jordi's standard, adopted)
Evidence must be rerun on the SUBMITTED head/config; lossy policy changes
are opt-in and separate PRs; scope: mechanism vs policy; static review
catches what lucky live tests hide; authorship preserved on adopted fixes
(git cherry-pick -C).
