# TODO-TRIPWIRE-FOLLOWUP-20261103 - re-entry escalation (small, high-value)

> Plan: `.codebase-memory/PLAN-TRIPWIRE-FOLLOWUP-20261103.md` — includes a
> correctness trap found during planning: the shared `budget_force_count>3`
> disable path would RE-ENABLE thinking under suppression on the 4th
> re-open (valve must skip counting suppressed closes).

Owner: tripwire codeowner (commits 2fd684d, e94d055). Parent doc:
TODO-TRIPWIRE-20261102.md + TRIPWIRE-RESULTS-20261102.md.

## Observed live (Nov-03 00:32-00:40, production session)
One conversation re-entered the SAME loop after every force-close: 8 tripwire
closes in 9 minutes (7x period-447, then 1x period-127 - the loop MUTATED).
Force-close ends the span; it cannot un-prime a context that now contains
eight copies of the cycle. Per-close waste ~2.7-3.5k tokens x8, each with the
model re-looping within ~60s. The conversation was unrecoverable by closing.

## Requested escalation (sibling of an existing pattern in the same file)
Budget already has: "thinking budget %d enforced %d times without closing the
span; budget disabled for this request" (~ds4_server.c:16228) - i.e. the
budget gives UP on a pathological request. Mirror it for the tripwire:

  2nd tripwire close within one request  =>  suppress thinking ENTIRELY for
  the remainder of that request: from then on, force-close at the FIRST
  in-span token (or skip open-think acceptance the way a no-think request
  renders). Log once: "thinking loop tripwire: repeated re-entry (N closes,
  period P); thinking suppressed for remainder of request".

Design notes:
- Count tripwire closes in the same per-request state that already tracks
  tw_total/tw_period; threshold 2 (third occurrence is always too late).
- Suppression should be per-REQUEST, not per-session/conversation (fresh
  request = fresh context = clean slate - matches how the user must escape
  manually today: start a new conversation).
- Reuse the existing close path (wrap-up + close) - only the trigger changes.
- Usage sidecar: extend thinking_tripwire report object with
  {"closes":N,"suppressed":bool} so clients can display it.
- Same scope discipline as the parent: ds4_server.c only; off = byte-identical
  (threshold unreachable when tripwire not armed); window only for the
  ./ds4_test battery; capture unaffected (labels stay engine-emitted).

## Corpus-side status (already done, no action needed here)
Spans are logged per close (TRIP_RE) and pid-attributed in
trainer/LOOP-QUARANTINE json: repeated re-entries are each excised at bank -
corpus is protected; this TODO saves the COMPUTE and the USER'S SESSION.

## Log-format ask (small, helps the corpus side)
Tripwire close lines currently omit the request's gen count, so offline span
geometry must be recovered from periodic 'ctx=...gen=N' progress lines
(implemented, dspark/loops.py v3) or widened conservatively. If the trip
line gains '(gen=%d)' like the budget line has (completion var is in scope,
ds4_server.c ~16286), spans become exact with zero recovery logic.

## Evidence artifact (moved Nov-03 02:3x)Full transcript of the re-entry session (8 tripwire closes, period 447 x7 ->
127 mutation, re-loop every ~60s, user had to abandon the conversation):
.codebase-memory/baseline-thinking-20261102/dbwal-val-conversation.json
(raw client session text - now .gitignore-covered by '*-conversation.json',
NEVER commit; kept locally as the motivating case for this escalation).

Effort estimate: ~10-20 lines + 1 unit test + the existing E2E loop-inducing
fixture adapted to trigger twice and assert suppression on the third span.
