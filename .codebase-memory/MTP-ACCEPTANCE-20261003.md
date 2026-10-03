# Qwen4 nextn MTP acceptance economics — measured 2026-10-03 (adaptive vs forced depth-3)

> **RE-MEASURED 17:2x-18:2x 2026-10-03 under DS4_QWEN4_VERIFY_PER_ROW=1**
> (same battery, same machine, fresh engine per run, 2 runs/config — run
> texts byte-identical within config). The headline numbers are in §PER-ROW
> below; the pre-fix tables above stay as history. The identity gate was
> later shown grid-luck-sensitive (QWEN4-VERIFY-IDENTITY-20261003.md
> ROOT-CAUSE-2): acceptance stats per config remain valid, the per-row WALL
> below is the operating point to act on.

## PER-ROW re-measurement (the caveat above, resolved)

| config (temp 0, QWEN_BATCH_SESSION=0, 10-prompt battery x400 tok) | wall runs | cycles | deep(T=3) | P(a1) | P(a2\|a1,deep) | tok/cycle |
|---|---|---|---|---|---|---|
| drift-default adaptive (pre-fix binary, 16:12) | 55.4 / 55.6 | 2142 | 11 (0.5%) | 73.5% | 81.8% (n=11) | 1.739 |
| **per-row adaptive** | **65.5 / 72.3** | 2127 | 17 (0.8%) | 72.5% | 90.9% (n=11) | **1.730** |
| forced-3 drift-default (pre-fix binary) | 56.2 | 1672 | 1668 | 70.5% | 70.5% (n=1176) | 2.201 |
| **forced-3 per-row** | **79.2 / 80.2** | 1672 | 1669 | 70.9% | 69.6% (n=1183) | **2.201** |
| serial reference | 69.6 / 73.8 | — | — | — | — | 1.0 |

- **Acceptance is drift-robust, cost is not.** tokens/cycle is identical
  before/after the fix in both modes (1.739→1.730 adaptive — 15 fewer cycles
  from the fixed p6/p7 trajectories; 2.201→2.201 forced-3 with P(a2|a1)
  70.5→69.6% at n≈1.2k). The drafter is the same; the WALL moves.
- **Per-row verify is expensive.** adaptive: 55.4 → 65.5-72.3 s (+18-30%,
  run-to-run wall noise ~±5 s on identical text); forced-3: 56.2 → 79.2-80.2 s
  (79.7 avg, +41%). T=3 cycles cost C3/C2 ~1.45-1.55 under per-row (extra rows
  re-read the 1.3 GiB Q8 logits head + trunk weights), vs the pre-fix >1.266
  bound — **forced depth-3 per-row (79.7 s) is now SLOWER than serial
  (71.7 avg)**, so the "wide-T lift" business case is strictly gated on fused
  row-exact verify, even more firmly than the pre-fix table concluded.
- **v2 (19:1x, ROOT-CAUSE-2 closed: per-row hc gate/mix + per-row attention
  universe under the SAME env):** adaptive 71.6/77.0 s — at/below serial's
  69.6/73.8 band; battery output 10/10 prompts BYTE-IDENTICAL to serial
  (20261003_pr2_*). This is expected: with every verify stage dispatched
  per-row, a T=2 cycle does the work of two serial steps; acceptance stays
  1.733 tok/cycle so cycle-count drops while per-cycle cost rises to match.
  forced-3 v2: 83.3 s, 2.196 tok/cycle, P(a2|a1,deep) 70.2% (n=1176).
  **Spec-under-v2 == serial bit-stream at serial cost: the correctness
  mode, not a speed mode.** Only row-INVARIANT fused kernels (B0/lane-2,
  now with the hard invariance requirement per
  QWEN4-VERIFY-IDENTITY-20261003.md ROOT-CAUSE-2 CLOSED) can deliver both.
- **Spec still pays under per-row vs serial** (65.5 best-run vs 69.6
  best-run, ~+6%; averages 68.9 vs 71.7, ~+4%) but the 12% headline from the
  day shift is noise-limited — treat per-row spec as "serial-identical at
  serial-ish cost" until the second drift source (ROOT-CAUSE-2) and the
  fused window land.
- Do NOT force DS4_QWEN4_MTP_DEPTH=3 anywhere. Adaptive default (drift)
  remains the fastest known binary behavior; per-row v2 is now the PROVEN
  correctness knob (bit-identical to serial end-to-end; matvec+hc+attention
  all per-row under the flag) at serial cost.

## CORRECTNESS-STACK re-measure 20:1x (v2 + batched indexer + grouped MoE)

| stack (all bits-exact vs serial, battery 10/10, 3-grid flips=0) | wall | shallow T=2 verify ms | deep T=3 verify ms |
|---|---|---|---|
| drift-default (fastest, NOT exact) | 55.4 | 22.5 | 28.3 |
| serial reference | 69.6 / 73.8 | - | - |
| per-row v2 alone | 71.6 / 77.0 | ~28.0 | ~38.4 |
| v2 + `VERIFY_INDEXER_BATCH=1` | 71.5 / 71.7 | 27.98 | 38.4 |
| v2 + batch + `MOE_GROUP_EXACT=1` | **66.8 / 67.0** | 27.9 | **37.0** |

The exact stack now BEATS serial by ~4-9% while being bit-identical to it.
Remaining gap to the drifting default is the per-row matvec weight re-reads
(~4.5-5 ms/cycle); that is the invariant-mv_ext kernel target (B2), not more
host plumbing. Evidence: 20261003_pr3_idxbatch_*, 20261003_pr4_group_*.
Profiled via `DS4_QWEN4_MTP_PROFILE=1` at 2048-cycle marks.

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
