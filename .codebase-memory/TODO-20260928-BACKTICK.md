# TODO 2026-09-28 — ds4-server robustness: literal structural syntax in content ("backtick problem")

Handoff from the KV-cache session (see HANDOVER-20260927.md for repo/engine
state; pi-side drift findings in
/Users/naz/Projects/PiScratch/DS4-CACHE-DRIFT-FINDINGS.md). Scope here: the
SERVER side. Not urgent, but real: any session whose content quotes
harness/protocol syntax (drift investigations, prompt-engineering work,
docs about tool calls) exercises these paths.

## Problem class

Message content (user text, tool results, assistant thinking/output) that
contains LITERAL structural syntax — tool-call markup blocks, chatml special
tokens, vision marker bytes, markdown code fences/backticks quoting any of
these — must never be confusable with real structure, in either direction:

1. INPUT: rendering a request whose content contains special-token strings.
   If the tokenizer maps them to structural token IDs (rather than inert
   byte tokens), content can inject fake structure (parse confusion; and a
   cache-key collision vector: two different conversations rendering to the
   same token stream).
2. OUTPUT: the DSML/tool-call parser scanning model output. Quoted markup
   echoed in text/thinking could trigger false tool-call detection or
   swallow real terminators.
3. HISTORY: rendering past tool calls back into prompts. This is where the
   pi drift findings live (args re-serialization); ds4's own renderer must
   be a pure function of stored bytes.

## Observed/adjacent events (evidence, not proof)

- One transient `finish=error "unterminated tool call (stop token)"` at
  239k ctx (1 of 149 tool turns, 2026-09-27 19:52). Unproven whether quoted
  markup contributed or it was a plain model flake. If reproducible under
  quoted-markup content, it moves from flake to bug.
- Live demonstration of the class on the opencode side (same evening):
  assistant text quoting tool-markup fragments was parsed as real tool
  calls by the client harness, twice. Clients vary; ds4 must not add to
  the problem.
- Store-key forensics show rendered history contains full tool-call markup
  blocks (function/parameter style); investigations quoting them put
  markup-in-content into both prompts and model outputs.

## Audit targets (start points, not exhaustive)

