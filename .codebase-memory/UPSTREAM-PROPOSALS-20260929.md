# Upstream proposals — drafted 2026-09-29, NOT sent

Both items below are in upstream code (`origin/main`), verified against it rather
than against the fork. Nothing here has been opened as a PR or an issue; this file
is the draft so the decision to send is a separate, deliberate act.

Standing constraint respected: `ds4-server.sh`, `auth_proxy.py` and the kvstore
disk layer are fork-only by decision and are NOT proposed upstream. Everything
below is in the generation and response paths of `ds4_server.c`, which are
upstream's own.

---

## Proposal 1 — PR: let an unterminated tool envelope join the existing recovery policy

**Target:** `ds4_server.c`, the `else` branch of the unterminated-tool-call
handler (upstream ~14357-14361, inside `generate_job_inner`).

**Title:** Unterminated tool call: defer to the parse-failure recovery instead of
erroring the turn

**Body:**

When the model opens a tool envelope in generated text and never closes it before
the stop token, this branch sets `finish = "error"`. That has a consequence that
looks unintentional: `parse_generated_message_for_response_for_syntax` refuses to
mark a result as recovered when `finish` is already `"error"` — correctly, since
it must not rewrite a genuine fault — so the `recovered_tool_parse_failure`
degrade path a few lines below becomes unreachable for exactly the case it was
written for.

The two routes therefore disagree. An envelope the parser cannot execute degrades
to assistant text with `finish_reason: "stop"`; an envelope that was never closed
returns `finish_reason: "error"`. Both are the same class of event: a model output
shape, not a server fault. An unclosed envelope is what a truncated or
rambling generation looks like, and the enclosing guard already excludes the cases
that ARE faults (`finish == "error"`, `finish == "length"`, client stop), while the
continuation route above still handles a failed session append.

Clients feel this differently per API, which is the practical argument:

- OpenAI-compatible: `finish_reason: "error"` on an HTTP 200 body. Agent loops that
  branch on `finish_reason` treat the turn as failed and typically retry or abort.
- Anthropic: worse than visible-and-wrong, it is invisible. `anthropic_stop_reason`
  maps everything except `tool_calls` and `length` to `end_turn`, so an errored turn
  arrives as a normal completion. See Proposal 2.
- Responses: `status: "failed"` with `error.code = server_error` and an item marked
  `incomplete`, for something the server did not fail at.

The change is to stop setting `finish` here and let the existing degrade path run:

```c
             } else {
-                finish = "error";
-                snprintf(err, sizeof(err), "unterminated tool call (%s; token=%d, generated=%d, limit=%d)",
-                         stop_detail, stop_token, completion, max_tokens);
+                /* An envelope the model opened and never closed is a model output
+                 * shape, not a server fault, and not an executable call.  Leave
+                 * finish alone so the parse-failure recovery below handles this the
+                 * same way it handles an envelope it cannot execute: setting
+                 * finish = "error" here makes the parser refuse to recover, which
+                 * is right for a genuine fault and wrong for this one.  Faults keep
+                 * their error - the enclosing guard excludes finish == "error" and
+                 * "length", and the continuation route above still reports a failed
+                 * session append. */
+                server_log(DS4_LOG_WARNING,
+                           "ds4-server: chat ctx=%s%s%s unterminated tool call; deferring to parse-failure recovery (%s; token=%d, generated=%d, limit=%d)",
+                           ctx_span,
+                           req_flags[0] ? " " : "",
+                           req_flags,
+                           stop_detail, stop_token, completion, max_tokens);
+                trace_event(s, trace_id,
+                            "unterminated tool call; deferring to parse-failure recovery");
             }
```

Resulting client-visible shape: `finish_reason: "stop"`, empty `err`, no
`tool_calls`, and content as the existing degrade path produces it. Note that
upstream's degrade does not strip the partial markup (the fork carries a
`strip_dsml_keep_prefix` helper from a later hardening commit), so upstream content
would still include the truncated envelope text. That is unchanged from today's
behaviour — today it is included AND reported as an error — so this PR does not
make content worse, it only stops mislabelling the turn. Proposal 1b is the
optional follow-up if you would rather the partial markup not reach the client.

