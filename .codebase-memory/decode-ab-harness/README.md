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
- ROTATION BLIND SPOT (learned 2026-11-10, extract-r2): an engine restart
  renames the log and creates a new file at the same path; a watcher holding
  the old fd stops seeing traffic and QUIETs out exactly quiet-seconds after
  the PREVIOUS file's last event (false end-of-run: quiet fired at 15:25 while
  the session was live at 199k depth). Fix candidates: reopen by path when the
  held inode != st_ino(path), or fire QUIET only when engine cpu% also idles.
  Until fixed: after any boot inside a watch window, re-arm the watcher — run
  verdicts come from log-file analysis regardless, which spans both files.

Run segmentation & attribution (multi-soak era, 2026-11-09; full rules in
HANDOFF-DECODE-FIX-DSPARK-20261108.md §8):
- `client disconnected` ends a request WITHOUT a finish= line: monitors must
  count it in inflight/quiet bookkeeping or STALL fires ghosts at an idle
  engine (false-positive 2026-11-10 21:27; alert-time thread sample preserved
  at ds4 `log/ex1_stall_2128.sample` showing accept()-park, cpu 0.0).
- Attribute by CONTEXT-CHAIN continuity (`kv cache hit ... file=<sha>` /
  ctx-ladder position), never by wall-clock: harnesses fork, sessions rejoin
  the single-slot lane, and a `kv cache hit` on chain cd9e2bf3 (5,497 tok) is
  the SHARED termbench task preamble every task opens on — chain reuse is NOT
  a session resume.
- Silence with inflight==0 (last event `finish=`) = client-side harness idle
  (e.g. make-doom runs MIPS builds/sim locally) — expected data, not a stall.
  STALL = silence WITH inflight>0 + cpu<1% (`ds41_watch_leg.py --stall 180`);
  QUIET-exit needs 2400s to absorb sim and inter-run gaps.
- Band attribution: `dspark req:` accept attaches at request-END ctx position,
  so 64k megaturns (machine-text dumps: ~96.5% accept, ~40 t/s) skew the band
  they END in, not the ones they grow through; within-turn `avg=` series is the
  position-true depth evidence. Two consecutive 64k finish=length messages kill
  the harbor/termbench harness while the engine stays clean — engine verdict
  and harness verdict are separate calls.
