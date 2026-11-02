# Decode concurrency + MTP baseline — 2026-10-23

Tool: `speed-bench/session_concurrency_bench` (engine API, no HTTP/scheduler
noise = server ceiling). `--spec` = Qwen3.8 MTP batched greedy decode.
Model Qwen3.8-Flash-Next-Q4 (resident 69.7 GiB weights, NOT streamed), M5 Max.
ctx 16384, gen 64, warmup 8. Server STOPPED during runs (single instance);
bench self-prices cells against 42.12 GiB free-for-sessions budget. CSVs:
`.codebase-memory/sweep-spec.csv`, `sweep-plain.csv`.

## Q1 — multi-slot decode scaling (with MTP, = production mode)
| slots | decode AGG t/s | per-stream | vs 1× | mem | step p95 |
|---|---|---|---|---|---|
| 1 | 62.6 | 62.65 | 1.00× | 8.8 GiB | 24 ms |
| 2 | 83.4 | 41.69 | 1.33× | 9.4 | 36 |
| 4 | 106.8 | 26.70 | 1.71× | 10.6 | 58 |
| 8 | 134.3 | 16.79 | 2.15× | 12.9 | 90 |

- Aggregate scales sub-linearly but real. Marginal gain per added slot falls
  (2→4 +11.7/stream; 4→8 +3.4/stream) → plateau toward ~8-12.
- Memory is NOT the constraint (8 slots = 12.9 of 42 GiB free). Headroom for 12-16.
- Per-STREAM latency degrades hard (62.6→16.8): throughput/cost lever for
  CONCURRENT sessions, NOT single-user speed. TTFT also worsens (prefill waves
  serialize: width 8 last stream ~90 s). Prod is `--batched-session 2`.

## Q2 — MTP (spec) vs plain batched decode
| slots | plain agg | MTP agg | MTP gain |
|---|---|---|---|
| 1 | 56.3 | 62.6 | +11.2% |
| 2 | 77.8 | 83.4 | +7.2% |
| 4 | 97.6 | 106.8 | +9.4% |
| 8 | 122.2 | 134.3 | +9.9% |

- MTP is a consistent **~+8-11%** net win at every width — LIVE lever, not a dead
  end (contrast PLE ~1.5%).
- **Lead**: accepted tokens/stream/cycle = **1.50 at width 1 → ~1.03-1.04 at
  width ≥2**. Under batching MTP barely speculates — capturing only a few % of
  draft potential. Why does acceptance collapse with batch width? (single-tree
  verify `DS4_QWEN4_VERIFY_SINGLE_TREE`, draft length, cross-stream verify).
  If recoverable, MTP gain could rise well past +10%.

## Caveats
- Acceptance/content-dependent (Italian corpus, staggered offsets). Absolute t/s
  not comparable to the server HTTP baseline (no scheduler/quantum noise); the
  RATIO (spec:plain, scaling) is the signal.
- Thermal/cache drift over ~4 min runs; single pass per point. Re-run before
  committing to a config change.

## Next actionable experiments
1. Raise `--batched-session` (4-8) ONLY if concurrent sessions justify the
   per-stream latency + TTFT cost. Baseline the real server (HTTP) at that
   setting before switching prod.
2. Chase the MTP acceptance collapse: instrument accepted/stream vs width;
   test verify-single-tree vs multi, draft depth. Bounded, promising.

## Single-stream speculation on CODE vs PROSE (thesis test — confirmed)
Same harness, width 1, ctx 16384, corpus = ds4.c (real code) vs promessi (Italian prose).

| corpus | plain t/s | MTP t/s | gain | accepted tok/stream/cycle |
|---|---|---|---|---|
| prose | 56.3 | 62.6 | +11.2% | 1.50 |
| code  | 57.7 | 75.3 | **+30.5%** | 1.94 |

