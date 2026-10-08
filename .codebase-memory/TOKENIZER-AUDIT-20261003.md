# Tokenizer/detokenizer per-request setup audit (#3975 analog)

Lane B of the 2026-10-03 overnight (`.codebase-memory/omlx-v070-final-perf.md`
§4 action #3a). Read-only subagent pass, main-session verified excerpts.
Trigger: oMLX #3975 built the streaming detokenizer per request (~45 ms/req on
Qwen3.8); fixed by hoisting to per-tokenizer.

## Verdict: no true #3975-class bug — construction is already load-time

- `vocab_load` (token array + `token_to_id` + `merge_rank` hash tables) runs
  ONLY at engine open (ds4.c:43303-43334, callers :71170/:71217/:71280-82;
  :64248/:64278 are one-shot CLI dump utils with their own model).
- Session vocab-sized buffers (`logits`, `sample_probs`) allocated per slot at
  server startup (ds4_server.c:17826 → ds4.c:73160-61), freed at shutdown.
- Chat templates are compiled-in C string builders (ds4_server.c:3092/:3198/
  :3580), never re-read/re-parsed from disk. Tokenizer fingerprint memoized
  (ds4.c:62226). SSE stream state is O(1) memset + incremental UTF-8/stop
  scans (ds4_server.c:7521-7526, :1344-1379, :15797-15818). rax is server
  bookkeeping only (tool-call dedup/memory), not the tokenizer.

## Same-spirit cousins found (ranked fix candidates)

1. **GPT-2 byte↔codepoint maps recomputed by O(256) linear scan per byte.**
   Encode side: `gpt2_byte_to_codepoint` (ds4.c:42573-42587, hit by every
   non-printable prompt byte incl. ALL UTF-8 continuation bytes ≥0x80,
   ~130-170 iters each). Decode side: `gpt2_codepoint_to_byte`
   (ds4.c:43853-43867, every detokenized codepoint). Both are PROCESS
   CONSTANTS. Fix: two static LUTs filled once (~20 lines); keep the `b>=174`
   unbounded-branch semantics (:43860). Lowest risk.
2. **`slot_refresh_live_text` re-detokenizes the ENTIRE session token list
   after every job** (ds4_server.c:13326-13341, calls :16761/:16790 →
   ds4_kvstore.c:1889-1901) — deliberate, to keep reuse probes memcmp-only
   (:13105-13110), but O(session_len) mallocs + O(256)/codepoint per request
   even when only a few hundred tokens were appended. Biggest absolute win for
   long agent sessions. Fix: incremental append when the new checkpoint
   extends the cached prefix (`live_text_pos` already tracked :13340); full
   re-render only after rewind (:15136)/disk-load/reset. MUST preserve the
   memcmp-only probe contract — open question below.
3. **`ds4_token_text` mallocs per call + scans for literal-special U+FF5C**
   (ds4.c:43878-43905, :43869-43876), per token in stream loop/kvstore
   render/trace. Fix: precompute decoded strings + literal-special flag per
   vocab entry at `vocab_load` (O(vocab) once, few MB @152k) or lazy per-id
   cache for TP-worker memory.
4. **Distributed-path sampler scratch**: `ds4_sample_logits` allocs+frees
   n_vocab floats per generated token (ds4.c:76891-76899,
   ds4_distributed.c:4033-4040, ~600 KB/token @152k). Server path already
   reuses `s->sample_probs` (ds4.c:76909). Fix: hoist scratch to worker-loop
   scope; `sample_top_p_min_p` already takes `prob_scratch` (:44596).
   Only pays off if `ds4_dist_run` is production-relevant (open question).
5. **Trivial**: `getenv("DS4_MTP_SPEC_DISABLE")` inside the token loop
   (ds4_server.c:15712/:15734) — resolve once per request; optional: replace
   O(vocab) memset in `sample_build_probabilities` (ds4.c:44164) with
   tracked-ID zeroing (runs per draft/target token in MTP verify,
   ds4.c:74132-74138/:74518).

Minor/negligible (no action): `special_token_at` 25-entry stack array per `<`
(ds4.c:43575-43617); per-request tool-call rax builds (keyed on request data);
top-k insertion-list worst case O(vocab·top_k) (ds4.c:44616-44628) — benchmark
against the heap variant before touching.

## Overnight plan

- IMPLEMENT now (cheap, testable via full make test in this window): fix 1
  (static LUTs) + fix 5 getenv hoist. **DONE (`d9a5d60`).**
