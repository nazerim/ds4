# Qwen4 nextn MTP acceptance economics — measured 2026-10-03 (adaptive vs forced depth-3)

Answers open question #1 of `.codebase-memory/omlx-v070-mtp-row-exact.md`
(the wide-T lift business case). Overnight window, engine on M5 Max, single
session (`QWEN_BATCH_SESSION=0`), `DS4_QWEN4_SPEC_TRACE=1`, temperature 0,
10-prompt battery (coding/JSON/math/structured prose — deterministic-leaning,
~3763 generated tokens per run). Scripts: /tmp/accept_run.py,
/tmp/parse_spec_trace.py (session-local, not repo'd). Binary = `555ee22`.

## Results

| Mode | cycles | deep(T=3) | P(a1) | P(a2\|a1,deep) | tokens/cycle | battery wall |
|---|---|---|---|---|---|---|
| adaptive (default policy) | 2142 | 11 (0.5%) | 73.5% | 81.8% (n=11) | **1.739** | **55.2 s** |
| `DS4_QWEN4_MTP_DEPTH=3` forced | 1672 | 1668 | 70.5% | **70.5% (n=1176)** | **2.201** | **56.2 s** |

## The finding: verify cost, not drafter quality, is the bottleneck

- Forced depth-3 delivers **+26.6% tokens/cycle** (2.201 vs 1.739) and is
  still **~2% SLOWER end-to-end**. The 3-row verify cycle costs >26.6% more
  than the 2-row cycle: `C3/C2 > 1.266`. The structural cause is known —
  T==3 runs `verify_rows_exact`, which splits attention into 2/1-row
  sub-batches (ds4.c:57652, :58322, :58479): extra dispatches + a second
  attention pass, i.e. exactly the cost oMLX #4041's *fused row-exact verify
  windows* eliminate.
- **P(a2|a1) = 70.5% at n=1176** on this mix — well above the "~0.6 on
  general prose" calibration quoted in the `qwen4_spec_depth` comment
  (ds4.c:74413-74417). The adaptive policy's engagement gate (perfect 8-bit
  window) fires on only 0.5% of cycles; its acceptance-side calibration is
  stale for coding-leaning traffic, but its DECISION is still correct while
  C3/C2 > 1.266 — the comment's "does not cover the wider cycle" holds for
  the current cost structure, not the current drafter.
- Adaptive P(a1)=73.5% vs forced 70.5%: same first draft at both depths
  (comment ds4.c:74402-74404); the ~3-point gap is trajectory divergence
  after deep accepts + ~2σ sampling noise. Not material.

## Decision impact on the T≤8 lift (lane 2 §3)

- **Re-gated GO, first milestone narrowed**: the lift only pays if T=3 (then
  T=k) fused verify costs ≈ C2. At C3≈1.05·C2, forced-3 would net ≈ +20% on
  this mix (2.201/1.05 ÷ 1.739); adaptive policy would then engage deep far
  more often on its own. Kernel work = lane 2's fused row-exact windows
  (blueprint: oMLX e15e5b53/f5bf6f7b; ds4 side: collapse the 2/1 sub-batch
  split into one dispatch with per-row exact arithmetic preserved).
- **Widths beyond 3 stay unmeasurable until the lift exists**: P(a3|a2)
  requires a 4-row verify path; nextn chain quality suggests ~0.6-0.7
  decay, i.e. mean ~2.6-2.8 tokens/cycle at T=5 IF fused costs stay flat —
  re-measure with the same scripts post-lift.
- **Do NOT force DS4_QWEN4_MTP_DEPTH=3 in production** on today's binary:
  measured net-negative (-2% wall) despite the acceptance headroom.

## Caveats

- Prompt mix is deterministic-leaning (agent/coding profile); general prose
  would pull P(a2|a1) toward the 0.6 the code comment cites. Production
  traffic is a mix — the +20% projection applies to the coding share.
- Wall-time delta (-2%) is within run-to-run noise on a single battery; the
  DIRECTION is nonetheless solid because the tokens/cycle gap (+26.6%) is
  large and the C3>C2 mechanism (sub-batch split) is structural.
- Single session only; batched-mode MTP (production shape, depth-2 rows) has
  its own cost curve and no trace instrumentation (ds4.c:80443-80452 counts
  but does not print per-cycle lines) — instrumenting the batched path is a
  follow-up if batched deep verify is ever considered.
- Idle-TTFT probe (#3974 analog) ran on the same instance afterwards — see
  OVERNIGHT-20261003.md for that result; it does not interact with these
  numbers (separate schedule, server otherwise idle).
