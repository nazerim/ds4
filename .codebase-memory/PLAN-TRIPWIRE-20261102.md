# PLAN-TRIPWIRE-20261102 — thinking loop tripwire (Tier-1, plan only)

Companion to TODO-TRIPWIRE-20261102.md. Builds directly on committed budget
work (`5249d80`); reuses its force-close path verbatim. Anchors verified
in-tree 2026-11-02 post-commit. Engine currently on Qwen profile (PID from
18:53 restart); no window assumed.

## Shape: trigger-only patch on shipped machinery

Everything the budget already provides is REUSED unchanged:
- force-close injection (`thinking_close_tokens` + queue in the decode loop
  ~ds4_server.c:16000-16060, wrap-up + close through the accepted-token path)
- span tracking (`thinking_state` feed + `was_thinking` transitions in the
  `for ti` body)
- parser neighborhood (`parse_thinking_budget_value` ~1141-1160, four-route
  wiring pattern, env-default + named-value precedence)
- safety valves: vocab-lacks-close no-op, >3-attempt disable, fit-guard
  (room < close-sequence length -> span left as-is)

This TODO adds ONE detector + its wire surface. No ds4.c. Default OFF.

## Detector design (the only new mechanism)

State, per span (new local struct next to budget state after the
`thinking_state thinking` init ~16145):

```c
struct {
    int ring[2048];        /* in-span token ids, oldest dropped */
    int len;               /* valid entries in ring */
    int since_check;       /* tokens since last periodicity scan */
    bool tripped;          /* once per span */
} tw;
```

- **Feed**: push `toks[ti]` into the ring exactly when the token is
  strictly-interior AND sampled (same condition as the budget's
  `thinking_span_tokens++`: `was_thinking && thinking.inside &&
  !budget_injected`). Open/close/injected tokens never enter the ring —
  keeps history pure loop-content.
- **Reset**: on span open transition (`!was_thinking && thinking.inside`):
  `len=0; tripped=false;` — fresh arming per span, matching the committed
  per-span semantics (comment ~833).