- **Fix #2 (incremental live_text) SHIPPED `58c549c`** after the lane-C
  read-only audit answered open question 1: **SAFE-WITH-CONDITIONS.** The
  sole consumer of `slot->live_text` is the memory-text probe
  (`slot_probe_reuse_locked`, pure byte-prefix memcmp) + router scoring —
  nothing searches/hashes/persists it, and the probe's
  `live_text_pos == live_pos` guard already fails closed on staleness.
  Detok prefix-stability proven by construction: the render loop
  (`ds4_kvstore_render_tokens_text` ds4_kvstore.c:1985-1997) is stateless
  per token (pure `ds4_token_text` appends, zero whole-string
  postprocessing; the SSE partial-UTF8/stop holds are a different path).
  Implementation: per-slot `live_text_ids` snapshot + pure predicate
  `live_text_can_append` (strictly-longer + identical head); every rewind/
  tool-rewrite/disk-swap/reset surfaces as head-mismatch or shrink and
  falls back to full re-render; predicate pinned by model-free --server
  unit test. Vision-store splice (:12404-12414) was the in-repo precedent.
- **TTFT-floor note corrected:** the ~460 ms cold TTFT is NOT explained by
  live_text (refresh runs after `job_complete`, lands on the NEXT request's
  queue time; a cold first prompt sees ~0). live_text fix buys per-turn
  overhead on multi-turn traffic; the cold-floor decomposition remains open.
- **Fix #3 (detok table) SHIPPED `c922e14`** — open question 2 answered:
  EAGER per-engine table at vocab_load (no lazy cache). Measured 1.58 MiB
  @ n_vocab=129,280 (arena 1.00 + off 0.49 + lit 0.12 MiB; ~2.3 MiB at the
  Qwen 152k vocab) — acceptable in every instantiation (one engine per
  process, slots share it, per-slot vocab-sized buffers already exist, TP
  workers pay a few MB each). Cross-check 0 mismatches / 129,280 entries;
  28.7x faster (0.001 vs 0.028 us/token); golden vectors OK.
- Fix 4 (distributed scratch, open question 3 still gates) — unchanged.

## Open questions

1. ~~`slot->live_text` consumers…~~ — **ANSWERED 2026-10-03 lane C: no
   fresh-render requirement anywhere; fix #2 shipped (`58c549c`)** with the
   conditions recorded above.
2. ~~Detok-table memory …~~ — **ANSWERED 2026-10-05: EAGER table shipped
   (`c922e14`)**; measurement above.
3. Is `ds4_dist_run` perf-relevant in production? Gates fix 4. — open.
4. ~~measure realized per-turn refresh savings~~ — **ANSWERED 2026-10-05
   (measured A/B, ~0 realized win).** Method: trace-gated
   slot_refresh_live_text wall-time instrumentation (`6af15aa`) +
   DS4_LIVE_TEXT_FULL A/B knob (`04c421a`) + 40-turn prefix-extending probe
   traffic on one slot (tests/spec_economics/livetext_probe.py), two traffic
   shapes (thinking on / reasoning_effort=none), one binary both arms
   (parse: livetext_ab.py, log results/20261005_livetext_ab.log):
   - arm 1 (thinking on, sessions to 7009 tok): full-render total 2.144 ms
     vs incremental-enabled 2.146 ms over 40 turns — saved −0.002 ms (noise).
   - arm 2 (effort=none, sessions to 3598 tok): 1.084 vs 1.107 ms — saved
     −0.023 ms (noise).
   - The append path NEVER fired (40/40 path=full, both arms). Root cause
     from the trace's first-mismatch window: the live checkpoint and each
     incoming prompt diverge at the prompt/completion token seam — thinking
     on: at the first generated token (the API's returned text omits the
     thinking tokens the session contains); thinking off: the whole
     completion region re-tokenizes differently from the generated tokens
     (API text normalization). The session sync rewinds the completion every
     turn, so the checkpoint is never a strict extension of the rendered
     snapshot and `live_text_can_append` fails closed by design.
   - With the detok table, the full re-render fallback costs ~5-9
     ns/token (arm fits: 0.010 ms + 9.2 ns/tok; 0.014 ms + 4.7 ns/tok):
     0.05-0.07 ms/turn @7k, ~0.5-0.9 ms/turn @100k, ~1.4-2.8 ms/turn @300k.
   Verdict: fix #2 stays shipped (correct, fails closed, unit-pinned) but
   its realized win on API-text round-trip traffic is ~0 — its value case
   rested on the pre-table slow detok, which fix #3 already captured.
   Remaining value is bounded by the full-render cost above and gated on
   token-clean seams that API round-trips don't produce. Optional follow-up:
   a production TRACE_PATH day would measure the real append firing rate on
   chat traffic (chat clients also re-render canonically, so the same seam
   question applies).
