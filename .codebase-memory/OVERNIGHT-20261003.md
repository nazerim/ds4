# OVERNIGHT 2026-10-03 — oMLX 0.7.0 follow-through: queue drained (operator-authorized window)

Operator granted an overnight window (~04:20–06:30): engine stopped, informed
decisions authorized, up to 2 parallel subagents, commit/push discipline.
This doc is the session record; per-item detail lives in the linked docs.
Parent context: `.codebase-memory/omlx-v070-final-perf.md` (§4 queue),
`HANDOVER-20261002.md` (state at window open: `bb6ecc7`, engine PID 87986 →
operator stopped it for this window).

## Shipped tonight (all signed, pushed to nazerim/main)

| SHA | What |
|---|---|
| `262a416` | docs: omlx v0.7.0-final reconciliation (pre-window, this session) |
| `c90e9c7` | docs: lane 1+2 folds — `omlx-v070-cache-policy.md` (V2 mapping verdict, Scenario L spec) + `omlx-v070-mtp-row-exact.md` (17-mechanism inventory, transfer verdicts, T≤8 priced sketch) |
| `a3c2107` | docs: `THREADGROUP-LIMITS-20261003.md` — lane A audit: NO pipeline declares maxTotalThreadsPerThreadgroup anywhere; qwen4 path has zero runtime queries; M5 Max clean; 16 older-GPU exposure sites / 4 families (RISK-1 dsv4 flash-reduce @1024 and RISK-2 qwen4 idx-select @1024 are the true #4063 analogs) |
| `bcbaba1` | docs: `TOKENIZER-AUDIT-20261003.md` — lane B: no #3975-class bug (all model-constant construction is load-time); cousins: O(256)/byte GPT-2 maps, full-session re-detok per job, per-token mallocs |
| `460ab6b` | **kv: Scenario L** — `ds4_kvstore_sweep_superseded_frontiers`: eager budget-independent sweep of superseded off-grid evict/shutdown snapshots (oMLX a28e5a87 parity); keep newest max(1,tail_anchors) per byte-prefix lineage; children==0/legacy/cold/continued untouched; hooked in evict after refresh (covers open + every store); RED-verified (4 fails) → GREEN |
| `4f618e1` | docs: cache-policy §3.1 SHIPPED record incl. design refinements (EVICT/SHUTDOWN-only — cold stores are divergence anchors; one-store lag; children==0 structural limit) |
| `d9a5d60` | **tokenizer+server**: static GPT-2 byte↔codepoint LUTs (were O(256) rescans per byte on every tokenize/detokenize; exhaustively cross-checked vs old scans: b2cp 0..255, cp2b 0..0x10FFFF) + DS4_MTP_SPEC_DISABLE getenv hoisted out of the token loop |
| `555ee22` | **metal: threadgroup hardening** — fail-closed width guard at the qwen4 dispatch chokepoint (loud refusal beats silent truncation; never fires on M5), 6 dsv4 rms-norm sites swapped to the queried pipeline twin (RISK-5), `DS4_METAL_LOG_TG_LIMITS=1` per-pipeline limit dump. Width-adaptive clamping DEFERRED (needs per-kernel width-invariance proofs; MoE nsg is function-constant-baked — clamping width would contradict the PSO). RISK-1 (dsv4 reduce @1024, needs nwg re-creation plumbing) deferred, upstream-issue candidate |
| `affe9e4` | docs: **MTP acceptance economics measured** (`MTP-ACCEPTANCE-20261003.md`) + wide-T re-gate |
| `d4f682d` | docs: **floor corrected 140.3→112.2 MiB** (QWEN4_EXP 49 layers → 36 linear; FLOOR-20261001 conflated DS4_SHAPE_PRO's 61/45 — its own 113.7 measurement already agreed with 36) + R2b design + TTFT verdict |
| `94d7ef3` | **kv: R2b resume-read trim** — non-terminal delta-chain links SEEK past tokens/logits/GDN-state/PLE (terminal-wins regions): (D−1)×112.2 MiB saved per depth-D walk (~25% of the 10 s cold-resume budget at field-max D=28). Span-aware `ds4_qwen4_payload_link_split` + loader three-way size check (writer↔reader↔split — caught my own full-vs-slice bug LOUDLY during bring-up) + model-free `--payload-trim` sweep. Gates: kv-delta parity OK, prefill-checkpoints OK |

## Measurements taken (engine + :8005 idle-verified around each)

1. **MTP acceptance economics** (single-session, SPEC_TRACE, temp 0, 10-prompt
   coding/prose battery ×2 modes): adaptive P(a1)=73.5% (n=2142, deep fired
   0.5%); forced depth-3 P(a2|a1)=**70.5% (n=1176)** — well above the 0.6
   calibration in `qwen4_spec_depth`'s comment for this traffic class. BUT
   forced-3 = +26.6% tokens/cycle and **+2% wall** ⇒ C3/C2 > 1.266: the T=3
   `verify_rows_exact` 2/1-row sub-batch split eats the gain. **Verify cost,
   not drafter quality, is the bottleneck.** Wide-T lift re-gated: GO only
   after fused T=3 verify ≈ C2 (lane 2 blueprint #4041-style); do NOT force
   DEPTH=3 in production on today's binary. Detail: MTP-ACCEPTANCE-20261003.md.
2. **Idle-TTFT probe** (#3974 analog; 3 warm / 3 post-360 s / 3 post-720 s):
   461 / 458 / 477 ms avg — **no idle-wake penalty on ds4**; keep-warm tick
   CLOSED (no action). Side observation: ~460 ms TTFT floor for a 6-token
   cold prompt is itself worth decomposing someday (lane B fixes #2/#3 are
   the suspects).
3. **Full `make test`** (DS4_TEST_MODEL=Qwen3.8-Flash-Next-Q4, engine
   stopped): everything green EXCEPT 4 failures — long-context, logprob-
   vectors, metal-ssd-streaming-cache-pressure, metal-short-prefill.
   **Baseline-verified NOT MINE**: worktree at `bb6ecc7` (pre-tonight) fails
   the same 4 with the IDENTICAL count (14 = 11+1+1+1) under the same model
   env — they are DeepSeek-fixture tests (flash-0731 vectors, story facts,
   role-token resolution, SSD-streaming config) with no model guard; the
   0731 gguf is absent from gguf/ so they can only pass under the (currently
   missing) DeepSeek model. Follow-up candidate: model-guard these 4 like
   the golden-vectors guard (addae6c pattern) so `make test` under Qwen is
   honest. kv-delta ✓ mtp-verify-depth ✓ dspark-verify-depth ✓ metal-kernels ✓
   tensor-equivalence ✓ server ✓ harness ✓ mtp-slice ✓ payload-trim ✓
   local-golden guard skipped-clean ✓.

## Blocked / deferred (with reasons)

- **Re-baseline vs omlx 0.7.0** — was blocked in the morning window (model
  absent from :8005); **RESOLVED in the second window below** (operator
  copied the model back, results recorded).
- **HC combine_norm beyond T=1** — DEFERRED by tonight's economics: with MTP
  always on, nearly all decode cycles are T=2/3 verify cycles; combine_norm
  T=1 helps only non-spec steps. Re-rank below the fused-verify work.
- **T≤8 verify_rows_exact lift** — re-gated (see measurement 1). First
  milestone narrowed: fused single-dispatch T=3 verify (kill the 2/1
  sub-batch split) with row-exact arithmetic preserved; projected ≈+20% on
  coding-leaning traffic at C3≈1.05·C2. Design: omlx-v070-mtp-row-exact.md §3.
- **RISK-1 dsv4 flash-reduce @1024** + width-adaptive clamping — design notes
  in THREADGROUP-LIMITS-20261003.md; upstream-issue candidate for RISK-1.
- Lane B fixes #2 (incremental live_text), #3 (detok table), #4 (distributed
  scratch) — open-question gates documented in TOKENIZER-AUDIT-20261003.md.

## Environment events

- **FireCuda520 was unmounted at window start** (kv dir empty); operator
  re-mounted ~04:35. Field cache intact: 109 .kv files. No KV-dir override
  was needed after remount.
- Server ran measurement config 04:43–05:5x (`DS4_QWEN4_SPEC_TRACE=1`,
  `QWEN_BATCH_SESSION=0`, `DS4_QWEN4_MTP_DEPTH=3` for the second battery);
  production config restored 06:06 (plain `start-qwen`: batched-session 2,
  ctx 524288, trace env off — NOTE: TRACE_PATH tracing per ds4-server.sh
  comment is separate and defaults as configured in the script).

## State at handoff + verification

```sh
cd /Users/naz/Projects/ds4
git log --oneline -3            # expect 94d7ef3 + docs after
pgrep -f 'ds4-server --model'   # engine restored 06:06
grep -c WARN log/ds4-qwen.log   # expect 0 (fresh log)
./ds4_test --payload-trim && ./ds4_test --mtp-slice   # model-free, coexist
make tests/kv_policy_harness && ./tests/kv_policy_harness  # Scenario L incl.
# FULL make test needs engine STOPPED and currently ends with the 4
# pre-existing DeepSeek-fixture failures under DS4_TEST_MODEL=Qwen (baseline-
# verified at bb6ecc7 — not fork drift; guard follow-up proposed above).
```

## SECOND WINDOW 2026-10-03 (~11:15-11:50, operator resumed)

Operator directive: "continue through all our next items". New standing
constraint recorded: **ONLY ONE of omlx/ds4 may hold a model resident at a
time** — 128 GB machine, both engines 70-97 GB; bench sessions must
cross-stop the other engine.

Operator copied the omlx Flash-Next model back
(`~/.omlx/models/Jundot/Qwen3.8-Flash-Next-oQ4e-mtp`, 99 GB, same oQ4e quant
as the rc1-era runs) and the glimmer restart registered it (catalog 11->12,
rc1-era model_settings survived intact: ctx 262144, PLE SSD offload on,
ANE off, MTP on).

| SHA | What |
|---|---|
| `58c549c` | **server: incremental live_text refresh** (lane B fix #2) — per-slot ids snapshot + pure `live_text_can_append` predicate (strictly-longer + identical head); ds4_token_text proven stateless per token so the splice is byte-identical to full re-render; rewind/rewrite/disk-swap/shrink fall back; --server unit test pins the predicate. Kills O(session_len) detok churn per turn |
| `8a120d9` + `973aaa4` | **tests: model-anchor guards** for the 4 DeepSeek fixtures (realpath vs canonical ds4flash.gguf, overridable via DS4_TEST_DEEPSEEK_MODEL) — **full `make test` under Qwen: 25 OK / 0 ERR, first fully-honest run** (the 4 now report skipped). Wording corrected: the canonical default is the Vision-Exp layers-37-42 gguf (ds4flash.gguf relink Sep-21), NOT 0731 — operator note; the fixtures pass under that default |
| `0eb8f5a` | docs: lane C verdict folded (live_text consumers pure-memcmp; TTFT-floor attribution corrected — refresh lands on the NEXT request's queue time, cold floor unexplained) |

### Re-baseline DONE (was "blocked" in the morning section)

`flash-next-perf/rebaseline_070.py` (now durable in the workstream — operator
rule: no scripts in /tmp) + results `rebaseline_070_2026-10-03.json`, run on
the ORIGINAL rc1-era `perf_payloads.json` (nonce-prefixed, cold, same
prompts as perf_results2.json). Model loaded in 13.2 s (page cache warm):

| size | rc1-era cold | 0.7.0 cold | change |
|---|---|---|---|
| 40k | 992 tok/s | **1733** | +75% |
| 80k | 560 tok/s | **1748** | **3.12x — scaling collapse gone** |
| decode warm | ~34 | 33.7 | flat (40k decode figure = early-EOS artifact, ignore) |

Consequence recorded in `omlx-v070-final-perf.md` §3.1: **ds4 no longer
leads cold prefill on this machine** (~35-38% behind) — the recon's open
candidates #2 (GDN prefill front, ~0.4% — now underwhelming) and #4 (QSA
tile widening) move from "nice" to "required"; decode lead (ds4 ~65 t/s
greedy+MTP vs 33.7) confirmed vs 0.7.0. Quant caveat: Q4_K gguf vs oQ4e MLX.

### Also this window

- **B0 fused-verify experiment (the decode wall): NEGATIVE, reverted.**
  Single-session exact T=3 attention wired through the batched rows kernels
  (env `DS4_QWEN4_VERIFY_ROWS_FUSED`, working-tree only): greedy identity
  BROKE on 2/10 battery prompts (accept-then-diverge at char ~1.4k) and it
  gained nothing (+0.3% wall — rows kernels burn 64-split-slot grids at 3
  rows). Root learning: rows geometry (fixed split windows, per-row
  qwen4_attn_row_splits) ≠ sub-batch exact geometry (redistributed
  keys_per_split) — the batched "same arithmetic" claim is internal to the
  batched path. A real fused window needs a purpose-built kernel
  reproducing the ≤2 path per row, and should start by PROFILING where the
  exact T=3 cycle spends (the HC 2/1 split at ds4.c:58349 is a cheaper next
  probe). Full record + re-scoped B1/B2: FUSED-VERIFY-DESIGN-20261003.md
  §B0-RESULT. Tooling SHIPPED: `tests/spec_economics/` (battery, trace
  parser, both 20261003 result JSONs).
- **B1 MoE attribution + knob (15913c6, defaults unchanged):** depth-bucketed
  cycle profiler (DS4_QWEN4_MTP_PROFILE) + TIMING=2 group breakdown pin the
  T3-verify +5.0ms delta at ~60% MoE (per-row expert re-stream, 20.3 vs
  17.2ms). Grouped-experts path at verify size: −1.35ms/cycle (~2.5%
  forced-3 wall) but breaks slot-reference bit-identity; shared-dense alone
  identical, saves nothing. Remaining exactness blocker localized to ONE
  question: grouped-vs-slot reduction order (metal MOE_*_GROUPED kernels).
  Full numbers: FUSED-VERIFY-DESIGN §B1.
- **RISK-1 upstream issue: DRAFTED** (threadgroup-limit exposure, all cites
  re-verified against upstream `0aaea5a` via gh api; no duplicate issues;
  #607 cross-ref as the M1-Max audience evidence). NOT POSTED — awaiting
  operator go-ahead (public action). Draft location: this file's git-history
  of the lane D run; summary: title "Metal: split-K attention reduce
  dispatched at 1024 threads with no per-pipeline limit check", minimal fix
  = the fail-closed guard shape we shipped in `555ee22`, plus a
  DS4_METAL_LOG_TG_LIMITS-style diagnostic.
- glimmer service restarted twice (register model; then unload before ds4
  tests) — :8005 healthy, models unloaded after bench; ds4 engine restored
  at window end (production config).

## DAY SHIFT UPDATE 2026-10-03 (B3 push -> identity root cause; see QWEN4-VERIFY-IDENTITY-20261003.md)
While pushing the decode wall (B3 policy tuning), the depth-perturbation
experiments exposed the systemic verify-vs-serial drift: ROOT CAUSE = mv_ext
matvec kernels not row-count invariant (1 ULP on 82% of logits elements,
T=2/3 vs T=1). Fix shipped env-gated (29224e2, DS4_QWEN4_VERIFY_PER_ROW=1);
--qwen4-verify-identity regression added (flips=0 with fix, think-high and
deep). Server-completions residual (prompt 3@884) open. Grouped MoE is
innocent (bit-exact in-engine replay, all layers); shared-DENSE at verify
sizes is NOT serial-exact (shared-slot is) -> GROUP_EXACT redesign = grouped
routed + shared-slot stride-11 (B2 note in FUSED-VERIFY-DESIGN).
Production engine restored with defaults (drift-status-quo, fastest known);
flip to per-row if identity > 15% decode latency.
Depth-policy knobs (MTP_PROFILE/DEPTH_BITS_*/DEPTH_UNLOCK/GROUP_EXACT/
SHARED_DENSE_MIN/VERIFY_PER_ROW) all env-gated, defaults = pre-experiment.

## NEXT queue (ranked) — UPDATED 3rd time

1. SERVER residual for the identity fix: per-row vs serial still differs at
   completions prompt-3@884 while the session probe is clean — bisect the
   server spec skip/rollback boundaries (think-close transitions).
   Then: decide VERIFY_PER_ROW default (cost 65.4 vs 55.4 battery) + re-capture
   goldens if flipped. THE decode headline is now correctness, not fused windows.
2. Fused single-dispatch T=3 verify (the re-gated lift milestone; blueprint
   in omlx-v070-mtp-row-exact.md §3 + e15e5b53/f5bf6f7b mechanisms) — note:
   per-row matvec cost must be folded into this design (they interact).
   Priority RAISED by the re-baseline: prefill+decode are now the two
   fronts where omlx 0.7.0 either leads (prefill) or trails (decode) —
   fused verify is the decode-side multiplier.
2. **POST the RISK-1 upstream issue** — draft ready, operator approval only.
3. QSA tile widening (rc1 recon §5 plan item #4) — now the direct response
   to losing the prefill row: omlx 0.7.0 prefill ~1750 flat vs ds4 1268-1319.
4. R2b field measurement (compare load `ms=`/`size=` in log/ds4-qwen.log at
   matched chain depths before/after the trim; engine was rebuilt so the
   "after" samples accumulate from today).
5. Scenario L reclaim tally (`frontier-superseded` lines + MiB).
6. Detok-table memory decision (lane B fix #3, open question 2) then
   distributed scratch (#4, question 3).
7. Per-turn live_text savings sizing via TRACE_PATH (lane C follow-up 1).

## State at handoff + verification — UPDATED

Production engine: restored via `./ds4-server.sh start-qwen` at window end.
Verify:
```sh
cd /Users/naz/Projects/ds4
git log --oneline -8
pgrep -f 'ds4-server --model'
grep -cE 'WARN|ERR' log/ds4-qwen.log          # expect 0
./ds4_test --server >/dev/null 2>&1 && echo server-green   # coexists (model-free)
curl -s 127.0.0.1:8005/health                  # glimmer up, 12 models, 0 loaded
# full make test now GREEN under DS4_TEST_MODEL=Qwen (25 OK, 4 guarded skips);
# under default (ds4flash.gguf -> Vision-Exp) unchanged behavior.
```
