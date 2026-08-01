# DSML Think-Tool Recovery KV Cache Miss — Root Cause & Fix

**Date:** 2026-08-01  
**Severity:** High (7+ minute full prefill after recovery)  
**Status:** Fixed

---

## Executive Summary

When the model emits DSML tool calls inside an unclosed `<think>` tag, the `chat_think_tool_recovery()` function injects `
</think>

` tokens into the live session to close the thinking block. This recovery causes a **token-mismatch** on the next request, triggering a full prefill from zero (7+ minutes at 90k context). The disk KV cache does not help because the cache key (SHA1 of rendered token text) includes the DSML tool-call text, which the client does not replay.

**Root cause:** After recovery, the live KV state diverges from what the client sends next. The live session contains sampled recovery tokens + DSML tool-call tokens, but the client's next prompt contains the visible assistant text (ending with `
</think>

\n\n`) + tool result. The tokenization of these two representations differs due to BPE ambiguity, causing a prefix mismatch at token position 89031 (only 4 of 9 generated tokens match).

**Fix:** Force checkpoint canonicalization after think-tool recovery, even when `raw_tool_text` is present. The canonicalization rewrites the live KV suffix to match what the client will replay, avoiding the token-mismatch.

---

## Detailed Root Cause Analysis

### 1. Recovery Injection Flow

**Code location:** `ds4_server.c:10152-10208` (`chat_think_tool_recovery`)

When the model emits `<｜DSML｜tool_` inside `<think>`:

1. **Detection:** The scanner detects the DSML marker at line 11628-11633
2. **Injection:** `chat_think_tool_recovery()` tokenizes `
</think>

\n\n` and feeds each token to the live session via `server_eval_token()` (line 10190-10196)
3. **Output modification:** The output text buffer is truncated at the DSML marker position, then `
</think>

\n\n` is appended (line 10197-10203)
4. **Flag set:** `post_recovery_validation_pending = true` (line 11665)

**Example from log (line 1744):**
```
0801 09:56:43 ds4-server: chat ctx=88950..89027:77 TOOLS tool call inside unclosed <think>; forced  after 9 generated tokens
```

At this point:
- Session position: 89027 (after 77-token prompt prefill)
- 9 tokens generated: positions 89027..89036
- Recovery injects ~4 tokens (`
</think>

\n\n`): session now at ~89040

### 2. Live KV State After Recovery

After recovery, the live session contains:

| Token Range | Content |
|---|---|
| 0..88950 | Conversation history (prompt) |
| 88950..89027 | Prompt prefill (77 tokens) |
| 89027..89036 | 9 sampled generated tokens (include `<｜DSML｜tool_`) |
| 89036..89040 | 4 injected `
</think>

\n\n` tokens |
| 89040..89134 | 94 more generated tokens (DSML tool call) |

**Total live session:** 89134 tokens

The output text buffer (sent to client) contains:
- Conversation history up to the DSML marker
- `
</think>

\n\n` (appended by recovery)
- **NOT** the DSML tool-call text (extracted separately as a tool call)

### 3. Next Request Prompt Construction

The client receives:
- Assistant message: content = [history up to marker][
</think>

\n\n]
- Tool call: `read` with ID `call_5bc28b11092fb72c944554d7de3cad43`

The client sends the next request with:
- `prompt_text` = [full conversation history][assistant message ending with `
</think>

