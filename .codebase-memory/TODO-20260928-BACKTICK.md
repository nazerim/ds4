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
