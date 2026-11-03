# WINDOW-PATCHES-20261103 — engine patch bundle for the next window (drafted live, engine untouched)

Drafted 2026-11-03 against live tree `f0a69ee` (PID 53508, Vision-Exp,
`--mtp-exact-sampling`, budget 16384, tripwire env volume 1200). This session
edited NO engine file. Bundle: `patches/01`, `patches/02` (engine, apply-in-
window) + `patches/03` (trainer, ALREADY APPLIED in place + verified — the
.patch is a record). Satisfies DSPARK-STATE-20261102 §6 QUEUE item 3.

## Verification status (done at draft time)

- Engine hunks line-verified against `f0a69ee`; `git apply --check` passes for
  01 and 02 independently against the live tree (regions disjoint).
- Sequential `patch -p1` 01 -> 02 onto a pristine copy reproduces the drafted
  final file BYTE-IDENTICALLY (`cmp` clean, patchlab run 17:1x).
- `cc -fsyntax-only -std=c99 -D_GNU_SOURCE -fno-finite-math-only -Wall
  -Wextra` on the final copy: ZERO warnings/errors, both prod shape and the
  `DS4_SERVER_TEST` include shape (mirrors tests/ds4_test.c). Full codegen is
  NOT possible from copies; the window still runs `make` + suites below.
- `git apply --check -R patches/03` passes => the record matches the live
  trainer state; `uv run pytest tests/` in trainer/: 23/23 green (incl. the
  two new/extended vis cases).

Apply order at window time: 01, then 02, then rebuild; 03 is already live.
Re-run `git apply --check` per patch first if the tree moved past f0a69ee.

---

## P0 — patch 01: visible-loop tripwire (engine: ds4_server.c, 2 hunks, +64 lines)

### Evidence
Nov-03 ~13:00-14:00 (PID 50920, temp-0.6 A/B): a ~55k-token VISIBLE DSML
tool-payload repeat loop ran to kill, log shape `chat ctx=... TOOLS
DSML_START decoding chunk=` with NO `THINKING` flag. The shipped tripwire is
structurally blind there: the ring push is gated on `was_thinking &&
thinking.inside && !budget_injected` (ds4_server.c:16512-16524), so visible
tokens never enter `tw_ring` (PLAN-TRIPWIRE-20261102 "Guards": "In-span-only
... tool/answer text repeating is never a trip" — by design, now a proven gap,
decision log #9). No budget covers it either (`thinking_span_tokens` is
thinking-only); the only ceiling was ctx room. Banked clean via LOOP-MANUAL
sidecar (9,017 recs) — the sidecar mechanism stays until this patch ships.

### Mechanism verified before designing (the visible decode path)
`server_generate` (single generate fn for chat/completion workers):
- `decode_again:` label 16125; per-pass locals declared after it (goto
  re-entry re-initializes them — all new vw state lives there too).
- main `while` 16197; per iteration: `dsml_state/in_tool_call` 16199-16201,
  sampling knobs 16205-16224 (`greedy_tool_syntax`, `payload_temperature`),
  budget/tripwire close block 16228-16354, injection consumption 16355-16358,
  sampling 16359-16368, spec/batched eval 16384-16441, `for ti` body 16452+
  (`token = toks[ti]` 16457, `piece`/`text` append 16469-16475,
  `was_thinking` 16476, `thinking_state_feed` 16477-16478 SKIPPED inside tool
  state — so `thinking.inside` stays false across the whole DSML payload:
  payload text IS a visible run, exactly the territory to watch),
  tw block 16492-16535, DSML tracker update 16536-16548, chunk log
  16686-16697 -> `log_decode_progress` 14146 (`THINKING` flag =
  `thinking.inside`, `DSML_START/END` = saw_tool_start/end, `log_flags`
  14129-14144).

### Insertion points (line numbers = pristine f0a69ee; hunks
`@@ -16171,6` and `@@ -16529,6`)
1. **State**, after `int tw_period = 0;` (16173, inside the tw state block
   16163-16180): `bool vw_on = j->req.thinking_tripwire; int vw_ring[3072];
   int vw_len/vw_since/vw_total/vw_period`.
   - `vw_on` arms off the SAME request/env resolution as `tw_on`
     (routes 4639-4644/4897/5967/6215: `thinking_enabled && (request-named
     bool | DS4_THINK_TRIPWIRE)`), so the documented per-request kill switch
     `"thinking_tripwire": false` and the env kill switch disable BOTH
     detectors, as specced. Explicit request-false beats env-on (named
     doctrine preserved).
   - `vw_on` is deliberately DECOUPLED from later `tw_on = false` flips
     (16252/16270/16294: vocab-lacks-close, >3-attempt valve, no-fit guard).
     An abort needs no close special — a thinking-side defeat must not blind
     the visible detector.
