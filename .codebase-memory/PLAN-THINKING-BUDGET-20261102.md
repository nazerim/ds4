# PLAN-THINKING-BUDGET-20261102 — Tier-1 implementation plan (window open)

> STATUS 2026-11-02: SHIPPED + VERIFIED — see
> THINKING-BUDGET-RESULTS-20261102.md for the full evidence log.
> One live-test-driven design change: explicitly-named request budget 0
> opts out of the server default (unset-vs-0 distinction).

Companion to TODO-THINKING-BUDGET-20261102.md. Anchors verified in-tree
2026-11-02 (engine PID 27033, DeepSeek Vision-Exp profile, ds4-server built
09:07; uncommitted ds4.c DSpark stage-4 + ds4-server.sh KV relocation are
ALREADY in the deployed binaries — this work rides on top of them).

## Headline finding (changes the TODO's Tier split)

The server loop **already drives token selection**: it samples via
`ds4_session_sample*` and commits via `server_eval_token(s, slot, token)`
(ds4_server.c ~15773-15830, ~14861-14940). The "~50-token chunks" in the
TODO are the *log cadence* (`next_decode_log = 50`), not enforcement
granularity. Therefore:

- **Tier 1 can enforce at EXACT token granularity**: at budget, stop sampling
  and feed the close-sequence tokens one at a time through the same
  `server_eval_token` + text-feed path. No ds4.c sampler hook, no logits
  override, no DSpark adoption-swap rewinds (engine draft-rejection is
  handled inside `ds4_session_eval_speculative`; injection is just more
  accepted tokens from the server's point of view).
- **Tier 2 stays deferred** — and should probably be closed unless live tests
  surprise us. ds4.c is untouched, so the engine binary is behavior-identical
  for all no-budget traffic.

## Design decisions (defaults unless operator overrides — see Open items)

1. **Fields** (all parsed into `request` struct ~810-861, new
   `int thinking_budget_tokens; /* 0 = no cap */`):
   - OpenAI route (~4284-4448), Responses route (~5465-5695), Anthropic
     /v1/messages route (~5778-5863), completions route (~4483-4665, field
     accepted for parity): top-level `reasoning_budget_tokens` + llama.cpp
     alias `thinking_budget_tokens` (first named wins; alias has no higher
     precedence).
   - `parse_thinking_control_value` (~1077): add `int *budget_out` out-param;
     capture `budget_tokens` ONLY when `type:"enabled"` (Anthropic semantics);
     disabled + budget -> ignore value, keep current lenient parse.
   - Server default: `DS4_THINKING_BUDGET_TOKENS` env (strtol; 0/unset/garbage
     = no cap). Precedence: request > env > none. Parse negative/0 as no cap
     (never an error — Strata-compatible).
   - Validation: clamp to `[0, INT_MAX]`; if `0 < budget` and request also has
     `max_tokens`, do NOT reject when budget >= max_tokens (Strata logs a
     warning shape instead; answer-room warning logged at enforcement).
2. **Counter**: ride `thinking_state` (~13829-13863, sliding-window tag
   tracker already seeded from prompt via `thinking_state_from_prompt`).
   Per accepted token in the `for ti` loop (~15840+): count when
   `was_thinking || thinking.inside` AFTER the feed, EXCLUDING the piece that
   closes the span naturally (oMLX #3905 lesson: counting starts only after
   open; close tag costs no budget). Re-open within the turn (second span)
   resets the counter — the budget terminates *each open span* (trivial with
   existing state; was_thinking transition detects re-open).
3. **Enforcement point** (budget hit, inside thinking, token-exact):
   - Before each speculative/batched step, when `thinking.inside`, clamp the
     block cap: `min(max_tokens - completion, budget_remaining)` so a draft
     block can never overrun the budget (accepted[2] batch path + toks[17]
     spec path both honor a smaller cap; when `budget_remaining < 2`, force
     the plain single-token path).
   - At the exact boundary: break out of sampling; run
     `force_close_thinking()`: tokenize `"\n" + close-tag + "\n"` once per
     request via `ds4_tokenize_rendered_chat` (same tokenizer that handles
     control tokens on the rebuild path ~12867/13037), then for each id:
     `server_eval_token` -> `ds4_token_text` -> `buf_append(&text, ...)` ->
     `thinking_state_feed` -> `openai_sse_stream_update` (stream mode).
     Injected pieces go through the SAME stream plumbing as sampled ones, so
     `
</think>

 stays hidden from `reasoning_content`
     (existing splitter skips it), tool-gating flips via
     `tool_scan_waiting_for_think_close` naturally, and the non-stream
     `split_reasoning_content` sees a well-closed span.
   - Injected tokens count toward `completion` (they are real output — Strata
     semantics) but NOT toward budget; budget is exhausted by then anyway.
   - Then resume the normal decode loop with thinking closed -> model answers.
     finish_reason unaffected ("stop"/"length" by normal rules).
   - Wrap-up line: **v1 = silent force-close** (oMLX pattern). Strata-style
     injected wrap-up text is a flagged follow-up (`thinking.wrap_up: true`
     no — keep out of v1 surface).
   - Guard: model never opened thinking (`was_thinking` never true, not
     seeded-open) -> counter never runs, nothing injected (oMLX #3904
     structurally avoided). DeepSeek seeded-open case DOES run the counter
     (assistant prefix already opens the span).
   - Cancel/disconnect mid-injection: check `job_cancelled` between evals,
     same as token loop.
   - max_tokens interplay: if budget remaining >= room to answer, proceed;
     when close+answer leaves no room (completion hits max with answer empty),
     log Strata-shaped warning (server_log DS4_LOG_DEFAULT).
4. **Observability** (oMLX #3687):
   - `usage.completion_tokens_details.reasoning_tokens` on OpenAI + Responses
     usage builders (~7379 `append_openai_usage_json`, ~8969 responses) =
     counter value; Anthropic keeps thinking inside output tokens (spec
     compliance), no new field.
   - Trace/log: `trace_event(... "thinking budget %d enforced at %d thinking
     tokens")` + one log line per enforcement. Only fires when budget set.

## Zero-change guarantee (capture discipline)

No-budget path compiles to the pre-existing code path: new fields default 0,
every new branch is gated on `budget > 0 && thinking.inside`. ds4.c/ds4.h
untouched -> DS4_TRAIN_DUMP / DS4_TD_PARITY taps and draft-cycle behavior
cannot change. Regression proof = byte-for-byte identical battery on
no-budget traffic (see below).

## Edit sites (all in ds4_server.c)

| # | Site | Change |
|---|------|--------|
| 1 | ~810-861 request struct | + `thinking_budget_tokens`, + `thinking_tokens` counters |
| 2 | ~1077 `parse_thinking_control_value` | + budget out-param for `type:"enabled"` |
| 3 | ~4284/4483/5465/5778 route parsers | + top-level field + alias + env default merge |
| 4 | ~15773-15830 decode loop | + clamp block cap + budget counter in `for ti` |
| 5 | new fn near thinking_state (~13890) | `force_close_thinking()` |
| 6 | ~7379/~8969 usage builders | + `reasoning_tokens` detail |
| 7 | ~20263 `test_api_thinking_controls_parse` | update semantics + new cases |
| 8 | unit tests (~19493-19575 stream tests area) | stream-splitter + counter tests |

Unit-test surface: `test_api_thinking_controls_parse` currently asserts
budget_tokens is DROPPED (`{"type":"disabled","budget_tokens":1024}` — stays
valid: disabled still ignores budget). Add: enabled+budget captured, alias
wins, request-over-env, 0 = off, negative = off, unknown keys still skipped.

## Window schedule (engine currently STOPPED-able; window OPEN now)

W0. **Pre-flight (engine still up):** `git status` baseline note (dirty ds4.c
    + ds4-server.sh are deployed, leave alone); capture baseline: run a
    long-thinking no-budget request through the live server, save full
    response + log slice to `.codebase-memory/baseline-thinking-20261102/`
    for later byte-compare. **Ask operator: bank first?** (DSPARK-STATE
    bank rhythm — graceful stop persists conversation tails; live 436k-ctx
    traffic says yes).
W1. **Graceful stop:** `./ds4-server.sh stop` (operator-approved).
W2. **Baseline suite:** `make` ; `make test` (full ./ds4_test on PRE-change
    tree = current binary parity proof) — record pass list.
W3. **Implement edits 1-8** (server-only). `make ds4-server`.
W4. **Post-change suite:** `make test` again — all non-budget suites must
    match W2 exactly. New parser/stream unit tests pass.
W5. **Live battery (new binary):** `./ds4-server.sh start` (same DeepSeek
    profile PID 27033 ran).
    - Budget 128 and 512 on a long-thinking prompt, streaming: thinking
      terminates at budget exactly (+/-0 tokens now, not +/-chunk), close tag
      hidden, answer present/parseable, `reasoning_tokens` in usage,
      finish_reason clean.
    - Non-streaming variant of same request.
    - Anthropic route: `thinking:{type:"enabled",budget_tokens:N}` + top-level
      alias.
    - Tool-call-heavy run with budget mid-think -> tool payloads survive
      (DSML gating resumes after force-close).
    - No-budget identical-request compare vs W0 baseline (byte-for-byte).
    - env-default (`DS4_THINKING_BUDGET_TOKENS=256` restart-less? no — read
      per-request build, so export in script env; or test via a start-env
      variant) + request-overrides-env case.
W6. **Qwen3.8 coverage:** needs profile swap (`restart-qwen`) = second
    engine cycle inside/beyond this window. If operator grants: repeat W5
    subset on qwen (budget 128/512, tool run, no-budget parity). Else: mark
    deferred, DeepSeek-only merge-readiness.
W7. **dspark acceptance** (`make dspark-acceptance`) on no-budget traffic if
    the window holds; else schedule next window. (ds4.c untouched, so this
    is belt-and-braces, same binary engine.)
W8. Restart engine to operator's preferred profile; write results into
    `.codebase-memory/THINKING-BUDGET-RESULTS-<date>.md`; no commits unless
    asked.

## Open items for the operator — ALL ANSWERED 2026-11-02

1. Bank: **already banked**, window open.
2. Re-open semantics: **budget per thinking SPAN** (reset on re-open).
3. **Strata-style visible wrap-up line** chosen over silent close.
4. **Qwen3.8 tested too** (both models overthink).
5. **Tier 2 closed** (resolution recorded in the TODO).

## Risks

- `ds4_tokenize_rendered_chat` on a fragment: must verify it treats the close
  tag as the special token and not BPE-split text (rebuild path tokenizes
  whole rendered prompts; a bare "\n
</think>

\n" fragment
  should hit the special-token branch — W3 first experiment; fallback is
  resolving ids from vocab directly via a tiny helper (still ds4.h API:
  `ds4_token_is_thinking_control` exists for the reverse check).
- Batched/qwen `accepted[2]` clamp: block cap <=1 forces non-speculative step;
  verify no assertion against cap<2 in `server_eval_tokens` batched branch.
- Stream splitter: injected close pieces feed `openai_sse_stream_update` with
  growing raw buffer; the hold-back logic (`</think>` minus-1) should release
  everything once the close arrives — covered by W5 streaming battery.
- Prompt-replay / KV lineage: force-close tokens extend the session sequence
  mid-turn, so future replays of the assistant turn must include the injected
  close (they read from the session token stream / stored conversation —
  verify stored history path picks up injected text, W5 multi-turn case).
