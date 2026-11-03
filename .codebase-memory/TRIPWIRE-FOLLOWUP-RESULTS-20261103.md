# TRIPWIRE-FOLLOWUP-RESULTS-20261103 — re-entry escalation: implementation + verification

Per PLAN-TRIPWIRE-FOLLOWUP-20261103.md. ds4_server.c only, +~70 lines incl.
tests & docs. Awaiting independent code review + independent regression +
commit (per GO pipeline instruction).

## Implemented (C1-C8)
- tw_closes / tw_suppress state, escalation at re-open after 2 tripwire
  closes, close-at-first-token suppression reusing the shared injection
  queue; once-only "repeated re-entry" log.
- Valve trap fixed as planned: suppressed closes never increment
  budget_force_count (short-circuit) so the disable path cannot
  re-enable thinking mid-suppression.
- Distinct suppressed-close log variant (stale-period avoided, R2) with
  TRIP_RE-compatible "closed span at N tokens" prefix; rate-limited
  (first 3, then every 25th) against re-open churn (R3).
- Detector close lines gained (gen=%d) (TODO log-format ask); sidecar
  gained "closes" + "suppressed" (both OpenAI + Responses shapes).
- decode_again re-init resets all four report fields incl. closes.

## Verification so far
- make: zero warnings (dead local removed after first pass flagged it).
- ./ds4_test --server x2: OK (extended sidecar shape tests:
  neither/budget-only/tw-only/closes+suppressed-true/both).
- Full ./ds4_test engine-down: rc=0 "ds4 tests: ok"
  (ds4_test_followup.log), pass/skip set unchanged.
- TRAINER COMPAT: loops.py TRIP_RE verified against live new-format line
  (captures (hi, tokens) exactly).
- LIVE TRIPS x2 (Nov-03 07:40/07:44, new binary): detector fired on
  dbwal-core replay prompts — (period 127) spans 920/1080 tokens,
  (gen=N) suffix present, sidecar {"tokens_in_span","period","closes":1,
  "suppressed":false} exact shape, model answered normally post-chop
  (finish=stop). The p=127 mutation class from the motivating session
  reproduced live.
- DBWAL TRANSCRIPT REPLAY: full 70-message conversation replay + continue
  directive -> model ESCAPED (coherent plan, finish=stop, zero trips) —
  notable data point re: self-contamination hypothesis (task-framing
  lift beats context priming).
- Suppression path (closes>=2 -> engage -> suppressed close): NOT yet
  observed live — post-chop in-request re-open refused to trigger under 4
  directive styles (models conclude instead). State machine is 5 guarded
  lines verified by code walk + sidecar/rate-limit units; first natural
  production re-entry exhibits observable via:
  grep -E "repeated re-entry|suppressed re-open" log/ds4*.log
  (dbwal-class sessions recur in agent traffic).

## Pipeline record (Nov-03 07:00-08:00) — COMPLETE
1. Implementation C1-C8 + valve fix + rate limiter -> build clean.
2. ./ds4_test --server x2 OK; full ./ds4_test engine-down rc=0 (pass/skip
   multiset identical to ds4_test_followup.log).
3. Live: 3 detector trips on the new binary (p127 spans 920/1080/696 at
   07:40/07:44/07:5x), new (gen=) line shape + extended sidecar exact
   {"tokens_in_span","period","closes","suppressed"}, model answered
   post-chop each time; dbwal full-transcript replay ESCAPED without
   tripping (task-framing lift beat context priming - data point for the
   pruning experiment).
4. INDEPENDENT CODE REVIEW (fresh agent): ship-it; 6/6 hard invariants
   verified (off-path byte parity, ring purity, monotone suppression,
   period-staleness handling, sidecar gating, rate-limiter boundaries).
   MINORs applied before commit: sidecar cross-span semantics documented
   (struct comment + close branch), "re-entry suppression lifted" suffix
   on the fit warning, engagement line prints the real close count,
   gen= added to the detector trace_event.
5. INDEPENDENT REGRESSION (fresh test agent, 8-point battery): 7 PASS +
   live-trip item PASS first try (TRIP_RE matched ('1102','696'));
   full-suite multiset diff EMPTY; parity classified against committed
   captures (reasoning_tokens field proven pre-existing). Evidence:
   TRIPWIRE-REGRESSION-EVIDENCE-20261103.md.
6. Engine left RUNNING on DeepSeek :8001 serving the final binary.
Suppression chain (closes>=2 -> engage -> suppressed close) has no live
sighting yet (in-request re-open refused to trigger across 5 attempt
styles); compensating controls: state-machine code walk + units +
production grep watch (dbwal-class recurrence ~daily).