- **Arm gate**: evaluate only after `len >= ARM_MIN` — **600** (revised from
  TODO's 300 by the corpus study below; see Parameter study).
- **Cadence**: scan every 32nd in-span token (`since_check` counter; scan
  cost is O(261k int cmp worst case => ~8k cmp/token amortized, negligible
  vs decode; TODO's "chunk boundary" made concrete). Detection lags at most
  +32 tokens — measured trips land at ~2.7k-3.7k span tokens (vs 8192
  budget cap), saving ~5k thinking tokens + ~2.2k draft-cycles of corpus
  noise per catch.
- **Test (REVISED by study — scaled cycles, not fixed 3)**: smallest p in
  [16, min(512, ring/2)] with `need = max(4, ceil(2400 / p))` and ring
  suffix of `need*p` tokens exactly periodic:
  `for i in 0..(need-1)*p-1: ring[L-1-i] == ring[L-1-i-p]`. Rationale: a
  loop is repetition that SUSTAINS a minimum token volume; finite legit
  repetition (3-row tables, short stutters) fails the volume requirement.
  A fixed 3-cycle rule trips ~15% of active healthy spans; the scaled rule
  trips 0% of days-3-8 healthy controls while still catching every
  budget-chop loop. Ring size 3072 (max compare window 2400+p ≤ 2912).

## Trigger + action (top of decode while-loop, budget trigger neighborhood)

Extend the existing exhaustion check to an OR — tripwire hit when
`tw_on && thinking.inside && !tw.tripped && period>0`. Both triggers feed
the SAME injection queue; on queue-up:
- set `tw.tripped = true` when it was the tripwire that fired;
- distinct log (trainer-parseable geometry — MUST match ENFORCE_RE shape
  `chat ctx=%s ...`):
  `ds4-server: chat ctx=%s thinking loop tripwire: closed span at %d tokens (period %d), injecting wrap-up + close`
- `trace_event` twin;
- budget race: evaluated first in the same block — whichever condition is
  true first queues the close; flags independent; `thinking_budget`
  untouched semantics when tripwire off.

## Wire surface (mirror the budget exactly)

- request struct (~832-841, right after budget fields):
  `bool thinking_tripwire; int tripwire_min_tokens;`
- fields (same parse neighborhood as `reasoning_budget_tokens`):
  - `"thinking_tripwire": true|false` (json_bool)
  - `"tripwire_min_tokens": int` — the SUSTAINED-VOLUME parameter
    (`min_loop_tokens` in the scaled rule): how many exact-periodic tokens
    must accumulate before a trip is allowed. Reuse
    `parse_thinking_budget_value` (NaN/Inf-reject), floor 600, 0/unset =
    default 2400. (Revised from the TODO's arm-min 300/floor-150 wording by
    the parameter study; internal arm gate stays fixed at 600.)
  - all four routes (chat/anthropic/responses/completions) for surface
    symmetry — TODO names OpenAI+Anthropic; approved.
- server defaults (same strict-fresh-read pattern as
  `server_thinking_budget_env`): `DS4_THINK_TRIPWIRE=1` (only "1" = on —
  anything else off), `DS4_TRIPWIRE_MIN_TOKENS=N` (strict strtol).
  Precedence: request > env; explicitly false request beats env-on
  (per-request opt-out, mirrors named-0 doctrine).
- usage sidecar: when a tripwire close happened, extend
  `completion_tokens_details` with `"thinking_tripwire":
  {"tokens_in_span":N,"period":P}` (new counter on request struct,
  set at trigger time; field ABSENT when tripwire never fires — off-path
  byte-for-byte). Anthropic usage unchanged (spec parity), same as budget.

## Guards (off-path byte-parity contract)

- All new runtime code gated `tw_on` (= request/env resolution); with no
  tripwire and no budget the decode loop runs the identical instruction
  sequence as commit `5249d80` (ring writes only when tw_on).
- Never-opened spans: ring never written (same transitions gate it) —
  grammar/no-think class no-op by construction.
- In-span-only: detector cannot see answer-channel repetition (the ti body
  push is inside the thinking-side branch) — tool/answer text repeating is
  never a trip.
- Reopen after a trip = NEW span, fresh arming (TODO's "do not re-arm for
  that span" reads as: the tripped span can't trip twice; the next span is
  a different span and gets its own ring reset) — confirm reading in Open
  item 1.

## Edit sites (all ds4_server.c)

| # | Site | Change |
|---|------|--------|
| 1 | ~832-841 request struct | + `thinking_tripwire`, `tripwire_min_tokens`, `tripwire_report_tokens/period` |
| 2 | ~1141-1160 helpers | + `server_tripwire_env_default()` (strict), reuse `parse_thinking_budget_value` |
| 3 | 4 route parsers (locals ~4377/4595/5604/5927; key branches near `reasoning_budget_tokens`; tails ~4554 etc.) | + two fields + resolve at tail with `thinking_enabled` gate |
| 4 | near `thinking_close_tokens` (~14010) | + `tripwire_period(const int *ring, int len)` pure function (unit-testable) |
| 5 | decode loop: after thinking init ~16145 | + tw state struct init |
| 6 | ti body ~16310 (counter block) | + ring push + reset on open (gated tw_on) |
| 7 | top-of-while trigger ~15985 | + tripwire condition feeding the existing queue; distinct log/trace |
| 8 | usage builders ~7537 (OpenAI; Responses symmetric) | + `thinking_tripwire` object, gated non-zero |
| 9 | tests (~20600 neighborhood) | parser tests + detector unit tests |

`tripwire_period` as a pure standalone function so the synthetic-stream
unit tests don't need an engine.

## Parameter study results (real corpus, 2026-11-02)

Method: reconstructed token streams from capture records (seed+labels
draft-cycle tiling; ~8% single-token dropout holes carried as unknowns and
EXCLUDED from equality tests with a >=50% both-known coverage requirement —
the live detector sees every token, so the live rule is the strict version).
Simulated detector variants against: all 8 LOOP-QUARANTINE segments,
164 banked healthy controls (days 3-8), and the unbanked `active/` file.

Provenance caveat: `active/train_pid32353.recs` is the CURRENT, unbanked
capture from the post-8192-era engine (PID started 18:53, budget default
active from binary deploy 18:36+). It was read while the engine writes it —
snapshot numbers, not a frozen corpus. The days 3-8 control result (0
false-trips on 164 healthy segments) does NOT depend on it.

1. Two loop species: EXACT-periodic (day1 p=388 @1.00 match, day9@27267
   p=422 @0.98) vs SEMANTIC (all 5 day2 segments + day9@85301 depth-187:
   best-lag match 0.17-0.25 — token-level repetition with varying content).
   No exact-token rule can catch the semantic class; the 8192 budget remains
   its cap. Tripwire's remit = the exact class (and the day9 exact loop is a
   210k-token run — the waste it removes is enormous).
2. The TODO's fixed 3-cycle rule trips 9/58 substantive healthy-class
   segments in ACTIVE — decoded text proves these are NOT false positives:
   p=392 repeats of verbatim "let me reconsider / think about it
   differently" re-derivation loops at runs ~8185 = spans that rode to the
   8192 budget chop. The depth>15 quarantine metric dilutes them below cut.
   So fixed-3cycle is not too aggressive on content — it is too eager on
   volume (3 cycles of a legit table would trip too; the active corpus just
   has no such samples).
3. Scaled-volume rule (`need = max(4, ceil(2400/p))` cycles): true loops
   still trip at 2720-2912 (day1/day9 exact — 3x earlier than budget,
   saving ~5k thinking tokens + ~2.2k draft-cycle corpus noise per chop,
   per era-day chop frequency); p=18 short stutters correctly suppressed at
   the volume floor; ALL active trips survive (they were real loops);
   days 3-8 healthy controls: 0/164 trips both ways. Rule shipped as spec.
4. Period distribution observed: 105-422 (quarantine set + active trips);
   p-range [16,512] retained, ring 3072 covers the 2400+p compare window.
5. Cadence 32 validated: +21-token worst lag vs earliest-theoretical trip.

Study scripts: tripwire_study.py / tripwire_v2.py / tripwire_v3.py /
td_decode.py + td_show.py (implementing session's tmp dir) — promote
revised copies into trainer/tools/ alongside the results doc at
implementation time so the parameter choice stays re-auditable.

## Tests / acceptance (updated for the scaled-volume rule)

Unit (no engine, in `./ds4_test --server` subset):
- detector (`tripwire_period` pure fn, scaled rule): sustained exact
  repetition reaching max(4,ceil(2400/p)) cycles trips with that p; p=8
  (below 16 floor) no trip; below-volume repetition (e.g. 3 cycles of
  p=400=1200 tokens < 2400) NO trip; the same pattern at p=300 with 8
  cycles trips; clean random-ish stream no trip; period drift (one token
  differs at cycle 2) no trip; pre-arm (<600 ring) never trips
- parser: tripwire bool on/off, volume floor 600 (below -> 600), named 0 =
  default-not-off (volume has no off semantic; OFF is `thinking_tripwire:
  false`), request-false beats env-true, env "1" only, garbage env = off
- golden regression fixtures: replay captured trip geometry (day1 p388 run
  -> trip 2720; day9 p422 -> 2912) as synthetic id streams, so the
  parameter choice stays pinned by tests

E2E (window; engine STOPPED for full ./ds4_test; --server coexists):
- loop-inducing prompt (verbatim-repetition task) on BOTH models with
  `thinking_tripwire:true`: close log fires at ~2.5-3.5k span tokens
  (volume-rule arithmetic), answer flows, finish clean, usage sidecar
  {tokens_in_span, period} present; log geometry must parse with an
  ENFORCE_RE-shaped regex (assert in test harness before merge)
- aggressive variant: `tripwire_min_tokens:600` -> trip in the ~700-1500
  range (proves the knob)
- same prompt tripwire false -> rides to budget 8192 (env) / max_tokens —
  proves independence of the two triggers
- tool traffic with tripwire on + loop prompt: tool payloads valid
- real-agent smoke: unbanked active era shows ~21 loop spans/day-class in
  pid32353 alone; first post-deploy day should show tripwire closes
  replacing most 8192-chops of the exact class (semantic class still
  chops at budget — expected, document in results)
- regression gate (THE headline): tripwire off everywhere -> live no-budget
  response byte-identical to the `baseline-thinking-20261102` artifacts;
  full ./ds4_test pass-set identical; `make dspark-acceptance` manual run
  4/4 output_match (Vision-Exp support per DSPARK-STATE note)

Corpus follow-up (post-first-era-day, not this patch):
- trainer/dspark/loops.py gains a second regex for the tripwire line
  (same [pos-tokens, pos] span geometry). Server PR description must flag
  it; do NOT fork the trainer here.
- `python -m dspark.loops --audit`: chop-log volume expected to drop for
  the exact class; ALSO propose (trainer side, theirs to decide) lowering
  the segment depth cut — the study showed real loops hiding at
  depth 4-5 in the dilution gap between MIN_PAIRS averaging and span-level
  truth. The tripwire log lines give them span-accurate markers.

## Window schedule (same discipline as budget run)

T0 baseline reuse: no re-capture needed — `resp-baseline.json` + shas in
MANIFEST are post-budget-5249d80-current; tripwire-off responses must match
THEM. Bank status: ask operator (last banked before budget run; engine is
live qwen since 18:53).
T1 stop -> T2 implement 1-9 -> T3 `make ds4-server ds4_test` +
`./ds4_test --server` -> T4 full `./ds4_test` -> T5 start qwen (profile in
use): E2E loop-prompt pair + tools + byte-parity curl vs baseline ->
T6 `restart` deepseek: same E2E subset -> T7 manual dspark acceptance ->
T8 results doc `TRIPWIRE-RESULTS-<date>.md`; no commits unless asked.

## Follow-up experiment (added 2026-11-03): escalation on repeat fires

Live evidence from the first production era (log/ds4.log 00:43-00:52): a
single deep agent conversation (ctx 70k->97k) tripwire-fired 8 times in 9
minutes, SEVEN consecutive fires at the SAME period (p=447, span
~2750-2910). The chop closes each turn but the loop core stays in the
replayed context, so it re-attaches turn after turn; by fire #8 a second
loop (p=127) had formed over the contaminated context. Notably, after each
chop the model answered coherently — once the thought-weight of the loop
is lifted it works normally, arguing the loop is PARTLY context
self-contamination (attention re-attaching to its own exact block), not a
model-level failure. The detector is doing its job; this experiment
attacks the recurrence.

Escalation ladder (per conversation, keyed by a session-level fires counter
+ loop-core fingerprint = hash of one detected period):
1. Second+ fire with the same fingerprint -> DO NOT re-inject the same
   template verbatim: rotate among N varied wrap-up wordings (the injected
   tail itself becomes context and can resonate with the recycled core;
   the close-tag special stays fixed — it's protocol).
2. Third+ -> escalate priority phrasing (short imperative: stop
   re-verifying, answer with what is established).
3. Fourth+ -> CONTEXT PRUNING: drop the recycled loop core from the
   reconstructed conversation so it cannot re-attach. Server-side/Tier-1
   shape only: reuse the existing prompt-rebuild + rewrite-from-common
   machinery (thinking-replay path), anchored on the fingerprinted span
   (tripwire log already gives [pos-tokens, pos] geometry).
   HARD constraints: tool-call id bindings of pruned turns must survive or
   be cleanly replayed; KV lineage handles the rebuild (cost bounded to
   the pruned suffix); capture stays untouched (pruning only reshapes
   FUTURE prompts, and it must be opt-in behind a knob until capture-
   distribution sign-off).
Success metric: fires-per-conversation drops from 8+ to <=2 for
fingerprint-recurring loops; the p=127 secondary-loop class should stop
forming.
Free testbed: this very conversation class is reproducing in production
traffic — measure before/next eras via the tripwire log lines.

EVIDENCE-VALIDATED ADDENDUM (2026-11-03, vs dbwal-val-conversation.json —
the preserved db-wal-recovery trial that produced the first production
fires; cross-checked 1:1 against log/ds4.log):

- 17 server fires (00:32:51 -> 00:52, minutes 5.7 -> 25.9, ~every 70 s)
  map exactly to 17 wrap-up-bearing reasoning parts (idx 15..33). Spans
  2745-4058 tokens; p=447 for 15/17 fires, stable across 20 minutes.
  Template chop NEVER broke the recurrence: 17 chops, 17 re-attachments.
  Pure repeated-template escalation is bounded at ~0 value on this class;
  ladder must escalate EARLY (analysis showed escape-after-1st-firing =
  2 steps of real tool probes, then relapse -> fire #2 is already the
  escalation point, not #4).
- The detector itself provides the fingerprint for free: at fire time the
  ring holds the exact p-token core (hash one period). Between-step
  recycling is paraphrase-grade early and CONVERGES to verbatim late
  (exact matches at min 12.9 and 25.9 = the two signature-carrying
  messages) — pruning those verbatim history cores attacks exactly the
  late-plateau re-attachment mechanism.
- Context contamination cost is measurable: prompt replay grew to
  14,403-token suffix at min ~16.9 (ctx 52.7k->67.1k) as cores piled up;
  trial verdict 0, death class 0-THINK-LOOP — the loop killed the reward,
  so this experiment's payoff is reward-direct, not just token waste.
- ANALYZER FIX on the preserve side: loop_analysis.detector_firings[3]
  captured the MODEL's own directives ("I need to stop the repetitive
  loop...") — self-interrupts, not server events. Server fires are
  uniquely identified by the fixed wrap-up string ("conclude my
  reasoning"); any downstream counter must key on that, not on
  loop-mention phrases (they also appear in genuine model escape
  attempts and are a signal of model-side awareness worth its own study).

## Open items — RESOLVED 2026-11-02

1. **Re-arm**: confirmed as read — a tripwire-closed span can't re-trip; a
   newly re-opened span gets fresh arming (matches committed budget
   per-span semantics).
2. **Routes**: all four (chat/anthropic/responses/completions) — approved.
3. **Server default**: SHIP ON via ds4-server.sh
   (`DS4_THINK_TRIPWIRE="${DS4_THINK_TRIPWIRE:-1}"`, the budget's
   :-default pattern), with a capture-era label comment (everything from
   that restart on is tripwire-era data for the trainer; loops.py second
   regex becomes load-bearing; era naming per DSPARK-STATE convention).
   Request-level explicit false = per-request kill switch. Usage-field
   absence when never-fired still keeps off-path responses byte-identical.
4. **Cadence / ARM_MIN / p-range**: decided from the real-loop study
   (tripwire_study.py vs the 8 LOOP-QUARANTINE segments + healthy day-3..8
   controls; wildcards over capture-dropout holes). Data appended under
   "Parameter study results" when the run lands.
5. **Window**: PLAN ONLY now — engine is running live jobs. No restart, no
   ./ds4_test. `make ds4-server` (compile-only) and `./ds4_test --server`
   (coexists) are allowed for code gates; first enforcement waits for an
   operator-granted window.

## Risks

- Known coverage limits (measured while E2E-testing, 2026-11-02):
  (a) loops whose cycle period exceeds the p<=512 window (e.g. full-table
  pass restarts at ~900+ tokens/pass) are NOT caught — 8192 budget remains
  their cap; corpus true loops measured p=105-422 so the window fits the
  pathology that actually shipped in data; do not silently widen without
  growing the ring (need*p <= 3072 bounds detectable p at volume floors).
  (b) loops with non-identical connective tissue between passes (model
  meta-commentary "But user wants...") break exact periodicity by
  construction — same semantic-class limit as (1) of the study. Both are
  documented, intended Tier-1 scope lines, not bugs.

- Detector false-positives: RESOLVED by the parameter study — the
  scaled-volume rule (max(4, ceil(2400/p)) cycles) produced 0 trips across
  164 banked healthy segments and every active-era trip was verified-loop
  by text decode. Residual risk is legitimate CONTENT with >2400 tokens of
  exact 105-422-token periodic structure inside thinking (none found in
  corpus audit). Usage sidecar + distinct log keep every trip auditable;
  `thinking_tripwire:false` per-request and env kill switch at process
  level. Shipping ON-by-default is backed by this evidence.
- `tripwire_period` worst-case scan is ~261k comparisons; if profiling
  shows per-chunk cost matters, iterate p descending with early-exit or
  check only p | (divisors of recent match) — not needed for v1.
- Ring lives in the job stack frame (3072 ints = ~12 KB, not the earlier
  ~8 KB estimate; review n1 corrected): check worker thread stacks are >=
  default 512 KB; budget patch already stacks toks[17]+close ids — fine.
- Log-geometry drift would silently un-tag corpus spans: E2E asserts the
  line parses with an ENFORCE_RE-shaped regex before merge.
