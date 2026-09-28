# p2 review — ds4_server.c: unterminated-tool-call degrade + content marker detector

## Verdict: APPROVE-WITH-NITS (M1 below must land before merge; rest optional)

Behaviour fix is sound and the continuation route is genuinely preserved. One
gating omission contradicts the helper's own documented contract.

## 1. Consumers of finish=="error"
- `final_response` / OpenAI: finish_reason error → `stop`; HTTP 200 not the
  error body at ds4_server.c:9234, and the 500 path (14170) is untouched.
- Responses: 8615 `incomplete` (length|error) → not incomplete; 8916 status
  `failed` → `completed`; 8945 `response.failed` → completed event. Intended.
- Anthropic: previously end_turn *with raw markup*; now end_turn with stripped
  text — strictly better, and the invisibility problem the diff claims is real.
- Logging at 16308/16336 (`!strcmp(final_finish,"error") && err[0]`) still fine
  because err is cleared together with finish; no "finish=error error=\"\"" line.
- Live-state clears: all four (`responses_live_clear`, `anthropic_live_clear`,
  `thinking_live_clear`, `request_live_state_clear`) are reachable from the
  degraded path via the else-branches — no leak of live state on degrade.
- No consumer found that requires error semantics for a model-shape fault.

## 2. Are the two gates sufficient? No — two more remember paths are ungated.
- ds4_server.c:16168 `anthropic_live_remember` and 16179/16196
  `canonicalize_tool_checkpoint` + 16201
  `remember_qwen_tool_turn_visible_checkpoint` all fire on `parsed_calls.len`
  with **no** `degraded_unterminated` check. Reachable when one envelope closed
  and a second did not (the exact rambling shape that motivated the fix):
  `remember_qwen_tool_turn_visible_checkpoint` then binds stripped visible text
  to a live frontier still holding the dropped markup — the phantom-markup
  hazard the helper comment forbids. canonicalize *rewrites* the suffix so it is
  arguably self-healing; the qwen visible remember is not.
- ds4_server.c:~16222 `kv_cache_store_current(s, slot, "turn", prompt_text, …)`
  is ungated too, but it keys on the request prompt, not assistant output, and
  was already ungated for error/length turns — pre-existing, lower priority.

## 3. Helper correctness
- Order correct: strip → finish map → clear err → free+NULL outs → reparse →
  content fallback. Frees precede NULL assignment; no double free, no leak.
- NULL contract honoured for the reparse (all three required); the
  `content_out && !*content_out` fallback allocates only if content_out given.
- Live caller passes NULL/NULL/NULL, so the all-three reparse branch has **no
  production caller** — it is test-only surface (see 6).
- Whole-output-is-markup → content `""` with finish `stop`: acceptable vs the
  old 500/markup, but it yields a silent no-op assistant turn for agent loops;
  the WARNING at the call site is the only signal. Fine.
- Nit: live site passes `ds4_think_mode_enabled(think_mode)` for
  `require_thinking_closed`, while the repair-path parse at 15866 passes
  `false`. Pick one deliberately.
- Nit: helper clears `err` unconditionally; safe only because the caller guard
  excludes finish=="error". Assert or comment that coupling.

## 4. Continuation route preserved — yes
15888 `if (!j->req.stream && !dsml_recovery_attempted && completion < max_tokens)`
is tried first and `goto decode_again`s on success; on failure it sets
finish="error" (15919) and the degrade is in the *else*, so a failed session
append still surfaces. The outer guard (15852) also excludes finish=="error",
finish=="length" and client_stop, so shutdown (15832) and client-stop faults are
never degraded.

## 5. Detector
- Cost: 8 `strstr` passes per message. On a 1.6 MB prompt ≈ 13 MB of scanning,
  well under a millisecond against a multi-second prefill. Negligible; runs
  once per request after tool_memory_attach.
- "False positives" are semantically true positives: a fenced `<think>` really
  does tokenize to the structural id, which is the point of Option A.
- Log spam is the real risk: agents that discuss protocol syntax emit one
  WARNING per request forever. Recommend DS4_LOG_DEFAULT/INFO, or rate-limit /
  env gate. WARNING is too loud for an expected, non-actionable condition.
- Missing markers (observability gap, not correctness): no GLM envelope strings
  though GLM is a supported syntax (cf. test_invalid_glm_tool_error_suffix), no
  Qwen `<function=` / `<parameter=`, no DSML *closing* tags, no
  `<｜DSML｜invoke`/`parameter` openers, no `<tool_response>`-style role
  marker for assistant. Given 27 mapped strings, 8 is thin; a close-only
  injection is invisible today.

## 6. Test adequacy — highest-value MISSING case
No test covers the production shape or the gating that is the actual risk: a
degraded turn must not remember a visible checkpoint. Add a case that drives
`degraded_unterminated=true` and asserts `remember_qwen_tool_turn_visible_checkpoint`
/ `responses_live_remember` are skipped (or, minimally, that a two-envelope turn
with one closed + one unclosed yields parsed_calls>0 **and** no visible
checkpoint). Second-best missing case: the NULL-triple caller followed by the
real post-strip parse, since that is the only path production executes.

## Must-fix
- M1: gate the `parsed_calls.len` remember paths at ds4_server.c:16168, 16196,
  16201 with `!degraded_unterminated` (or document per-site why each is safe).

## Nice-to-have
- N1: downgrade/rate-limit the marker WARNING (5).
- N2: add GLM + Qwen + DSML closing markers to the list (5).
- N3: reconcile `require_thinking_closed` between 15866 and the new call site (3).
- N4: test the production NULL-triple path and the gating (6).
- N5: consider gating the multimodal `reason=turn` store, or record why not (2).