=> A single stream claims the multi-stream per-step headroom via speculation:
low-entropy code raises draft acceptance 1.50→1.94, lifting single-stream MTP
gain +11%→+30%. 75.3 t/s one stream ≈ most of width-2 prose batching (83.4),
from ONE session. Acceptance (1.94) is still below the N=2 ceiling (~3), so
deeper/n-gram drafting (#1167) on repetitive code should push single-stream
further. This is the single-stream analog of the batching lever — content is the
multiplier. Caveat: greedy/temperature-0 only; source-code proxy for agent output.

## ds4-native MTP DEPTH lever — measured 2026-10-23 (code prompt, greedy)
Hypothesis: adaptive locks depth-3 (2-token draft) to ~0.4% to protect verify-vs-
serial identity; maybe forcing it wins throughput. Live server, code prompt, median/3:
| config | decode tok/s |
|---|---|
| adaptive (production policy) | **66.8** |
| forced depth-3 (DS4_QWEN4_MTP_DEPTH=3) | 63.2 |
| adaptive + PROFILE (sync check) | 66.3 |

**Conclusion: forcing deeper drafts is ~5% SLOWER — the adaptive policy is already
optimal on this model/content.** depth-3 only pays when acceptance is near-perfect
(the policy's gate); applying it always wastes verify cycles on the 3-row pass.
PROFILE's per-cycle sync did NOT perturb tok/s (66.3≈66.8) → numbers trustworthy.
⇒ The native ds4 single-stream lever (draft deeper) is EXHAUSTED. Remaining
acceptance headroom is a **drafter-quality** problem (DFlash 2 = a *better* draft
that survives verification), reachable on this box only via Splash. Confirms the
decode line funnels into the Splash/DFlash2 A/B, not a ds4 code tweak.

## Fresh confirm 20261023 (late) — fused-stream cost curve + verify buckets
Streams (sweep-spec.csv, ctx16k, MTP-on): marginal step cost per ADDED stream —
| width | step_mean | Δ vs prev | agg t/s | ×baseline |
|---|---|---|---|---|
| 1 | 23.94ms | — | 62.65 | 1.00× |
| 2 | 24.92ms | **+0.98ms /+1 stream** | 83.39 | 1.33× |
| 4 | 38.63ms | +13.7ms /+2 (~6.9 ea) | 106.79 | 1.71× |
| 8 | 87.50ms | +48.9ms /+4 (~12.2 ea) | 134.29 | **2.14×** |
=> 1st extra stream ~FREE (idle lanes), then bandwidth wall. 8-way = **2.14× (+114%)**,
NOT 8× (+700%) — the box leaves latency-bound by ~width 4-8.

Verify buckets (this run, DS4_QWEN4_MTP_DEPTH=3 + PROFILE, prod single-tree):
- shallow(T2) verify **22.6ms ≈ a single T=1 step (23.9ms)** → the 2nd speculative
  row IS near-free (fused). Correction to my earlier framing: the "per-row re-read"
  penalty is NOT at T=2; it's at T=3, and the 1.45–1.55× figure was the OLD
  per-row(v2) correctness mode.
- **deep(T3) n=0** — depth-3 did NOT engage under production single-tree for this
  prompt (chain gate `glm_mtp_have2 && !snap2_ple_hist`). So C3/C2=1.5 is NOT
  reconfirmed live today; it stands only from the 2026-10-03 (per-row) study.
- accept: 650/949 cycles = 68.5% → ~1.69 tok/cycle (matches).

## CORRECTED limiter (20261023 verify measurement)
- T=2 verify is ~FREE vs T=1 (22.6 vs 23.9 ms) → the 2nd speculative row is not
  the cost wall. Earlier "C3/C2 1.45–1.55" was the retired per-row(v2) mode.
- depth-3/T=3 **does not run in production SINGLE_TREE** (deep n=0): T=3 fused is
  gated on `verify_rows_exact` (row-invariant fused wide-verify = the unshipped
  B2 invariant-mv_ext kernel). Wide-T "business case strictly gated on fused
  row-exact verify" (MTP-ACCEPTANCE doc).
- Single-head chaining: n_nextn_predict=1 → wide drafts are CHAINED (a serial extra
  draft pass), not one parallel block like DFlash.
- ⇒ Single-stream MTP ceiling on this box = T=2 only → ~1.7 tok/cycle, ~+11% over
  serial (matches prose bench). Not bandwidth-limited; **acceptance + absent fused
  wide-verify + chained single head** are the limiters.

## DECODE-THREAD CLOSURE (2026-10-23) — operator decision
All decode levers above are measured and **closed**:
- PLE n-gram disk read = ~1.5% of decode, context-independent → dead end (see
  PLE-FINDING-20261023.md).
- Multi-slot = throughput/capacity, not single-stream latency → not ours to grab.
- MTP depth-3 (wide/parallel draft verify) → **DECLINED**; corroborates
  `QSA-ATTRIBUTION-20261004.md` "C3 tested-and-declined". Single head chains;
  wide fused verify gated on the unshipped B2 invariant-mv_ext kernel. Do not
  re-open from the "Next actionable experiments" list above (item #2 MTP-acceptance
  is subsumed by that decline).
- Warm-TTFT / finer KV anchoring → **DECLINED by operator**: KV config already tuned
  to real workloads; overlaps QSA-ATTRIBUTION §4.5 decode-side residual (monitored).
- Splash sidequest A/B → CLOSED (see SPLASH-INTEL-20261023.md): short/code decode +43-63%
  but ds4 wins 300k live-KV decode +27% (resident via our PLE-on-disk bet).

**The only genuinely open performance front** (post attn-NAX, Oct-8) is **MoE (biggest
prefill bucket) + the GDN chunked-scan** — prefill, not decode. Full resume packet:
`HANDOVER-20261023.md` (read first; VERIFY MoE-NAX-Q4 status before spending a window).
