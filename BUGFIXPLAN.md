# BUGFIXPLAN.md — DSML-in-Think Recovery KV Cache Miss Fix

**Status:** Implemented and tested
**Date:** 2026-08-01
**Issue:** DSML tool calls inside unclosed `<think>` tags trigger recovery that causes KV cache misses and 7+ minute full prefills

## Problem Summary

When the model emits DSML tool calls inside an unclosed `<think>` tag, the recovery mechanism injects `</think>` tokens into the live session. The next request from the client contains visible text + tool result (not raw DSML), causing a token-mismatch at the KV cache boundary. This triggers a full prefill from zero: 90k tokens at 280 t/s = 5.5 minutes.

## Root Cause

`should_canonicalize_tool_checkpoint()` skips canonicalization when `raw_tool_text` is present, assuming the client will replay raw DSML bytes. After recovery, this assumption is **wrong**: the client sends visible assistant text + tool result, not raw DSML. The live session diverges from the client's next prompt → token-mismatch → full prefill.

## Fix

**File:** `ds4_server.c` (3 minimal changes)

1. Added `bool think_tool_recovery_fired` flag (line 11413)
2. Set flag when recovery fires (line 11653)
3. Force canonicalization after recovery (line 12083): `(think_tool_recovery_fired || should_canonicalize_tool_checkpoint(...))`

**How it works:** After recovery, canonicalization rewrites the live KV suffix to match what the client will send next, avoiding the token-mismatch.

## Code Changes

```diff
@@ -11411,6 +11411,8 @@ static void generate_job(server *s, server_slot *slot, job *j) {
 
     int dsml_recovery_attempts = 0;
     bool post_recovery_validation_pending = false;
+    /* Set when think-tool recovery fires; forces checkpoint canonicalization. */
+    bool think_tool_recovery_fired = false;
     uint64_t rng = j->req.seed ? j->req.seed :
         (((uint64_t)time(NULL) << 32) ^ (response_seq << 1) ^
          (uint64_t)(uintptr_t)j);
@@ -11648,6 +11650,7 @@ decode_again:
                                     "think tool recovery after %d generated tokens",
                                     completion);
                         post_recovery_validation_pending = true;
+                        think_tool_recovery_fired = true;
                         clamp_live_stream_positions(&plain_stream_pos, &openai_live,
                                                     &anthropic_live, &responses_live,
                                                     recovery_inject_at);
@@ -12077,14 +12080,24 @@ decode_again:
 
     if (j->req.kind == REQ_CHAT && parsed_calls.len &&
         j->req.api != API_RESPONSES &&
-        should_canonicalize_tool_checkpoint(s, &parsed_calls))
+        (think_tool_recovery_fired ||
+         should_canonicalize_tool_checkpoint(s, &parsed_calls)))
     {
         /* Chat/completions has no protocol object that binds the next request
          * to this live KV state.  Canonicalize only the fallback tool-call
          * path where we lack exact sampled DSML replay; when raw DSML is known,
          * replaying those bytes keeps future prompts aligned without rebuilding
          * hidden reasoning.  Responses deliberately skips this path because its
-         * previous_response_id contract binds the next turn to live state. */
+         * previous_response_id contract binds the next turn to live state.
+         *
+         * After think-tool recovery, the raw-DSML-replay assumption is wrong:
+         * the recovery injected closing-think tokens into the live session,
+         * and the DSML tool-call text was extracted as a tool call (not sent
+         * as assistant content).  The next client prompt will contain the
+         * visible assistant text plus tool result, not the raw DSML bytes.
+         * Force canonicalization to rewrite the live KV suffix to match what
+         * the client will replay, avoiding a token-mismatch cache miss and
+         * 7+ minute full prefill. */
         canonicalize_tool_checkpoint(s, slot, j, ctx_span, trace_id,
                                      parsed_content ? parsed_content : "",
                                      parsed_reasoning, &parsed_calls);
```

## Verification

✅ **Build:** Compiles cleanly with zero warnings
✅ **Tests:** All server tests pass (`./ds4_test --server`)
✅ **Expected impact:** Eliminates 5+ minute delays per recovery event

## Next Steps

1. **Runtime testing:** Run server with model that triggers think-tool recovery to confirm fix
2. **Monitor logs:** Look for "tool checkpoint canonicalized" messages after recovery
3. **Upstream port:** Consider porting fix to `antirez/ds4` upstream

## References

- Recovery function: `ds4_server.c:10152-10208` (`chat_think_tool_recovery`)
- Canonicalization: `ds4_server.c:10699-10857` (`canonicalize_tool_checkpoint`)
- Log evidence: `log/ds4.log:1744-1819` (repeated pattern)
- Related: `DS4PLAN.md` (DSML recovery hardening)