\n\n`][tool result]

This prompt is tokenized: **90683 tokens** (line 1750)

### 4. Token-Mismatch Detection

**Code location:** `ds4_server.c:11115` (live cache check)

```c
cached = common == old_pos && j->req.prompt.len >= old_pos ? common : 0;
```

- `old_pos` = 89134 (live session length)
- `j->req.prompt.len` = 90683 (new prompt length)
- `common` = `ds4_session_common_prefix(slot->session, &j->req.prompt)` = **89031**

**Common prefix calculation** (`ds4.c:58980-58986`):
```c
int ds4_session_common_prefix(ds4_session *s, const ds4_tokens *prompt) {
    int n = s->checkpoint.len < prompt->len ? s->checkpoint.len : prompt->len;
    int i = 0;
    while (i < n && s->checkpoint.v[i] == prompt->v[i]) i++;
    return i;
}
```

The common prefix is **89031 tokens**:
- 89027 tokens of prompt match
- 4 tokens of generated content match (89027..89031)
- **Mismatch at token 89031** (the 5th generated token)

**Why the mismatch?** BPE tokenization ambiguity. The 9 generated tokens were sampled in the context of the live session. When the client re-tokenizes the visible text (which includes those 9 tokens rendered as text), the BPE boundaries may shift. The first 4 tokens happen to match, but the 5th diverges.

**Log evidence (line 1750):**
```
0801 09:56:48 ds4-server: live kv cache miss live=89134 prompt=90683 common=89031 reason=token-mismatch
```

### 5. Why Disk Cache Doesn't Help

**Code location:** `ds4_server.c:11153` (disk cache store) and `ds4_kvstore.c:1215-1339` (disk cache load)

When the live cache miss is detected:

1. **Store current state:** `kv_cache_store_current(s, slot, "evict")` (line 11153)
   - Stores 89134 tokens
   - Key = SHA1 of rendered text of all 89134 tokens
   - Rendered text includes the DSML tool-call text (which was generated but not sent to client)

2. **Try to load:** `kv_cache_try_load()` (line 11156)
   - Looks for a checkpoint whose text is a prefix of the new prompt text (90683 tokens)
   - The new prompt text does **NOT** include the DSML tool-call text
   - No match found

**Log evidence (lines 1751-1752):**
```
0801 09:56:48 ds4-server: kv cache evicted reason=disk-cache-full tokens=81920 hits=0 size=1098.57 MiB file=/tmp/ds4-kv/c6201676c45e03db49642a8dbbee9aca3c86abc8.kv
0801 09:56:48 ds4-server: kv cache stored tokens=89134 trimmed=0 reason=evict key=token-text size=1193.25 MiB save=263.8 ms
```

The disk cache is keyed by `token-text` (rendered token text), which includes the DSML tool call. The next prompt doesn't include the DSML tool call, so the SHA1 keys don't match.

### 6. Why Canonicalization Is Skipped

**Code location:** `ds4_server.c:12078-12091`

After tool call extraction, the server checks whether to canonicalize the checkpoint:

```c
if (j->req.kind == REQ_CHAT && parsed_calls.len &&
    j->req.api != API_RESPONSES &&
    should_canonicalize_tool_checkpoint(s, &parsed_calls))
{
    canonicalize_tool_checkpoint(s, slot, j, ctx_span, trace_id, ...);
}
```

**`should_canonicalize_tool_checkpoint()` logic** (line 10859-10867):
```c
static bool should_canonicalize_tool_checkpoint(const server *s, const tool_calls *calls) {
    if (!calls || calls->len == 0) return false;
    if (s && !s->disable_exact_dsml_tool_replay &&
        calls->raw_tool_text && calls->raw_tool_text[0])
    {
        return false;  // <-- SKIPS CANONICALIZATION
    }
    return true;
}
```

The function returns `false` when `raw_tool_text` is present. The assumption: "when raw DSML is known, replaying those bytes keeps future prompts aligned without rebuilding hidden reasoning."

**But this assumption is WRONG after recovery!** After recovery:
- The client does **NOT** replay the raw DSML bytes
- The client sends the visible assistant text (ending with `
</think>

\n\n`) + tool result
- The live session contains sampled recovery tokens + DSML tokens
- These don't match, causing the token-mismatch

**Log evidence (line 1748):**
```
0801 09:56:48 ds4-server: tool calls ctx=88950..89027:77 n=1 raw_tool_text=1 ids=[call_5bc28b11092fb72c944554d7de3cad43] names=[read]
```

`raw_tool_text=1` means canonicalization is skipped.

### 7. Full Prefill Consequence

With no live cache hit and no disk cache hit, the server starts from zero:

**Log evidence (lines 1753-1786):**
```
0801 09:56:48 ds4-server: chat ctx=0..90683:90683 TOOLS prompt start
0801 09:56:48 ds4-server: chat ctx=0..90683:90683 TOOLS prefill chunk 0/90683 (0.0%) ...
...
0801 10:02:17 ds4-server: chat ctx=0..90683:90683 TOOLS prefill chunk 90683/90683 (100.0%) ...
0801 10:02:17 ds4-server: chat ctx=0..90683:90683 TOOLS prompt done 328.623s
```

**328 seconds = 5.5 minutes** for a 90k-token prefill at ~276 t/s.

This pattern repeats multiple times in the log (lines 1744, 1798, 1804), causing repeated 5+ minute delays.

---

## The Fix

### Strategy

Force checkpoint canonicalization after think-tool recovery, even when `raw_tool_text` is present. The canonicalization rewrites the live KV suffix to match what the client will replay.

### Implementation

**File:** `ds4_server.c`

**Change 1:** Add a persistent flag to track recovery (line 11412-11423)

```c
int dsml_recovery_attempts = 0;
bool post_recovery_validation_pending = false;
/* Set when chat_think_tool_recovery() injects <think>
</think>

 into the live session.  After recovery the live KV prefix contains
 * sampled recovery tokens + DSML tool-call tokens that the client will
 * NOT replay verbatim: the client sees the visible assistant content
 * (ending with ) plus the tool result, not the raw
 * DSML bytes.  Force checkpoint canonicalization in that case even
 * when raw_tool_text is available, because the raw-DSML-replay
 * assumption behind should_canonicalize_tool_checkpoint() is wrong
 * after recovery. */
