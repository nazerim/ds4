# PLAN-TRIPWIRE-FOLLOWUP-20261103 — re-entry escalation: per-request thinking suppression

Addresses TODO-TRIPWIRE-FOLLOWUP-20261103.md. ~20 lines in ds4_server.c
(decode-loop state machine + one log format touch + sidecar shape). No
ds4.c. Off = byte-identical. Anchors verified in-tree 2026-11-03 against
HEAD e94d055.

## Design (state machine, per decode pass — mirrors existing tw_* state block ~16159)

New locals next to `tw_tripped`:
```c
int  tw_closes = 0;            /* tripwire-triggered closes this request */
bool tw_suppress = false;      /* re-entry threshold reached: close at first token */
bool tw_suppress_engaged = false; /* sticky, for the usage sidecar */
```

Rules (three touch points):

1. **Count** — at each tripwire queue-success branch (~16281, where
   `tw_tripped = true` today): `tw_closes++`,
   `j->req.tripwire_report_closes = tw_closes`.
2. **Escalate on re-open** — ti-body re-open transition (~16448, where
   `tw_tripped = false` today) additionally:
   `if (tw_closes >= 2 && !tw_suppress) { tw_suppress = true;
   tw_suppress_engaged = true; }` — third span of the same request is
   where suppression starts (threshold 2 per TODO: "third is always too
   late" refers to giving up; we SUPPRESS, not give up).
3. **Suppress mode trigger** — in the push branch: when `tw_suppress`,
   set `tw_exhaust = true` on the FIRST in-span sampled token (bypasses
   arm-600/volume/period entirely). Trigger + injection are UNCHANGED
   machinery (`close_wanted` -> shared queue -> wrap-up + close). Each
   suppressed span burns 1 sampled token + the 11-token close sequence;
   the open-tag is never rewritten — engine-emitted tokens stay
   engine-emitted (capture invariant preserved).

Log once on escalation:
`ds4-server: %s ctx=%s thinking loop tripwire: repeated re-entry (2 closes, last period %d); thinking suppressed for remainder of request`

## Correctness trap found while planning — valve vs suppression (MUST FIX)

The shared `budget_force_count > 3` disable path (~16228) increments on
EVERY close-queue event and disables BOTH features at 4. Suppressed spans
are LEGITIMATE repeated closes (1 token + close each) — a model fighting
suppression would hit the valve on its 4th suppressed re-open and get its
thinking RE-ENABLED, the exact opposite of intent. Fix: do not increment
`budget_force_count` while `tw_suppress` is engaged (those closes always
work: the drain flips inside=false by construction — span was 1 token).
The valve keeps its original purpose (detecting drain-without-close).
Bounded safety: max_tokens still ends the loop-churn regime.

## Log-format ask (gen=)

Tripwire close line (~16286): append `(gen=%d)` before `, injecting
wrap-up + close` using the in-scope `completion` var (same shape as the
budget line). Verified compatible: trainer TRIP_RE (loops.py:46) has no
end-anchor and captures only `ctx hi` + `closed span at N tokens` —
adding `(gen=%d)` is additive; loops.py can later switch to reading it
directly and drop the PROGRESS_RE recovery pass (trainer side, theirs).

## Usage sidecar shape

`"thinking_tripwire":{"tokens_in_span":N,"period":P,"closes":N,
"suppressed":true|false}` — keys appended AFTER the existing two; emitted
only when the tripwire fired at least once (unchanged absence rule ->
off-path bytes untouched). Request-struct fields to add:
`tripwire_report_closes` (+ reuse existing tokens/period). Note: the
sidecar is 1 day old in production (2fd684d); shape extension is safe —
no external consumer pinned yet (loops.py parses LOG lines, not usage).

## Off-path byte-parity gate

- Tripwire not armed -> `tw_closes`/`tw_suppress` never set (all gated on
  tw_on) -> close_wanted/injection/valve truth tables unchanged.
- Armed-but-single-fire conversations -> identical responses except the
  tripwire line gains `(gen=N)` (log text) and the sidecar gains two keys
  (only when fired). Decode path unaffected.
- Budget-only and no-feature requests: byte-identical (compare against
  resp-committed-nobudget.json + budget 128 spotcheck artifacts).

## Edit sites (all ds4_server.c)

| # | Anchor (HEAD e94d055) | Change |
|---|---|---|
| 1 | request struct tripwire fields | + `int tripwire_report_closes` |
| 2 | ~16159-16166 tw state block | + tw_closes/tw_suppress/engaged + report reset line |
| 3 | ~16228 valve branch | skip increment while tw_suppress |
| 4 | ~16281 queue-success tw branch | tw_closes++, report_closes, suppression log (once) |
| 5 | ~16448 re-open reset branch | escalate tw_suppress at closes>=2 |
| 6 | push branch (~16452+) | `if (tw_suppress) tw_exhaust = true;` on first push |
| 7 | ~16286 tripwire log line | `+ (gen=%d)` |
| 8 | usage builders (OpenAI ~7700s, Responses ~9200s) | sidecar keys closes/suppressed |
| 9 | tests (~21000s) | extend test_tripwire_detector: sidecar shapes with new keys |

## Tests / acceptance

Unit (`./ds4_test --server`, engine up — coexists):
- sidecar 4-shape test extension (neither/budget-only/tripwire/with
  closes+suppressed)
- valve-suppress interaction is decode-loop runtime: NOT unit-testable
  engine-free -> covered by E2E + targeted simulation below

E2E (window; ./ds4_test full needs engine STOPPED):
- re-entry induction is the hard part: within ONE request the model must
  open -> get tripwired -> answer with another open-think (re-open) ->
  tripwire -> re-open a 3rd time. Best candidates: tool-heavy prompt with
  forced thinking around each call; verbatim-recitation prompt at temp 0
  (the fixedpoint/vision runs proved models usually answer after a close
  instead of re-opening). Attempt 3-4 prompt shapes; if none re-open twice
  in-request, the mechanism still ships: state machine is 5 lines and the
  suppression LOG line (`repeated re-entry`) is the observable — first
  natural production session that re-opens will exhibit it (grep watch).
  Cross-request dbwal replay is NOT the fixture (per-request semantics
  deliberately reset per conversation TODO note) but its plateau steps
  predict: after this patch, each new request still gets 2 real closes,
  then any same-request re-entry dies at ~13 tokens.
- regression: full ./ds4_test rc=0, pass/skip set unchanged; DeepSeek
  no-budget byte-identical vs resp-committed-nobudget.json; budget-128
  spotcheck unchanged; tripwire single-fire geometry unchanged except
  gen= suffix; TRIP_RE still matches new line shape (run
  `python -m dspark.loops --audit` scoped to today's logs and confirm
  span extraction unaffected).

## Window schedule

W0 survey done (this doc). W1 implement 1-9 + make. W2 ./ds4_test
--server (coexists, engine is DeepSeek now). W3 stop -> full ./ds4_test.
W4 start -> E2E attempts + regression curls + loops.py audit compat
check. W5 restore operator profile. No commits until asked (same
discipline as parent; review optional given size — recommend the
code-review lane anyway for the valve-interaction reasoning).

## Open items

1. Suppression mechanism = close-at-first-token (TODO primary, planned)
   vs skip-open-acceptance (rejected: rewrites protocol, capture-adjacent,
   more invasive).
2. `tw_closes` counts TRIPWIRE closes only (budget-enforced closes do not
   escalate to suppression) — matches TODO wording "2nd tripwire close";
   confirm.
3. No cap on suppressed re-opens within a request (max_tokens bounds
   churn). Alternative: after ~8 suppressed closes disable the tripwire
   itself and let budget/max_tokens finish the turn. Recommend: keep
   uncapped + rely on max_tokens; revisit if production shows churn.
4. Threshold 2 hard-coded vs knob (`thinking_tripwire: "escalate"` string
   forms) — keep hard-coded (TODO: threshold 2).

## Review findings (2026-11-03 adversarial pass on the plan itself)

R1 VALVE TRAP (was already in plan; confirmed as the critical one):
   shared budget_force_count>3 disables BOTH features; suppressed closes
   are legitimate repeated closes and would re-enable thinking at the 4th
   event. Fix: skip valve increment while tw_suppress engaged. Mixed
   budget+tripwire counting checked by table: budget close (1) + tw close
   (2) + suppress-engage on re-open + suppressed closes (skipped) — valve
   never trips spuriously; genuine drain-without-close still trips.
R2 STALE-PERIOD LOG BUG (new): suppressed closes bypass the detector, so
   `tw_period` at queue time holds the PREVIOUS span's period — the close
   line would print a misleading "(period 447)" for a span that had 1
   token. Fix: suppressed closes log a distinct variant
   "thinking loop tripwire: closed span at %d tokens (suppressed re-open)
   (gen=%d), injecting wrap-up + close" — TRIP_RE (loops.py:46) captures
   only "closed span at N tokens", prefix identical, trainer extraction
   unaffected; sidecar period keeps last-DETECTED value (documented
   meaning: "period of the last detector trip").
R3 LOG SPAM + CORPUS NOISE (new): a model fighting suppression burns
   ~12 completion tokens per close cycle, so worst case ~max_tokens/12
   closes = hundreds of log lines AND hundreds of TRIP_RE spans. Token
   churn is self-bounding (closes consume real completions; max_tokens
   ends it) and trainer span extraction skips them via its depth-of-window
   guard (13-token windows never reach min_pairs=300). Log side: rate-limit
   suppressed-close lines — always log the first 3, then every 25th
   (counter carried in tw_closes); engagement line itself logs once.
R4 RETRY RESET (new, small): the decode_again re-init block must reset
   j->req.tripwire_report_closes alongside tokens/period (added in step 2
   below); escalation state is per-pass by construction.
R5 E2E INDUCTION UNCERTAINTY (confirmed realistic): within-request
   re-open is the pattern openai_sse_stream_update's guard_second_reasoning
   already defends against — models DO re-open thinking mid-turn naturally,
   so the fixture has a real chance; attempt matrix in execution step E3.
   Honest fallback if no live double-close: mechanism ships on state-
   machine review + unit sidecar tests + production watch (the new
   "repeated re-entry" line is the observable; dbwal-class sessions
   recur ~daily in current traffic).
R6 FIT-GUARD AT SUPPRESSION TIME: room < close-seq disables both features
   (existing path) — acceptable: at that point the answer window is already
   gone; behavior equals pre-patch.
R7 ACCEPTED TRADE-OFF (per-request scope): a dbwal-class conversation
   still pays 2 full closes (~5.6k thinking tokens) in EACH new request;
   escalation resets per request by design (TODO: fresh request = clean
   slate). Session-level escalation (cross-request counter + fingerprint
   memory) is the PLAN-TRIPWIRE pruning experiment's territory, not this
   patch.
R8 BUDGET RE-CHOPS DO NOT ESCALATE: a looping request under a small named
   budget chops repeatedly without suppression (client asked for that
   budget; budget closes are legitimate, not pathology signals). Documented
   as future knob if production data demands.
R9 SUPPRESS-CLOSE BYPASS VERIFICATION: suppress sets tw_exhaust on first
   push; close_wanted evaluates top-of-NEXT while iteration — up to one
   spec block (<=17 tokens) of extra in-span tokens may land between push
   and queue. Bounded, matches normal-detector latency class; note for
   the E2E expectation (suppressed span ~1..17 tokens, sidecar
   tokens_in_span reflects it).

## Execution plan (step-by-step, pending GO)

Preconditions: engine DeepSeek running (restarts are operator-window
events; --server gates coexist). HEAD e94d055 + uncommitted ds4.c/script
WIP left untouched. No commits until asked.

C1 request struct: add `int tripwire_report_closes;` after
   tripwire_report_period (memset-zeroed by request_init).
C2 decode tw state block (~16159): add the three locals (tw_closes,
   tw_suppress, tw_suppress_engaged); extend the report-reset lines with
   tripwire_report_closes (R4).
C3 valve branch (~16228): `if (++budget_force_count > 3)` -> only when
   `!tw_suppress` (R1): wrap as `if (!tw_suppress && ++budget_force_count > 3)`.
   NOTE: short-circuit keeps increment skipped during suppression.
C4 queue-success tw branch (~16281):
   - `tw_closes++; j->req.tripwire_report_closes = tw_closes;`
   - if this close was suppress-triggered (flag captured at close_wanted
     evaluation: `bool suppress_close = by_tripwire && tw_suppress;`):
     rate-limited log per R3 (closes<=3 || closes%25==0), suppressed
     variant text per R2, no period in text.
   - else: existing line + `(gen=%d)` appended (completion in scope) —
     TRIP_RE still matches.
C5 re-open transition (~16448): after existing resets add
   `if (tw_closes >= 2 && !tw_suppress) { tw_suppress = true;
   tw_suppress_engaged = true; server_log(re-entry line, ctx_span, 2,
   tw_period); }`.
C6 push branch (~16452+): first statement inside the strictly-interior
   case: `if (tw_suppress) tw_exhaust = true;` (scan skipped automatically
   — existing gate requires !tw_exhaust).
C7 usage builders (OpenAI append_openai_usage_json; Responses twin):
   sidecar -> `"thinking_tripwire":{"tokens_in_span":%d,"period":%d,
   "closes":%d,"suppressed":%s}` using report_closes + engaged bool
   ("true"/"false"). Absence rule unchanged.
C8 tests (test_tripwire_detector usage-shape block): update the tw-only
   and both-cases asserts for the new key pair; add a closes=3/suppressed
   shape case. Detector goldens untouched.
C9 build gate: make ds4-server ds4_test; ./ds4_test --server (engine up).
C10 full gate: operator-window stop -> ./ds4_test rc=0 + pass/skip parity
   -> start.
E1 regression curls: (a) no-budget request vs resp-committed-nobudget.json
   byte-identical; (b) budget-128 spotcheck (128->128, gen line shape);
   (c) tripwire single-fire fixture if any earlier prompt cooperates —
   verify `(gen=N)` suffix + TRIP_RE match by running
   `python -m dspark.loops --audit --logs log/ds4.log*` scoped run and
   confirming span extraction counts unchanged+1.
E2 suppression attempt matrix (live, DeepSeek temp 0):
   m1: loop-inducing prompt + explicit "after concluding, open a NEW
   deliberation and re-check before answering" (guard_second_reasoning
   says re-opens happen naturally);
   m2: two-tool tool_choice:required + loop content (thinking gates each
   tool call -> multiple spans per request);
   m3: fixedpoint table prompt at volume 600 (known re-check loop, 14k
   tokens of thinking historically);
   m4: vision re-verification (e2e_vision_loop.py) with tripwire on.
   Success = 2 close lines + "repeated re-entry" engagement + a suppressed
   close line + sidecar closes>=3 suppressed:true, answer flows, finish
   clean. Any matrix hit is sufficient; log attempts honestly either way.
E3 (only if no E2 hit): leave shipped; production watch documented:
   `grep -E "repeated re-entry|suppressed re-open" log/ds4.log*`.
E4 engine restore per operator; results doc
   TRIPWIRE-FOLLOWUP-RESULTS-<date>.md; await commit/push instruction
   (review lane optional at this size — recommend code-review subagent
   pass focused on C3/C5/C6 interactions before commit).

Rollback: single revert commit (server-only; no ds4.c; no data migration;
log-shape change is additive; loops.py TRIP_RE forward/backward
compatible).