2. **Feed + detect + abort**, new `if (vw_on) {...}` block immediately after
   the `if (tw_on) {...}` block closes (16535) and before the DSML tracker
   update (16536), i.e. inside `for ti`, per KEPT token:
   - feed condition `!(thinking.inside || was_thinking || budget_injected)`;
     everything else RESETS len/since/total (open token, close token, in-span
     tokens, injected tokens) — ring purity mirrors tw_ring: only SAMPLED
     tokens strictly inside the current visible run; a run never arms across
     a thinking interruption; tool payload runs stay fed (feed skip at
     16477 makes them visible).
   - same 32-token cadence + 600 arm + `tripwire_period(vw_ring, vw_len,
     tw_volume)` — the shipped pure detector (14245-14262) and the shipped
     volume (request `tripwire_min_tokens` else env; production 1200) reused
     UNCHANGED (ledger #2 rule, #4 default — no contradiction; no wire fields
     added; #6 anchor-repeat NOT implemented, exact-period only).
   - **action = abort only**: `finish = "length"; stop_detail = "visible loop
     tripwire"; stop_decode = true; break;`. NO wrap-up/close injection —
     nothing is ever written into the answer channel. Downstream semantics
     are the EXISTING length-stop path: speculative boundary rewind runs at
     16731 (kept<ntok, non-error — proven path, same as the tool-end break
     16714-16718); incomplete-tool-call guard 16781-16784 skips repair /
     continue_after_invalid_dsml / degrade rewrite for length stops
     ("true length stop preserved"); usage/finish mappings need NO change
     (OpenAI `finish_reason:"length"`, Responses `incomplete/max_tokens`,
     Anthropic `max_tokens`); `should_remember_thinking_checkpoint` refuses
     length finishes (14722) so no checkpoint anchors a garbage frontier.
     `content_filter` was considered and REJECTED: it is not in this server's
     finish set (only length/stop/error/tool_calls; mappings 7351/9007/9307/
     9344/9633/9723) — inventing it widens the wire surface for zero
     client-visible benefit (agent clients already handle truncated
     length-stop payloads).

### Log line (abort fires from `for ti`, ctx_span + kind in scope — same as
16630's recovered-tool-call line)
```
ds4-server: %s ctx=%s visible loop tripwire: aborted generation at %d visible tokens (period %d) (gen=%d), no injection
```
`%s` = chat|completion; `%d` = vw_total, vw_period, completion — geometry
identical in shape to the TRIP lines 16325/16333 (ledger #5 `gen=` kept).
NEW phrase `visible loop tripwire` deliberately (NOT reusing "thinking loop
tripwire" — a close never happened; the trainer must be able to tell a
span-chop from a turn-abort). trace_event twin emitted.

### Trainer side (patch 03, already live)
`VIS_RE` + `parse_spans` branch in trainer/dspark/loops.py, span kind `"vis"`,
geometry `[B+G-T, B+G]` like trip. `file_exclusions` needs NO change:
pid-attributed kinds except "budget" already excise unconditionally — correct
for "vis" (the firing IS exact-cycle evidence AND the whole aborted turn is
junk, incl. its legitimate-looking head). Unattributed vis windows fall to
SPAN_WINDOW_DEPTH=8.0 (conservative). loops.py test suite: 23/23 incl.
`test_attributed_vis_span_unconditional_like_trip` and the extended
`test_log_parse_gen_geometry` vis line. `--audit` kinds counter shows
{'trip':..,'budget':..,'vis':..} automatically.

### Risks / coverage lines (documented, intended Tier-1 scope)
- FP class: LEGIT visible output sustaining >=1200 exact-periodic tokens at
  p in [16,512] (e.g. a huge JSON array of literally identical objects).
  Mitigation: abort-only (no injected garbage), per-request + env kill
  switches, volume knob raises the bar. The healthy-control study behind
  ledger #2/#4 measured 0 false-trip volume at 600 for thinking content;
  visible traffic has LESS legitimate exact repetition.
- Coverage gaps (unchanged scope lines): semantic/drift loops still NOT
  caught (ledger #6 stays blocked); p>512 cycles (ledger #9); requests with
  thinking DISABLED never arm (thinking_tripwire resolves false — routes'
  `thinking_enabled` gate); a drift-class payload runaway stays guard-blind
  with NO cap at all (visible has no budget analog) -> follow-up candidate:
  a visible-segment hard cap knob above legit-payload size (the 9k proper
  payload of decision #10 must not be touched), NOT shipped here.
- Stack: +12 KB (vw_ring) in the decode frame next to the existing tw_ring —
  within the reviewed budget (PLAN risk note: 3072 ints = ~12 KB, worker
  stacks >= 512 KB).
- Capture/era: fires should be ~0 on healthy exact-era traffic (like thinking
  trips on the two clean suites). Deploy = bank note: era suffix gains
  `+visible-tripwire`. LOOP-MANUAL sidecar remains the tool for pre-deploy
  hazards (day-12 already merged it).
- KV-disk cache: aborted partial payload can poison a storable prefix —
  already guarded by the observed self-heal path ("kv cache discarded ...
  corrupt text-prefix payload", DSPARK-STATE 08:33 watch item). No new code.

---

## P1 — patch 02: DS4_THINK_TEMPERATURE / TOP_P / MIN_P env hooks (ds4_server.c, 5 hunks)

### Goal & rationale
Change the thinking-regime defaults at LAUNCH for sampling A/Bs without
rebuilds (today's temp-0.6 experiment needed one). Current facts: ds4.h:59-61
(DS4_DEFAULT_TEMPERATURE 1.0f / TOP_P 1.0f / MIN_P 0.05f), re-applied ONLY
for unset knobs inside `if (ds4_think_mode_enabled(...))` at 16209-16218
(explicit request values win via `*_set` flags). DECISION (settled, log #9):
temp 1.0 stands; these hooks are for FUTURE A/Bs, not to re-litigate 0.6.

### Insertion points
1. Helpers after `resolve_tripwire_volume` (1239-1244): `server_think_env_
   float(name, fallback, lo, hi)` — strict whole-string `strtof`, hex
   explicitly rejected ("0x10" doctrine, mirrors 1186-1194), `isfinite` +
   range gate, garbage/absent = compiled default — plus cached globals
   `g_think_temperature/top_p/min_p` and `server_think_sampling_init()`.
2. Use sites: 16214/16216/16217 -> `g_think_*` (top_k stays 0).
3. `main()`: `server_think_sampling_init()` + banner BEFORE
   `int lfd = listen_on(...)` (18665; workers exist but serve nothing until
   accept). Exact banner format (strings-discipline line):
   `ds4-server: thinking sampling defaults temp=%.3f top_p=%.3f min_p=%.3f (env-overridable)`
4. `test_think_sampling_env()` after `test_tripwire_detector` (20927-21068)
   + registration at 28517.

### Design notes / risks
- PARSED EXACTLY ONCE (deliberate divergence from the fresh-read tripwire
  knobs): the block at 16209 executes per token BLOCK, and the #3975-analog
  audit (hoist comment 16133-16136) bans per-token getenv. Changing a value
  = restart. A/B ergonomics are unchanged (experiments restart anyway).
- SCOPE TRAP, documented at both sites: the thinking regime covers the WHOLE
  request (thinking AND visible tokens) while think mode is on — that is how
  temp 0.6 reached the visible payload in the first place. The patch does NOT
  change that semantics (decision #9 keeps the regime; P0 guards it).
  Env names say THINK_* because they bind to the think-mode regime, not to
  in-span tokens only.
- Ranges: temperature [0,10], top_p [0,1], min_p [0,1] (reject-out-of-range
  = fallback; no silent clamping). Non-thinking path untouched (request_init
  1025-1027 still uses the compiled DS4_DEFAULT_*). No request-field
  additions. Off-state (no env) is byte-parity: getters initialized to the
  same constants.
- Not exposed (v1): DS4_THINK_TOP_K (block hardcodes 0) — add only when an
  A/B needs it.

---

## P1 — patch 3: wrap-up injection hygiene review — CONCLUSION: NO CHANGE WARRANTED (n=1, cause most likely not the string)

Evidence assembled (all ds4_server.c unless noted):
- Injected text: `kThinkingCloseSeq = "\nI need to conclude my reasoning
  here.\n</think>\n"` (14196-14197); tokenized once, capped `cached_ids[24]`
  (14209); queued at 16296-16300, consumed 16355-16358, one token per while
  iteration through the NORMAL eval/feed/stream path — design comment
  14190-14195: stream splitters/tool gating/reasoning split behave exactly
  as after a natural close. It works as designed: every observed chop so far
  resumed clean visible output (17/17 dbwal fires answered coherently;
  Nov-03 live trips answered finish=stop post-chop).
- The wrap-up line is NOT stripped from delivered reasoning, session KV, or
  capture — by design (it rides the same path).
- The fixed string is LOAD-BEARING infrastructure: PLAN-TRIPWIRE-20261102
  EVIDENCE ADDENDUM (lines ~318-324): "Server fires are uniquely identified
  by the fixed wrap-up string ('conclude my reasoning'); any downstream
  counter must key on that, not on loop-mention phrases" — the model itself
  emits escape phrasings, so the string doubles as the server-event
  fingerprint in transcripts and in the corpus.
- Cost of changing it: (1) breaks the trainer identifier key + already-banked
  attribution; (2) shifts the capture distribution mid-era (injected tokens
  are recorded as generation targets — era discipline, DSPARK-STATE §1);
  (3) lengthening risks exceeding the 24-token `cached_ids` cap ->
  `cached_n=0` -> budget+tripwire SILENTLY DISABLE per vocab ("vocab lacks a
  thinking-close special" at 16256-16271) — a regression-class landmine.
- Differential evidence is n=1: the hygiene dock came from the ONE 16k-ride
  budget chop (decision #10 run-2); two full clean suites had zero chops and
  zero docks. Everything that co-varied with the injected text: (a) the span
  was truncated at 16k — the model had NOT self-decided to answer; the
  answer is composed cold at regime temperature right after a boilerplate
  line and `</think>`, with the task's format instructions ~16k tokens back;
  (b) the turn was the longest-thinking turn in the suite. The injected
  SENTENCE itself is one short in-distribution line the model already emits
  in its own escape attempts (low bleed risk); "suppressing injected chatter
  bleeding into answers" has no supporting artifact (the answer did not
  quote it — verdict was "proper result, hygiene docked", i.e. form, not
  echo).
=> Most probable cause: truncated-planning + position, NOT string choice.
   A more directive tail cannot fix "the model ran out of budget before it
   planned its answer"; and shipping it blind would pay the three costs
   above on 1 data point.

Measurement protocol FIRST (engine-free, existing knobs): termbench paired
A/B, same prompt set, n>=8 pairs — arm A natural (budget 16384), arm B
forced-chop via per-request `"thinking_budget": 256` + `thinking_tripwire:
false` (isolates the budget chop path). Score hygiene delta. Only if B
reproducibly docks: draft the string change as its own reviewed patch with
(1) new identifier string for analyzer keying, (2) tokenized length
verified <= 24 on BOTH vocabs, (3) era label + loops/atlas re-key, e.g.
tail: `"\nI must stop here and write the final answer now, in the exact
requested format.\n</think>\n"`. Reference-only; NOT in the apply set;
deliberately shipped as no .patch file so the window cannot apply it blind.

---

## Window runbook (mandatory, unchanged doctrine)

1. Engine STOPPED only when needed: `make ds4-server ds4_test` + `./ds4_test
   --server` coexist with the live engine (do those first as pre-stop gate);
   `ds4-server.sh stop` -> full `./ds4_test` -> restart.
2. Apply 01 then 02 (`git apply --check` each first), `make`, zero-warning
   requirement (the -Wall -Wextra draft check above is the floor).
3. strings discipline on the NEW binary (burned twice by lag, DSPARK-STATE
   §7): `strings ds4-server | grep -E "visible loop tripwire|DS4_THINK_
   TEMPERATURE|thinking sampling defaults"` all non-empty.
4. `./ds4_test --server` x2 (new test_think_sampling_env + unchanged tripwire
   set), full `./ds4_test` engine-down (pass/skip multiset diff vs
   ds4_test_followup.log), `make dspark-acceptance` manual run 4/4 vs
   Vision-Exp support.
5. Off-path parity gate: tripwire off -> byte-identical vs
   `baseline-thinking-20261102` artifacts (unchanged doctrine from the
   tripwire run).
6. DEPLOY per DSPARK-STATE decision #10: `restart-vision-dspark` **WITH
   `DSPARK_EXACT=1` in the command env** (plain restart drops `--dspark` =>
   zero capture + fp-change full re-prefill — burned 2x Nov-03), capture env
   (`TRAIN_DUMP=active`, `DS4_TD_PARITY` s3) present, `DS4_THINK_TRIPWIRE=1`,
   budget 16384, volume 1200; capture the active env + banner lines into the
   bank note; era label += `+visible-tripwire+think-env-sampling`.
7. Post-deploy checks: one termbench suite (expect ZERO vis/thinking trips,
   hygiene at par); `grep "visible loop tripwire" log/ds4.log*` watch; at
   first production fire decode the span and confirm exact-repeat class, and
   `python -m dspark.loops --audit` shows the `vis` span kind; the
   fp/banner checks from step 3 verified against the RUNNING PID (strings),
   not the source tree.
8. If the window slips: engine stays as-is (P0 gap persists — LOOP-MANUAL
   sidecar remains the quarantine path for visible runaways).