**Suggested test:** a model-free case in the server unit group constructing
generated text with an opened-but-never-closed envelope and asserting the parse
result is `finish == "stop"` with zero calls and `recovered == true`. The fork
carries six such cases plus a stripped-text gate; the minimal upstream version is
the single envelope case and the "complete envelope is untouched" negative.

**Risk:** low. The branch is reached only when `saw_tool_start && !saw_tool_end`
and finish is neither error nor length and the client has not stopped, and the
continuation route is tried first. The one behaviour worth calling out: a turn
whose entire output was an unclosed envelope now yields empty-ish content with
`finish_reason: "stop"` instead of an error, which an agent loop may read as a
successful no-op turn. The WARNING line is the operator's signal.

**Provenance, for the commit message:** found while investigating a transient
`finish=error "unterminated tool call (stop token)"` at 239k context in production
(1 of 149 tool turns). The mechanism that makes it reachable is worth stating in
the PR: message content is rendered verbatim and `tokenize_rendered_chat_vocab`
maps structural strings to real structural token ids anywhere in the rendered text,
so content that quotes an envelope opener injects a real opener, and the model can
continue inside a call it believes is open. That input-side behaviour is a separate
question and this PR does not touch it.

---

## Proposal 1b — optional follow-up: keep the partial markup out of content

Only worth sending if the maintainer agrees the truncated envelope should not reach
the client. The fork's version truncates the generated buffer at the first envelope
opener after the last closed thinking block, trims trailing whitespace, then
re-parses; content becomes the text before the markup. It also needs the invariant
that the fork learned the hard way: when text has been stripped, the live session
still holds the dropped tokens, so no visible checkpoint or live protocol state may
be bound to that frontier, or a later request can be handed text it never rendered.
Upstream has less of that machinery (no visible-transcript checkpoints), so the
upstream version is smaller — but the Responses live-state path has the same shape
and would need the same guard.

---

## Proposal 2 — issue: Anthropic clients never see a failed turn

**Title:** `anthropic_stop_reason` maps `finish == "error"` to `end_turn`, so
server-side failures are invisible to Anthropic clients

**Body:**

```c
static const char *anthropic_stop_reason(const char *finish) {
    if (finish && !strcmp(finish, "tool_calls")) return "tool_use";
    if (finish && !strcmp(finish, "length")) return "max_tokens";
    return "end_turn";
}
```

Every other finish — including `"error"` — becomes `end_turn`. A turn that failed
(engine fault, failed session append, unterminated tool call, stream write failure)
is therefore indistinguishable from a normal completion on the Anthropic API, and
the error text is not surfaced anywhere in the response.

The OpenAI-compatible path is at least honest: `finish_reason: "error"` on a 200
body. The Responses path is more honest still: `status: "failed"` with
`error.code = server_error`.

Anthropic's `stop_reason` vocabulary has no error member, so this cannot be a
one-line mapping fix. Options worth a maintainer's opinion:

1. Surface the failure out-of-band, e.g. an error field on the response object or a
   `message_stop` accompanied by an error event, keeping `stop_reason: "end_turn"`.
2. Return an HTTP error for the classes that are genuinely server faults, while
   keeping model-output shapes (unterminated envelope) as completions — which is the
   distinction Proposal 1 draws.
3. Document the collapse explicitly, so an operator debugging a silent Anthropic
   failure knows to check the server log rather than the response.

Observed while investigating Proposal 1: with the unterminated branch setting
`finish = "error"`, an Anthropic client received the raw truncated markup as
ordinary assistant text and `stop_reason: "end_turn"`. Nothing in the response
indicated anything had gone wrong.

---

## Not proposed upstream

- Everything in the KV disk cache: v3 delta chaining, the tool-map trailer, the
  bootstrap index, retention and lineage. Fork-only by standing decision.
- The input-side structural-syntax question (content rendered verbatim, structural
  strings mapped to real token ids). It is genuine and it is upstream's code too,
  but any fix changes rendered text and therefore every cache key, so it needs a
  migration conversation rather than a PR. The fork now logs when content carries
  structural syntax; that frequency data is the precondition for having it.
- `strip_dsml_keep_prefix` and the qwen visible-checkpoint family: fork-specific.