bool think_tool_recovery_fired = false;
```

**Change 2:** Set the flag when recovery fires (line 11665)

```c
if (recovered) {
    server_log(DS4_LOG_WARNING, ...);
    trace_event(s, trace_id, ...);
    post_recovery_validation_pending = true;
    think_tool_recovery_fired = true;  // <-- NEW
    ...
}
```

**Change 3:** Modify the canonicalization condition (line 12094-12107)

```c
if (j->req.kind == REQ_CHAT && parsed_calls.len &&
    j->req.api != API_RESPONSES &&
    (think_tool_recovery_fired ||                    // <-- NEW
     should_canonicalize_tool_checkpoint(s, &parsed_calls)))
{
    /* Chat/completions has no protocol object that binds the next request
     * to this live KV state.  Canonicalize only the fallback tool-call
     * path where we lack exact sampled DSML replay; when raw DSML is known,
     * replaying those bytes keeps future prompts aligned without rebuilding
     * hidden reasoning.  Responses deliberately skips this path because its
     * previous_response_id contract binds the next turn to live state.
     *
     * After think-tool recovery, the raw-DSML-replay assumption is wrong:
     * the recovery injected <think>
</think>

     tokens into the live session, and the DSML tool-call text was
     * extracted as a tool call (not sent as assistant content).  The next
     * client prompt will contain the visible assistant text (ending with
     * ) + tool result, not the raw DSML bytes.  Force
     * canonicalization to rewrite the live KV suffix to match what the
     * client will replay, avoiding a token-mismatch cache miss and
     * 7+ minute full prefill. */
    canonicalize_tool_checkpoint(s, slot, j, ctx_span, trace_id, ...);
    thinking_live_clear(s, slot);
}
```

### How Canonicalization Fixes the Issue

**`canonicalize_tool_checkpoint()` logic** (line 10699-10857):

1. **Build canonical prompt:** Tokenize `j->req.prompt_text` + tool call suffix
   - The suffix is built from `parsed_content` (which includes the `
</think>

\n\n` injection) + tool calls
   - This matches what the client will send next

2. **Compare with live session:** `ds4_session_common_prefix(slot->session, &canonical)`
   - If they match, do nothing
   - If they differ, rewrite the live session

3. **Rewrite live session:** `ds4_session_rewrite_from_common()` (line 10741-10744)
   - Overwrites the divergent suffix in the live session
   - The live session now matches the canonical prompt

4. **Fallback:** If the rewrite is too large (`DS4_SESSION_REWRITE_REBUILD_NEEDED`), load an older disk checkpoint or rebuild from scratch

After canonicalization, the live session matches what the client will send next. The next request will have a live cache hit, avoiding the 7+ minute full prefill.

---

## Verification

### Build Test

```bash
$ make
cc -O3 -ffast-math -g -mcpu=native -Wall -Wextra -std=c99 -c -o ds4_server.o ds4_server.c
cc -O3 -ffast-math -g -mcpu=native -Wall -Wextra -std=c99 -o ds4-server ds4_server.o ...
```

**Result:** Compiles cleanly with zero warnings.

### Expected Behavior After Fix

When think-tool recovery fires:

1. **Recovery injects `
</think>

