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
