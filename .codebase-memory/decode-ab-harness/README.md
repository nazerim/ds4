# Decode A/B harness (transient-reap investigation, 2026-11-08)

Deterministic 24-turn soak (`ds41_soak.py`, seeded traffic: mixed prose/code/
JSON/CJK appends + thinking decode), leg runners (`ds41_run_leg*.sh`: plain &
dspark lanes, env-matrix legs, graceful stop), analyzers (`ds41_compare_legs.py`
API curves; `ds41_analyze.py` engine decode buckets; `ds41_static_audit.py`
brace-scoped call-path audit), shared corpus builder (`ds41_speed.py`).

Normalized A/B rule (learned the hard way): decode-isolate by segmenting turns
at `finish=` and using the last decode line's cumulative (gen, wall); the
`chunk=` rate field is unsafe across prefill boundaries (interval-timer reset
artifact). Verdicts: .codebase-memory/DS41-SMOKE-20261106.md +
PLAN-DECODE-DEGRADE-20261108.md. Upstream: issue #1330 / PR #1331.

Spec-era tools (DS4_DSPARK_STATS=1 required):
- `ds41_collect_spec.py [--since HH:MM:SS] log...` — per-request `dspark req:`
  deltas, weighted accept, leg span/wall, decode avg= medians split by
  speculating vs zero-cycles. Handles cycles>0/proposed=0 (confidence-gated)
  rows without div-by-zero.
- `ds41_watch_leg.py` — long-run monitor (snapshots to /tmp/ds41_leg.snap):
  liveness/RSS/swap, spec aggregates, max depth, 15-min decode-median
  regression tripwire, boot-ctx/restart detection.
- Cross-leg rule: on stochastic agent benches, request sequences DIVERGE —
  compare decode `avg=` medians BANDED BY CONTEXT DEPTH (ctx=..pos), never raw
  wall or total tokens. Age-bucket medians are the leak tripwire (flat = fixed).
- Midnight-span windows: log date prefix flips 1108->1109; window arithmetic
  must wrap (subtract start mod 86400) or half the run silently drops.