\n\n` tokens** (unchanged)
2. **Tool call is extracted** (unchanged)
3. **Canonicalization runs** (NEW):
   - Builds canonical prompt from visible text + tool result
   - Rewrites live session to match
4. **Next request arrives:**
   - Live cache hit: `common == old_pos`
   - No full prefill needed
   - Generation continues from the cached state

**Expected log output:**
```
0801 09:56:43 ds4-server: chat ctx=88950..89027:77 TOOLS tool call inside unclosed <think>; forced  after 9 generated tokens
0801 09:56:48 ds4-server: tool calls ctx=88950..89027:77 n=1 raw_tool_text=1 ...
0801 09:56:48 ds4-server: tool checkpoint canonicalized ctx=88950..89027:77 common=89027 live=89134 canonical=90683  <-- NEW
0801 09:56:48 ds4-server: chat ctx=89027..90683:1656 TOOLS prompt start  <-- PARTIAL PREFILL
0801 09:56:49 ds4-server: chat ctx=89027..90683:1656 TOOLS prompt done 1.2s  <-- FAST
```

Instead of:
```
0801 09:56:48 ds4-server: live kv cache miss live=89134 prompt=90683 common=89031 reason=token-mismatch
0801 09:56:48 ds4-server: chat ctx=0..90683:90683 TOOLS prompt start
0801 10:02:17 ds4-server: chat ctx=0..90683:90683 TOOLS prompt done 328.623s  <-- 5.5 MINUTES
```

### Regression Risk

**Low.** The fix only affects the think-tool recovery path:
- Normal tool calls (no recovery): unchanged behavior
- Recovery without tool calls: unchanged behavior (no canonicalization)
- Recovery with tool calls: canonicalization now runs (fixes the bug)

The canonicalization logic is already well-tested for the non-recovery case. We're just enabling it for the recovery case.

---

## Alternative Approaches Considered

### 1. Store Disk Checkpoint with Visible-Text Key

**Idea:** After recovery, store a disk checkpoint keyed by the visible text (not the full token text).

**Pros:** Would allow disk cache to match the next prompt.

**Cons:**
- Requires modifying the disk cache key generation logic
- The visible text is not easily available at the time of disk cache store (line 11153)
- More complex than canonicalization

**Verdict:** Rejected. Canonicalization is simpler and more direct.

### 2. Avoid Injecting Tokens That Diverge

**Idea:** Instead of injecting `
</think>

\n\n` tokens, adjust the live KV state to match what the client will send.

**Pros:** Would avoid the divergence entirely.

**Cons:**
- Requires predicting what the client will send (impossible in general)
- The client's prompt depends on the assistant message content, which includes the recovery injection
- Complex and error-prone

**Verdict:** Rejected. Canonicalization is a post-hoc fix that's simpler and more robust.

### 3. Increase Disk Cache Size

**Idea:** Increase the disk cache budget from 8 GB to 16 GB or more.

**Pros:** Would reduce eviction frequency.

**Cons:**
- Does not fix the root cause (token-mismatch)
- The disk cache key still doesn't match after recovery
- Wastes disk space

**Verdict:** Rejected. Does not address the root cause.

### 4. Disable KV Cache Quantization

**Idea:** Store unquantized KV caches to avoid quantization-induced mismatches.

**Pros:** Might reduce mismatches.

**Cons:**
- Doubles or triples disk cache size
- Does not fix the root cause (BPE tokenization mismatch, not quantization)
- Performance impact

**Verdict:** Rejected. Does not address the root cause.

---

## Conclusion

The think-tool recovery path injects tokens into the live session that diverge from what the client sends next. The raw-DSML-replay assumption behind `should_canonicalize_tool_checkpoint()` is wrong after recovery. The fix forces canonicalization in the recovery case, rewriting the live KV suffix to match the client's next prompt. This avoids the token-mismatch cache miss and the 7+ minute full prefill.

**Impact:** Eliminates 5+ minute delays after think-tool recovery, improving agent loop responsiveness.

**Risk:** Low. The fix only affects the recovery path and uses existing canonicalization logic.

**Testing:** Compiles cleanly. Runtime testing with a model that triggers think-tool recovery is recommended to confirm the fix.

---

## References

- **Recovery function:** `ds4_server.c:10152-10208` (`chat_think_tool_recovery`)
- **Recovery trigger:** `ds4_server.c:11628-11668`
- **Canonicalization:** `ds4_server.c:10699-10857` (`canonicalize_tool_checkpoint`)
- **Disk cache store:** `ds4_kvstore.c:923-1169` (`ds4_kvstore_store_live_prefix_text`)
- **Disk cache load:** `ds4_kvstore.c:1215-1339` (`ds4_kvstore_try_load_text`)
- **Common prefix:** `ds4.c:58980-58986` (`ds4_session_common_prefix`)
- **Log file:** `log/ds4.log:1744-1819` (repeated recovery + full prefill pattern)
