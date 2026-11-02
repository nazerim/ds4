# TRIPWIRE-RESULTS-20261102 — thinking-loop tripwire: implementation + verification log

Deliverable of TODO-TRIPWIRE-20261102.md + PLAN-TRIPWIRE-20261102.md.
ds4_server.c only. Ships on top of committed budget 5249d80 (reuses its
force-close path verbatim; adds the loop detector + trigger).

## Feature surface

- Request fields (all four routes, like the budget):
  `"thinking_tripwire": true|false`, `"tripwire_min_tokens": int`
  (sustained-repetition volume; floor 600, default 2400).
- Server default: `DS4_THINK_TRIPWIRE=1` (only "1" enables) +
  `DS4_TRIPWIRE_MIN_TOKENS` (strict). Request-named false beats env-on
  (per-request kill switch). Thinking-off drops the tripwire.
- Detector: contiguous ring (3072) of strictly-inside-span SAMPLED token
  ids; arm 600; scan every 32 pushes; trips at smallest p in [16,512] whose
  exact repetition sustains `max(4, ceil(volume/p))` cycles
  (`tripwire_period`, pure fn, corpus-studied parameters).
- Action: reuses the budget injection (wrap-up line + close special through
  the accepted-token path). Whichever of budget/tripwire hits first closes
  the span; disable paths (vocab-lacks-close, >3-attempt valve, no-fit-room)
  turn BOTH off for the request — closing is exactly what they cannot do.
- Per-span semantics: trip once per span; re-opened span gets fresh arming
  (operator decision 1).
- Observability: log line `chat ctx=A..B:C thinking loop tripwire: closed
  span at N tokens (period P), injecting wrap-up + close` (ENFORCE_RE-shaped
  geometry for trainer span excision) + trace + usage
  `completion_tokens_details.thinking_tripwire:{tokens_in_span, period}`
  (and Responses output_tokens_details twin). Fields absent unless fired.
- Off-path: zero behavioral change when unset (all gates on tw_on); usage
  objects byte-identical unless the sidecar fires.

## Verification (window 2026-11-02, 23:0x -> 00:0x) — COMPLETE

- make ds4-server + ds4_test: clean, zero new warnings. ds4 CLI binary NOT
  relinked (server-only change) -> dspark-acceptance from the budget run
  remains valid on the unchanged ./ds4 (documented skip).
- ./ds4_test --server (engine up): OK incl. new test_tripwire_detector:
  study goldens p=388@2716-window trips / p=422 clean trips / drift blocks
  / below-volume 6x388 no trip / p=18 stutter needs 2412 tokens (study
  @48538 suppression) / under-arm / volume resolver floor+default+strict-env
  / DS4_THINK_TRIPWIRE "1"-only enablement.
- Full ./ds4_test (engine STOPPED, final binary): rc=0 "ds4 tests: ok"
  (ds4_test_tripwire.log), pass/skip set = pre-change parity.
- REGRESSION HEADLINE (final binary, budget 8192 + tripwire ON as server
  defaults): DeepSeek no-budget/no-field baseline request -> message AND
  usage byte-identical to resp-baseline.json (resp-tripwire-nobudget.json).
- Armed-detector zero-cost parity (Qwen): identical deterministic 120-line
  request with tripwire env OFF vs armed-ON-no-fire -> message byte-identical
  and usage identical (qwen-parity-ref-envoff.json vs live).
- Budget interplay live:
  * tripwire ON + budget 0 (named): budget sidecar correctly ABSENT, budget
    never chops (qwen unnumbered-120 run) -> independence proven.
  * tripwire ON + budget 8000 (DeepSeek): budget chop exact at 8000->8000
    with wrap-up + answer -> coexist path proven.
  * tripwire ON + budget 14000 (DeepSeek vision + fixedpoint runs): 14010
    chop exact; detector correctly SILENT (those loops were drift/pass>512)
    -> no false trip on near-miss classes live, matching the study's
    semantic-class limitation exactly.
- No-fire live battery: 9 diverse live requests (counting w/ numbering,
  verbatim w/ compression, recheck w/ temp-drift, vision, tools) -> detector
  fired 0, misfired 0, every response byte-stable; injection machinery
  exercised 3x live via the budget trigger (identical code path).
- First natural live trip: expected from production traffic (study measured
  ~21 exact-class loop spans per unbanked pid-file per day). Watch:
  `grep "thinking loop tripwire" log/ds4-qwen.log log/ds4.log`
- Engine restored to window-open state: Qwen3.8 profile, port 8002, both
  defaults live (DS4_THINKING_BUDGET_TOKENS=8192, DS4_THINK_TRIPWIRE=1) via
  ds4-server.sh; per-request overrides verified live.

## Synthetic-loop prompt learnings (why live-positive is deferred to prod)

Instruction-forced repetition escapes into: answer-channel (deepseek),
numbered lines (qwen), line compression (qwen), meta-commentary between
passes (both) — each defeats exact periodicity by construction; the real
corpus loops were spontaneous verification loops in deep vision sessions.
The detector's positive-side correctness is therefore evidenced by (1)
simulation of the IDENTICAL algorithm on the real captured loop streams
(day1 p388 trip@2720, day9 p422 trip@2912, active 21/21 true), (2) unit
goldens replaying those geometries, (3) 9 live near-miss non-fires.

## Files
- ds4_server.c: +~120 lines on top of budget (detector fn, tw state/push/
  scan, close_wanted merge, usage sidecar, parser wiring x4 routes, tests).
- ds4-server.sh: DS4_THINK_TRIPWIRE=1 default block with era label.
- Study scripts (tripwire_study/v2/v3.py, td_decode/td_show.py) + E2E
  artifacts in .codebase-memory/baseline-thinking-20261102/.

## Trainer/corpus interface notes (for the PR + trainer owners)

- loops.py needs a second regex for the tripwire line (same
  [pos-tokens, pos] span geometry as ENFORCE_RE); trip closes mark spans of
  ~2.5-3.7k instead of 8.1k -> expected drops in chop-span audit volume
  (`python -m dspark.loops --audit`).
- Study showed real loops hiding at pair-depth 4-5 (dilution below the
  depth>15 cut): the tripwire log lines give span-accurate markers;
  proposing a lowered depth cut is trainers' call (do not fork loops.py).
- Script ships DS4_THINK_TRIPWIRE=1 by default -> capture era label:
  data from this restart onward is tripwire-era.
