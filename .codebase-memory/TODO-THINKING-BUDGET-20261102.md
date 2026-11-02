# TODO-THINKING-BUDGET-20261102 - hard thinking budget for ds4 (separate-agent handoff)

> Implementation plan (window-bound schedule, revised tier split):
> `.codebase-memory/PLAN-THINKING-BUDGET-20261102.md`

## Goal
Per-request (and optionally server-default) HARD cap on thinking-token count for
Qwen3.8 and DeepSeek V4.1 chat serving: once the model emitted N thinking tokens,
the server terminates the thinking span (wrap-up + close tag) and the model
continues into its visible answer. Today ds4 has only SOFT prompt-injected effort;
Anthropic 'budget_tokens' is accepted-and-ignored by design.

## Scope discipline (read first - repo rules)
- Tier 1 (this TODO) = ds4_server.c ONLY. Do NOT touch ds4.c engine sampler/Metal
  internals - that is Tier 2, explicitly deferred; would need a user window.
- Live server restarts are window-bound: ask the operator before restarting
  ds4-server. make test / ./ds4_test need the engine STOPPED (process lock);
  --server subsets coexist with a running engine.
- No commits unless explicitly asked.
- Capture must be untouched: with no budget on a request, behavior must be
  byte-for-byte unchanged (DS4_TRAIN_DUMP / DS4_TD_PARITY taps keep firing as
  they do). Budgets are opt-in per request precisely because forced thinking
  boundaries reshape the draft-cycle stream (thinking = highest-engagement
  drafting regime, 45% full-window proposals measured 2026-11-02).

## Prior art (web-verified 2026-11-02, production precedent exists)
Strata (github.com/Niko1221/Strata), field 'reasoning_budget_tokens':
- request field on OpenAI AND Anthropic routes, plus llama.cpp alias
  'thinking_budget_tokens'; request value wins over per-model config default
  (serve/runconfig.py defines it; serve/frontend.py back-fills the config value
  into requests that do not name one). Default 0 = no cap.
- Enforcement SERVER-side, not engine-side: at budget, ends thinking with a
  short wrap-up line + close tag, model answers from there; wrap-up counts as
  output tokens. Positioned as leaving room for the answer.
oMLX (github.com/jundot/omlx), field 'thinking_budget' + per-model
'thinking_budget_enabled/tokens':
- enforcement = mlx-lm LOGITS PROCESSOR (omlx/api/thinking.py
  ThinkingBudgetProcessor): counts tokens genuinely generated while in thinking
  (starts counting only after the model OPENS thinking), at exhaustion forces
  the full close sequence (newline + close-tag id(s) + trailing newlines,
  matching the training pattern) one token at a time via -inf logits mask, then
  no-ops. Handles multi-token close tags (sliding window), UTF-8 boundaries,
  re-entry reset, and SPECULATIVE DECODING via snapshot_state/restore_state
  rewind on draft rejection (omlx/speculative/processing_sampler.py).
