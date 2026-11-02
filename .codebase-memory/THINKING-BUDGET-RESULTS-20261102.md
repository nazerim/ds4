# THINKING-BUDGET-RESULTS-20261102 — Tier-1 hard thinking budget, implementation + verification log

Deliverable of TODO-THINKING-BUDGET-20261102.md + PLAN-THINKING-BUDGET-20261102.md.
All changes in ds4_server.c (350 insertions, 16 deletions). ds4.c/ds4.h NOT
touched; Tier 2 CLOSED (server already drives token selection, so Tier 1
enforces at exact token granularity).

## Final feature surface (as deployed/verified 2026-11-02)

- Request fields on all four routes (chat/completions/anthropic/responses):
  `reasoning_budget_tokens` (canonical, Strata), `thinking_budget_tokens`
  (llama.cpp alias), Anthropic `thinking:{type:"enabled",budget_tokens:N}`
  (now honored; previously accept-and-ignored).
- Precedence: thinking-object > canonical field > alias > env default.
  **Explicitly named 0 opts OUT of the env default** (found via live test:
  unset-vs-0 distinction, -1 sentinel internally).
- Server default: DS4_THINKING_BUDGET_TOKENS (cached first read; 0/unset/
  garbage = no cap).
- Enforcement: per thinking SPAN (resets on re-open; oMLX #3905 open-then-
  count; model's natural close costs no budget; models that never open are
  untouched — oMLX #3904 class structurally impossible). At budget: draft
  blocks clamped to remaining budget; then newline + Strata wrap-up line
  ("I need to conclude my reasoning here.") + newline + close special +
  newline injected token-by-token through the ordinary accepted-token path
  (sampled-token bypass; stream splitters, DSML tool gating, and the
  non-stream reasoning split see a natural close). finish_reason normal;
  injected tokens count as output (Strata semantics).
- No-close-special vocab -> budget no-ops with one WARNING log (never writes
  garbage). Safety valve: >3 failed force attempts disables budget for the
  request with WARNING.
- Observability (oMLX #3687): usage.completion_tokens_details.reasoning_tokens
  (OpenAI + Responses; field absent when no budget active — byte-parity kept)
  + DS4_LOG_DEFAULT line `thinking budget N enforced after M in-span tokens`
  + trace_event.

## Verification record (window 2026-11-02 17:17 →)

Engine/DB artifacts in .codebase-memory/baseline-thinking-20261102/.

### DeepSeek-V4-Flash-Vision-Exp (port 8001)
- PRE-change baseline (old binary): resp-baseline.json/-stream.sse (temp0
  seed42 max1536 -> finish=length, 6371 reasoning chars, 0 answer; sha256
  in MANIFEST).
- Battery: budget 128/256-alias/512 -> termination exact, answer 4274/5049/
  3919 chars, usage reasoning_tokens 138/266/522 (= budget + wrap/close).
- Streaming budget 128: 1538 SSE lines, usage in final chunk
  reasoning_tokens=138, ZERO tag leaks on the wire.
- Anthropic /v1/messages thinking-object 128: thinking block ends with
  wrap-up, text block 4721 chars, stop_reason=max_tokens.
- Forced-boundary + tool: budget 96 mid-think -> finish=tool_calls,
  submit_answer payload valid JSON (7 steps, 2 strategies), usage
  reasoning_tokens=106.
- NO-BUDGET REGRESSION: messages byte-for-byte identical to baseline
  (python ==); usage identical except perf_time_ms (wall-clock field).

### Qwen3.8-Flash-Next (port 8002, DS4_THINKING_BUDGET_TOKENS=256 set)
- env-default request (no fields): enforced at exactly 256 (log:
  `thinking budget 256 enforced after 256 in-span tokens (gen=256)`),
  usage 267.
- request 128 beats env 256 (139 reported); alias 64 works (75);
  explicit 0 opts out of env (ABSENT field, uncapped 4154-char think).
- Streaming 128: usage reasoning_tokens=139 in final chunk.
- Anthropic object 128: thinking tail wrap-up, 3392 chars text.
- Tool prompt forced 96/160: finish=tool_calls, valid submit_answer payload
  both runs; CONTROL (budget 0) shows the no-budget path is untouched.

### Engine/test gates
- ./ds4_test pre-change: all OK. Post-change: all OK, pass/skip sets
  identical (suite-parity diff clean). Qwen-gated suites skipped in both
  runs (DS4_TEST_MODEL gate) — unchanged behavior.
- test_api_thinking_controls_parse extended: object capture/disabled-drops/
  invalid-number-rejected/unknown-keys-still-skipped/named-0-precedence set.
- make ds4-server + ds4_test: zero new warnings.
- dspark-acceptance: see gate results below (running at write time).

## Notable pre-existing findings (NOT budget-caused)

1. `make test` full-suite build is BROKEN on the current tree BEFORE any
   budget edits: tests/test_session_state.o includes ../ds4.c with
   DS4_NO_GPU-style config; the uncommitted ds4.c WIP references
   ds4_gpu_graph / s->graph / dspark_draft_* / metal_graph_dspark_confidence_hidden
   around ds4.c:76100+ without the surrounding #ifndef DS4_NO_GPU guards.
   Production builds (plain make) unaffected. Needs a guard-only fix in the
   dirty ds4.c (owner's call — uncommitted stage-4 WIP).
2. `ps eww` exposes DS4_API_KEY plaintext to same-user processes on this
   host — rotation or launchctl-scoped env worth considering.
3. qwen tools-forced96 first-run anomaly (finish=stop, tool text vanished):
   controls show the model itself fails to complete the tool block under
   max_tokens even WITHOUT budget on that prompt; forced reruns at 96/160
   both completed valid payloads. Classified model-traffic noise, not
   enforcement corruption.

## Status — COMPLETE (2026-11-02 18:00)

- ds4_test final binary: rc=0, "ds4 tests: ok", skip-sets unchanged.
- dspark-acceptance: run manually vs Vision-Exp support per DSPARK-STATE
  known-quirk note (Makefile pins absent 0731 file): all 4 fixtures
  output_match=1 (byte-exact decode parity on no-budget traffic); the
  c_add accepted_draft 3<8 rc quirk is documented, not a defect.
- FINAL-BINARY DeepSeek re-check: no-budget response byte-identical to
  pre-session baseline; budget-128 spot check enforced exactly
  ("thinking budget 128 enforced after 128 in-span tokens"), answer flowed.
- Engine restored: DeepSeek Vision-Exp profile on 8001 + auth_proxy (as
  found). No DS4_THINKING_BUDGET_TOKENS in the deployed env (server default
  OFF; feature is purely request-opt-in).
- No commits made (per scope discipline; operator decides commit window).
