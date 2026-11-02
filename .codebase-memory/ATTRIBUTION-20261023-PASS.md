# ATTRIBUTION PASS 2026-10-23 (post-#1238) — fresh prefill decomposition. Result: no unknown time; no unlit lever.

Executed the "fresh attribution" next-move from `HANDOVER-20261023.md` (MoE/GDN audit had
already closed both candidates). Tools: `QWEN4_BENCH=1 tests/test_qwen4_kernels` (rebuilt
Oct-23 22:22, model-free, coexists with prod), `DS4_QWEN4_TIMING=2` bucket rows (one short
engine window), `prefill_probe.py` wall rates, plus a `QWEN_EXACT_VERIFY=0` A/B window.
Logs: `results/20261023_attr_bench.log`, `results/20261023_attr_timing.log`,
`results/20261023_probe_ab*.txt` (this doc's numbers are transposed here).

## 1. Live bucket shares (TIMING=2 sync regime, per 2048-token chunk, pos>=40k)
| bucket | Oct-5 banked | Oct-23 | share | delta |
|---|---|---|---|---|
| moe | 582 | 598.8 | 37.4% | +3% (thermal/noise; NAX level-2 live in both) |
| gdn | 386 | 392.9 | 24.5% | flat |
| attn | 395 | 326.8 | 20.4% | **−17.3% — attn-NAX delivered** |
| hc (ffn+attn) | 242 | 245.7 | 15.3% | flat (f16 arm declined Oct-5 stands) |
| ple | 18.6 | 38.7 | 2.4% | **2x — see §4** |
| sum | ~1623 | 1603 | — | sync wall measured 1599 → **coverage ~100%, NO hidden structural overhead** |

## 2. Kernel arms stable (bench vs Oct-5 anchors, all within ±1–2%)
moe-mm live-chunk 10067→10187 us; gdn qkv-proj-NAX 9242→9234; ab-conv-prep 4248→4281;
scan-r4 9725→9921; chunked-CK-total 70129→70707 (7x slower than serial — reconfirms the
banked negative with today's build); router-f32-vs-dense and q8-gemm fat unchanged (arm
selection closure re-validated).

## 3. Wall rates + umbrella A/B
Cold prefill (non-sync): 16k ≈ 1476, 51k ≈ 1417 t/s with umbrella ON (Oct-4 baseline
~1350 → the +5–9% is the NAX lift). omlx reference 1733–1748 → **residual gap ≈ −19–23%,
entirely inside the five buckets of §1** (nothing outside them).
`QWEN_EXACT_VERIFY=0` A/B: 16k +2.8%, 51k −3.1% — sign flips with ctx, spans intra-session
thermal drift → **the exactness umbrella is prefill-free** (its cost is decode-side verify,
where it stays an intentional operator choice). NOT a prefill lever.

## 4. One watch item: PLE prefill bucket doubled (18.6 → ~36–39 ms/chunk, pos-flat)
First measurement after #1238's `d2ae13e` (PLE n-gram read + flush-layer split). Could be
genuine added prefill-side n-gram staging or a sync-boundary reallocation (flush moves the
bucket edge). Costs ≈ −1.3% wall at 51k, consistent with #1238's "max −2.8%" prefill note.
Low priority (2.4% bucket); re-check with a pre-#1238 binary only if chasing the last %.

## 5. Verdict — the prefill attribution era is CLOSED
Every remaining ms of the omlx gap sits in a bucket whose further compression requires:
(a) OPERATOR golden-decisions: **HC-f16 arm (15.3% of wall, ~1 ULP drift, declined Oct-5)**
— the single biggest untouched block; scan-f16-drift (6.4%, declined Oct-5, PLAN-GATED-SCAN
revisit condition). (b) research-grade reformulations below the ~90 GF scan wall or beyond
MoE-NAX level-2 — no known route. (c) NOT: dispatch-audit, arm-selection, umbrella, sync
"hidden time" — all measured flat/absent in this pass.
If the operator will not move (a), ds4 prefill is at its economic floor for this engine
architecture, and attention returns to decode-quality (drafter acceptance) or capacity
(multi-slot 2.15x) instead.

## Ops notes
- TIMING bucket env is `DS4_QWEN4_TIMING=2` (ds4.c:60323; the Oct docs' "TIMING=2" shorthand;
  grep for it with digits in the pattern — [A-Z_]* misses DS4_QWEN4_*). Stop subcommand is
  `./ds4-server.sh stop` (there is no `stop-qwen`).
- 22:22→22:35 engine windows: TIMING capture, umbrella A/B; production fully restored
  (default umbrella ON, exact stack) after each; verify `curl :8002/v1/models`.
