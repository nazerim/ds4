# TODO-TRIPWIRE-20261102 - thinking loop tripwire (separate-agent handoff)

> Implementation plan (trigger-only patch on the shipped budget machinery,
> detector spec, window schedule, open items):
> `.codebase-memory/PLAN-TRIPWIRE-20261102.md`
> Implementation + verification log (shipped this session; detector shipped
> ON by default via ds4-server.sh):
> `.codebase-memory/TRIPWIRE-RESULTS-20261102.md`

## Goal
Detect a repetitive CYCLE inside a thinking span and force-close it early
(~few hundred tokens into the loop), instead of riding to the 8192-token
thinking budget. Continuation of the committed thinking-budget work
(`5249d80` server: hard thinking-token budget) - reuse its force-close
path verbatim; this TODO only adds the TRIGGER.

## Why (measured 2026-11-02)
- Production sees loops at ~200k ctx: four budget-chops in 90 min, model
  re-loops after every close (each chop wastes up to 8192 thinking tokens;
  ~2.2k draft-cycles of corpus noise per chop, mitigated post-hoc by
  trainer/dspark/loops.py span excision - a detector prevents what the
  filter currently mops up).
- Prior art: Strata `reasoning_loop_recovery: "stop"`. oMLX has no detector
  (budget only) - do not copy them; copy the budget close-path we already ship.

## Scope discipline (repo rules - same as TODO-THINKING-BUDGET-20261102.md)
- ds4_server.c ONLY (Tier-1 shape). No ds4.c sampler/Metal changes.
- Restart is window-bound (piggyback the next bank if possible).
- No commits unless explicitly asked.
- Default must be OFF (opt-in per request + opt-in server env). When off:
  byte-for-byte identical behavior (capture + regression batteries unchanged).
- Fidelity invariant: like the budget, the tripwire only FORCE-CLOSES the
  span (real engine-emitted close token) - never rewrites/penalizes sampling.

## Mechanism spec
1. Trigger (state machine per thinking span, fed accepted tokens):
   - Arm only after >= ARM_MIN tokens in-span (default 300; protects short
     legit cycles: counting lists, table rows).
   - Cycle test on the in-span token history: find period p in [16, 512]
     s.t. the last 2p tokens exactly equal the preceding 2p tokens, AND the
     match repeats >= 3 cycles total (i.e., suffix of length 3p has period p).
   - Evaluate at chunk boundaries (~50 tokens - the existing decode-chunk
     cadence), O(vocab-free): pure int array comparisons over the recent
     history ring (keep last ~1536 ids per span, trivial memory).
   - On hit: trip once per span (close -> do NOT re-arm for that span even if
     thinking reopens: reopening means the model already restarted a NEW
     span, which gets fresh arming - match the budget's per-span semantics,
     see committed budget code near the "force-closes each thinking span"
     comment ~ds4_server.c:833).
2. Action: call the SAME force-close path as the budget (wrap-up + close
   sequence + continue into answer). Log distinctly:
   "thinking loop tripwire: closed span at N tokens (period P)" - the log
   line is the corpus-side marker; trainer span-excision (dspark/loops.py
   ENFORCE_RE) should gain a second regex for tripwire closes (same
   [pos-tokens, pos] span geometry). Note it in the PR description; the
   loops.py change itself is ours to merge, do not fork the trainer.
3. Wire surface:
   - Request: "thinking_tripwire": true|false (OpenAI + Anthropic routes,
     same parser neighborhood as reasoning_budget_tokens ~1141-1160);
     optional "tripwire_min_tokens": int (>=150 floor).
   - Server default: DS4_THINK_TRIPWIRE=1 env + DS4_TRIPWIRE_MIN_TOKENS=N
     (unset = OFF; if you later enable via ds4-server.sh, mirror the
     budget's :-default pattern and label the capture era).
   - Reporting: extend thinking_budget_reported (usage sidecar from the
     budget commit) to cover tripwire-closed spans: {tokens_in_span, period}.
4. Interplay checks:
   - Budget vs tripwire race: whichever fires first closes the span; flags
     independent.
   - Tool traffic: tripwire must never touch answer-channel repetition
     (in-span=thinking only - the budget patch's span tracking already
     isolates this).
   - Grammar/no-think models: no-op by construction (never in-span).

## Tests / acceptance
- Unit: detector on synthetic id streams - periodic suffix trips at 3p,
  clean stream no-trip, short-cycle-under-ARM no-trip, near-miss (period
  drift) no-trip.
- E2E (window: engine STOPPED for ./ds4_test; --server subsets coexist):
  loop-inducing prompt (e.g. long verbatim repetition task) with
  tripwire+min 300 -> close logs at few-hundred span size, answer flows,
  finish_reason clean; same prompt tripwire OFF -> rides to budget 8192.
- Regression gate: tripwire off everywhere -> full suite + dspark acceptance
  identical (4/4 output_match), no-budget responses byte-identical to the
  thinking-budget baseline (reuse baseline-thinking-20261102 artifacts).
- Corpus check after first era day: active/ span-excision volume via
  `python -m dspark.loops --audit` should drop (chop logs shorter).

## Pointers
- Committed budget: git show 5249d80 --stat (412 lines ds4_server.c);
  force-close path + span tracking + usage field; parser neighborhood
  ~1141; request struct ~4286/832-841.
- Results/prior report: .codebase-memory/THINKING-BUDGET-RESULTS-20261102.md
  (test discipline used there - replicate it), PLAN-THINKING-BUDGET-20261102.md.
- Corpus side: trainer/dspark/loops.py (spans regex ENFORCE_RE ~line 35),
  LOOP-QUARANTINE json, DSPARK-STATE-20261102.md (era labels).
- Loop examples in data: day-9 depth 187x/108x, cap-era residue depth ~10x
  (what the tripwire will prevent: closes at 300-600 instead of 8192).