- their bug list = our design review: silently ignored on native-template
  models (#3112), enforcement invisible on the wire - finish_reason stop, no
  reasoning token count in usage (#3687), damages models that skip the thought
  channel (#3904), ignored under grammars, miscounted before think-open (#3905).

## Current ds4 surface (verified in-tree 2026-11-02)
- ds4.h: ds4_think_mode enum NONE/LOW/MEDIUM/HIGH/MAX + numeric 1-100 levels
  (DS4_THINK_LEVEL_BASE); enabled only for DeepSeek-V4.1 family.
- ds4_server.c parse_thinking_control_value (~1077): accepts bool or object;
  honors type=enabled/disabled; EVERY unknown key (incl budget_tokens) hits
  json_skip_value - parsed then dropped. Unit test
  test_api_thinking_controls_parse (~20263) asserts exactly this ignore.
- parse_reasoning_effort_value/name (~1039): OpenAI names + numeric strings +
  null; parse_output_config_effort for Anthropic output_config.effort;
  parse_chat_template_kwargs (~1118) for vLLM-style enable_thinking.
- Request struct: ds4_server.c ~4286 (reasoning_effort default DS4_THINK_HIGH).
- Thinking spans ARE already tracked server-side (thinking-replay path for
  tool traffic: struct comment ~844, json_content_replace ~2237): the budget
  counter should ride this tracking, not invent a new one.
- ds4.c vocab exposes think_start_id (used by qwen4_chat_assistant_prefix
  ~44508); close id likewise resolvable from vocab special tokens.
- Chat decode runs in ~50-token chunks (log 'decoding chunk' rows) - the
  natural Tier-1 enforcement granularity (+/-1 chunk).

## Tier 1 implementation (this TODO)
1. Wire fields: request-level 'reasoning_budget_tokens' (OpenAI + Anthropic
   /v1/messages routes) and llama.cpp alias 'thinking_budget_tokens'; ALSO
   honor budget_tokens inside Anthropic thinking:{type,budget_tokens} when
   type=enabled (currently skipped). Precedence: request > server default >
   none. Server default via DS4_THINKING_BUDGET_TOKENS env (0/unset = no cap).
   Parse into the request struct (~4286); update the compat unit test to the
   new semantics (keep testing type-only objects still).
2. Counter: per-session thinking-token count derived from the EXISTING
   thinking-span tracking between open-think and close token ids. Start at
   open (oMLX #3905 lesson), reset never mid-turn (re-entry out of scope for
   v1 unless trivially supported by existing parser state).
3. Enforcement at budget N, chunk boundary:
   - stop emitting thinking deltas;
   - append to the session sequence: newline + close-tag token(s) (+ training
     pattern trailing newlines; mirror ds4.c chat-prefix conventions);
   - continue decode so the answer flows; surface a wrap-up marker (Strata
     style short line) - decide: injected text vs stream-only notice;
   - finish_reason stays normal; usage counts real generated tokens.
4. Guards:
   - no cap when budget 0/unset AND env unset: zero behavior change (capture
     and all regression batteries must pass untouched);
   - model skipped thinking entirely (never emitted open token): counter never
     runs - the oMLX #3904 class of damage is structurally avoided; do NOT
     force-close text the model wrote as content;
   - tool turns: if thinking is dropped/replaced by tool_call blocks, budget
     still terminates thinking but must not corrupt tool payloads;
   - max_tokens interplay: budget remaining should leave answer room (log the
     same warning shape as Strata when reply maxes out mid-thinking).
5. Report on the wire: add thinking tokens generated to usage/logs
   (oMLX #3687 lesson - enforcement must be observable).

## Tier 2 (CLOSED 2026-11-02 — not implementing)

> Resolution: survey of the decode loop showed the server already drives
> token selection (ds4_session_sample* -> server_eval_token; the "~50-token
> chunk" was log cadence only). Tier 1 as implemented per
> PLAN-THINKING-BUDGET-20261102.md enforces at EXACT token granularity
> (draft blocks clamped to remaining budget; close sequence injected
> token-by-token through the normal accepted-token path). The sampler hook
> below is unnecessary; operator confirmed closure 2026-11-02.
>
> Original notes: exact-token budget in ds4.c sampler: per-session one-shot
> 'force next token id' consumed by the sampler at the verify/sample step
> (argmax-pin via logits row override), rewind counter on draft-reject at the
> 4 DSpark adoption swap-sites (~64948/65647/65787/65996) - oMLX
> snapshot/restore is the pattern. Only worth it if 50-token granularity
> proves insufficient; it does not.

## Tests / acceptance
- Parser unit tests (extend test_api_thinking_controls_parse): budget honored
  via field, via Anthropic object, alias wins, request-over-default, 0 = off.
- ./ds4 CLI or --server test battery (engine STOPPED for ./ds4_test; ask
  operator for the window): long-thinking prompt with budget 128/512 ->
  thinking terminates at budget (+/-chunk), answer present and parseable,
  finish_reason clean, usage reports thinking tokens.
- Qwen3.8 AND DeepSeek-V4.1 coverage; one tool-call-heavy run to prove
  tool payloads survive the cut.
- Regression gate: full suite + dspark acceptance on no-budget traffic
  identical to pre-change (byte-for-byte); DS4_TD_PARITY/DS4_TRAIN_DUMP
  untouched when no budgets are used.
- Lint/build: make ds4-server; run make test only per the stop/run runbook
  (ds4-server.sh stop -> tests -> start-qwen/restart-qwen).

## Pointers
- ds4_server.c: parse_thinking_control_value ~1077; parse_reasoning_effort_*
  ~1039-1076; parse_output_config_effort ~1155; request struct ~4286;
  /v1/messages ~17228; thinking-replay ~844 + ~2237; compat test ~20263.
- ds4.h enum ds4_think_mode ~26-33; ds4.c think ids: vocab think_start_id
  (~44508 usage); level helpers ~61687-61731.
- Context docs: .codebase-memory/DSPARK-STATE-20261102.md (method notes
  cover restart/test discipline); this research thread (Strata DETAILS.md,
  omlx thinking.py) was web-verified 2026-11-02 - cite, do not re-derive.