- Output parsing: DSML tracker state machine in ds4_server.c generation
  loop (search: dsml_decode_state, saw_tool_start, "unterminated tool
  call", DSML_START/DSML_END flags in log_flags). Note log line "DSML
  token not found in vocab; suppression disabled" — understand what
  suppression exists and what happens without it.
- Tool extraction: raw_tool_text paths ("raw_tool_text=1" log), tool-call
  id/name parsing, build_qwen_tool_turn_visible_text (~13732 region).
- Input rendering: chatml render of messages (search: im_start render /
  request prompt assembly); tokenizer special-token policy in ds4.c for
  qwen4 + deepseek families (do special-token strings in content become
  structural IDs?).
- Vision markers: \x1e..\x1f DS4_IMAGE marker bytes appearing in content —
  canonicalize_image_marker / ds4_normalize_image_text robustness when
  marker-like bytes are user content, not image placeholders.
- Cache-key safety: whatever the parse decisions are, confirm two distinct
  conversations can't collide to one token stream via injected structure.

## Repro recipe

Synthetic probes against the live engine (pattern proven in the KV work;
engine on :8002, loopback, no auth; see HANDOVER-20260927.md for start
commands and the probe script shape in git history / log archives):

1. User content containing: a fenced code block quoting tool-call markup;
   bare special-token strings; marker-like control bytes.
2. Ask the model to echo them verbatim; watch DSML flags, tool extraction,
   finish reasons, and the rendered prompt in log/ds4-qwen.trace
   ("--- rendered prompt ---" section shows exactly what the tokenizer saw).
3. Compare token streams: same content sent as text vs as structural
   elements — divergence point = the escaping policy (or absence).

## Constraints (IMPORTANT)

- Any change to prompt rendering changes cache keys: existing checkpoint
  lineages (500+ GiB on the blade, user's live conversations) become
  unreachable → one-time full-rebuild cost per conversation. Gate changes
  carefully; prefer output-parse fixes (no key impact) over input-render
  changes; if input rendering must change, plan the migration note.
- C11, no C++; tests: make test / ds4_test --server has tool-parsing
  coverage; add quoted-markup cases there.
- Check antirez/ds4 upstream issues first — markup-in-content robustness
  is generic and possibly upstreamable (unlike the disk-KV stack, which is
  fork-only by standing decision).
- Server is production (user's pi sessions run against it nightly):
  coordinate restarts; never run two engines; model tests only with the
  server stopped.

## Definition of done

- Documented escaping/quoting policy for structural syntax in content
  (input render + output parse), with test coverage for quoted markup,
  special tokens, and marker bytes.
- Verdict on the 19:52 unterminated-tool-call class: flake vs triggered;
  if triggered, fix + regression test.
- No cache-key churn without an explicit migration note.

## AUDIT RESULTS 2026-09-28 ~03:00 (overnight autonomous pass)

### Policy inventory: what already exists
1. Tool-argument bodies ARE escaped in both directions.  On render,
   append_dsml_parameter_text and append_glm_tag_body_text entity-escape any
   byte that would open the closing wrapper (plus ampersands); on parse,
   ds4_tool_text_unescape reverses it.  Covered by
   test_tool_body_escape_round_trip and test_tool_control_text_inside_arguments.
2. Output scanning for control markers skips wrapper regions:
   find_tool_structural_text ("tool arguments are literal data, including
   protocol-looking text").  Used for the thinking closer and the tool
   envelope closers across all three parser families (deepseek/ds41, glm,
   qwen).
3. INPUT rendering has NO policy.  Message content is appended verbatim --
   append_trimmed_text only trims leading/trailing whitespace, and no
   sanitizer symbol exists anywhere in ds4_server.c.  On the encode side,
   ds4.c tokenize_rendered_chat_vocab walks the entire rendered text and maps
   27 structural strings to real structural token ids through
   special_token_at (ds4.c ~43575), with no context awareness.  Those ids are
   vocab lookups of the same strings the renderer emits for real structure.

So the asymmetry is the finding: argument values are carefully escaped both
ways, message content is not escaped at all.

### Confirmed live (evidence, not inference)
Probe against the running engine (8 tiny requests, content carrying one
structural marker each versus an inert same-length control; script shape:
rendered-prompt sections of log/ds4-qwen.trace inspected by count only).
Result: the injected markers appear in the rendered prompt alongside the
renderer's own -- a system+user request renders two role-end markers, and the
injected case shows three.  Content-borne structure therefore reaches the
token stream as real structure.
prompt_tokens deltas were 0 or -1.  That is NOT weak evidence against the
finding; it confirms the sharper form of it: because the structural ids are
vocab lookups of the same strings, BPE and structural mapping yield the same
id, so content-borne structure is token-IDENTICAL to real structure and
indistinguishable to the model.

### Cache-key assessment (corrects the concern in the handoff above)
Keys are the sha1 of the rendered TEXT, so two distinct conversations cannot
collide onto one key, and the mapping being a deterministic text-to-token
function leaves cache consistency intact.  This is therefore NOT a KV
correctness bug and fixing it needs no KV format change.  The real exposure is
injection, not collision.

### Verdict on the 19:52 unterminated-tool-call event
Mechanism is now specific and plausible: content quoting a tool envelope
opener injects a real structural opener into the model's view of the prompt;
the model can then continue inside what it believes is a tool call, and the
decode tracker can reach the stop token with an unclosed envelope, which is
exactly the logged error.  NOT PROVEN for that single event (1 of 149 tool
turns, content not preserved).  A repro is now cheap to build: one request
whose user content quotes an envelope opener, a long generation, then watch
the DSML flags and finish reason in the trace.

### Options (decision needed; gated by the no-churn rule above)
- Option A - output-side hardening, ZERO cache-key impact.  Make an envelope
  opener that is never closed by end-of-generation degrade to inert text
  instead of erroring the turn, and extend the quoted-markup inertness that
  find_tool_structural_text already provides for argument wrappers to
  free text and thinking.  Recommended first move.
- Option B - input-side neutralization.  Escape structural strings inside
  message CONTENT at render time using the entity scheme already applied to
  argument bodies, so the tokenizer can never see them as structure.  This
  changes rendered text, so every lineage whose content contains such strings
  becomes unreachable: with ~378 GiB on the blade and live pi/opencode
  sessions, that is a one-time full rebuild per affected conversation.  If
  pursued, implement behind an env flag defaulting to today's behaviour and
  flip it only alongside a planned purge.
- Option C - document and accept.  Content-borne structure stays
  indistinguishable; rely on Option A for robustness.

### Next concrete steps
1. Model-free tests pinning CURRENT input behaviour (content containing each
   structural string renders verbatim), so an accidental change is caught and
   the gap stays documented rather than folklore.
2. Build the unterminated-tool-call repro, then implement Option A with that
   repro as the failing test.
3. Take Option B versus C to the user; B is a policy decision about injection
   risk versus a cache rebuild, not an engineering question.

Note on harness-side spillover: while running this pass, quoted tool markup in
assistant prose broke the opencode session twice (parsed as live tool calls)
and a stray code-fence character terminated a turn.  That is the client-side
face of the same problem class and is tracked separately in PiScratch;
nothing here changes ds4 behaviour for it.

## Follow-on 2026-09-28 18:20 — prompt-head churn costs KV ladder depth (pi-side)
A live linode-agent conversation (418978 tokens) lost resume depth and re-rooted
at 40960 after an agent to subagent to agent round trip, forcing a ~378k-token
re-prefill. NOT a ds4 fault: the first divergent byte (159188) is inside the
block pi's tool-manifest extension appends after the cwd element, and the
parent-lineage checkpoint written after the dive has no manifest at all where
the 16:09 checkpoint had one. Chain integrity was clean (110 chained files, zero
orphans, no warnings), and 40960 is simply the deepest rung whose stored bytes
end before the divergence.
Mechanism: renderToolManifest returns null when the registry reports neither
active built-ins nor guidelines, so a resume can silently drop the block; the
listing is also derived from the live registry, so a lane granted ls emits a
different manifest than its parent for the same conversation.
Handoff with measured variants, prioritised fixes (memoize the manifest on the
base prompt, reuse instead of dropping, head-stability warning) and a drafted
unapplied patch: /Users/naz/Projects/PiScratch/PROMPT-HEAD-STABILITY-20260928.md
and PROMPT-HEAD-STABILITY-patch.md
Operator rule until fixed: the head is conversation identity. Do mode switches,
extension-set changes, tool-config edits and AGENTS.md changes at conversation
boundaries, never mid-session. Diagnostic that makes this self-evident: start
the engine with DS4_KV_DEBUG=1 (logs reject reason plus first-divergent-byte).

## UPDATE 2026-09-29 ~01:00 — Option A IMPLEMENTED, plus the detector

Shipped in ds4_server.c. Design input: .codebase-memory/backtick-option-a-recon.md.

1. degrade_unterminated_tool_call() now holds the policy: an envelope the model
   opened and never closed is a model output shape, not a server fault and not an
   executable call. It strips the partial markup keeping the text before it, maps
   the finish through tool_parse_failure_recovery_finish (a true length stop
   survives, everything else becomes stop), clears the error, and optionally
   re-parses. The unterminated branch in the worker calls it instead of setting
   finish=error.
2. Why that was the whole bug: setting finish=error made the parser refuse to
   recover (it must not rewrite a genuine fault), which made the ALREADY SHIPPED
   degrade path unreachable. The two routes disagreed - one stripped the markup,
   the other returned it as content with finish_reason=error. Anthropic clients
   saw something worse: anthropic_stop_reason maps error to end_turn, so the
   failure was invisible and the markup arrived as ordinary assistant text.
3. turn_text_was_stripped() is one gate for both degrade routes, because the
   parse-failure route strips inline without setting any flag. When it is true the
   live session still holds the dropped tokens, so nothing may bind this turn's
   visible text to that frontier: gated at the Responses live remember, the
   thinking checkpoint remember, and the Anthropic live remember. Without that
   gate a later request could be handed phantom markup it never rendered.
4. log_structural_content_markers() warns once per request when message content
   carries any of 24 structural markers. Observability only, never a rewrite -
   pinned by a test asserting the content bytes come back unchanged, so it cannot
   become a sanitizer by accident. This is the data that decides Option B.

Review findings acted on (bounded review lane, verdict APPROVE-WITH-NITS):
- M1, gate the parsed_calls.len remember paths: done, via the derived predicate,
  which also covers the parse-failure route that a flag-only gate would miss.
- N2, extend the marker list from 9 to 24 (GLM arg wrappers, qwen function and
  parameter tags, DSML invoke and parameter openers and closers, and the closers
  of the singular envelopes): done. A close-only injection was invisible before.
- N3, reconcile require_thinking_closed between the repair parse (false) and the
  degrade call (think-mode dependent): the difference is deliberate and now
  documented - the repair path validates a candidate rewrite, the degrade path
  renders the final turn, so it must match what the client will replay.
- N4, cover the gate predicate: done, 7 cases.
- One review claim was wrong and is recorded so it is not re-litigated:
  anthropic_live_remember returns void, not bool, so no downstream consumer reads
  its result and the suggested consumer check does not exist.
- N1, WARNING is too loud for an expected condition: accepted as a risk for now.
  The line is the only signal that content-borne structure reached the model, and
  the point of this phase is to learn the frequency. Downgrade to INFO once the
  count is known; if it proves constant on agent traffic, gate it behind an env.

Deliberately NOT done:
- Option B (input-side neutralization). Still deferred, and the reason is sharper
  now: it must exempt replayed raw tool spans or it re-orphans every checkpoint,
  it changes what the model sees rather than only the cache key, and it would not
  have prevented the client-side breakage actually observed this week. The
  detector supplies the frequency data needed to revisit it.
- The 19:52 field event stays UNATTRIBUTED. The mechanism is confirmed possible
  and can no longer error a turn, but that single occurrence preserved no
  content. The new WARNING is what would identify a recurrence.
- Upstream: the error site is upstream code (origin/main ds4_server.c
  14313-14359, from upstream commit 759dd7c). Worth proposing the finish change
  alone upstream, since it removes a client-visible error for a model output
  shape; the strip helper is fork-only and must stay out of that patch.
  Separately reportable: the Anthropic error-to-end_turn mapping hides this whole
  class of failure from Anthropic clients.
