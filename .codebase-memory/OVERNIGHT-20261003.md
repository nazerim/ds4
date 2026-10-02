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

- **Re-baseline vs omlx 0.7.0** — BLOCKED: current :8005 catalog no longer
  serves Qwen3.8-Flash-Next-oQ4e-mtp (operator's config now Muse-Glimmer-30B
  family + Qwen3.8-27B); :8000 app down. Did NOT reconfigure the production
  glimmer overnight. Tooling ready for when the model is back:
  /tmp/gen_payloads.py + /tmp/rebaseline_8005.py (40k/80k cold, nonce-
  prefixed; rc1-era reference 992/560 tok/s). Decode side needs no re-run:
  the 2026-10-02 0.7.0 smoke already recorded 34.3 tok/s warm single-stream
  vs ds4's 47–68.
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

## NEXT queue (ranked)

1. Fused single-dispatch T=3 verify (the re-gated lift milestone; blueprint
   in omlx-v070-mtp-row-exact.md §3 + e15e5b53/f5bf6f7b mechanisms).
2. Model-guard the 4 DeepSeek-fixture tests (addae6c pattern) so make test
   under Qwen is honest; or restore a 0731 gguf + record it in download_model.sh.
3. R2b follow-through: measure realized resume-latency delta in the field
   (log `save=`/load times before-after at matched depths) — the seek path
   is shipped but the seconds column is projected, not measured.
4. Scenario L field check: after a few days, tally `frontier-superseded`
   lines + MiB freed in log/ds4-qwen.log.
5. Re-baseline when Flash-Next returns to a glimmer instance (scripts ready).
6. Lane B #2 incremental live_text (gated on the tool_mu consumer invariant
   question) — biggest per-request CPU win for long agent sessions; the
   ~460 ms cold TTFT floor decomposition belongs with it.
7. Upstream: RISK-1 issue draft for antirez/ds4 (matches #4063 audience);
   watch #1163/#1164/#1020/#1022.
