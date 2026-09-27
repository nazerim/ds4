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
