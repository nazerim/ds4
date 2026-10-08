# HANDOVER 2026-10-08 (canary window) — PR1149 decision state + queue

Context-limit checkpoint. Read this first after compaction.

## Repo / production state
- `main` @ c5211b2 (PR1149 MERGED), clean. Branch `pr1149-ab` merged;
  evidence + review doc now on main.
- Production UP: ds4-server (qwen) PID 4434, smoke OK — running the
  merged binary with NAX LIVE (live prefill probe 1271 tok/s @6581).
  Rollback without rebuild: `DS4_QWEN4_NO_ATTN_MM_NAX=1` (verified exact
  vs `tests/test-vectors/qwen38-flashnext/local-golden-pre-nax.vec`).
- Subagent lane healthy (general + coder verified this week).
- Engine cycles: operator grants windows as needed (stop/start-qwen).

## PR1149 (upstream NAX QSA prefill attention) — decision state
- **Step 1 DONE**: kernel A/B on our hardware — NAX wins 1.25x@T=512,
  1.30x@T=1024, 2.24x@T=1024-clustered, 1.14-1.24x@T=2048 (production
  prefill quantum), tie@T=8192. Author's numbers reproduce. Win is real.
- **Step 2 DONE**: production-config stream A/B — **9/11 prompts diverge
  visibly** (2/11 bit-identical; first flip char 259-978 then cascade);
  spec acceptance 73.57% vs 73.03% (within noise). Full golden-tree
  re-anchor class, NOT ~1ulp-invisible. Package:
  .codebase-memory/PR1149-REVIEW-20261008.md (on pr1149-ab).
- **OPERATOR DECISION: (a) ACCEPTED 2026-10-08** — merged (`c5211b2`),
  deployed (PID 4434). Golden guard = triple pin (canonical classic /
  NAX production / pre-#1149 production), NO re-anchor of the classic
  fixture; harness hatch `DS4_TEST_LOCAL_GOLDEN_METAL4=1`. Stream gate
  on merged binary: 11/11 bit-identical to banked NAX arm; acceptance
  73.70% (noise). Golden matrix: `results/20261008_golden_matrix_*.log`.
- **Step 3 = live canary A/B: DONE 2026-10-08** (runbook was appended to
  the review doc). Result: prefill win REAL — cold prefill tok/s
  **+4.6/+8.2/+7.2%** (6.6k/13k/25.8k prompts), TIMING=2 attn bucket
  **384.2 vs 485.8 ms/chunk (-20.9%)**, total prefill chunk **-6.7%**;
  controls (gdn/moe/ple/hc) unchanged; acceptance 73.85% vs 72.97%
  (noise); 0 errors. Banked: `tests/spec_economics/results/20261008_canary.log`
  + canary battery JSONs (main `448d9c8`); STEP 3 RESULT section in the
  review doc (pr1149-ab `6af4be9`). Production restored (PID 3291).
  Canary committed nothing; goldens move only on deliberate re-capture.

## Queue after the canary
1. DONE: PR1149 (a) accepted, merged, deployed, triple-pinned.
2. Watch upstream: #1179 (WIP, no prefill-scan change — review banked
   PR1179-REVIEW-20261008.md, HOLD), #1167/#1168, #1163, #1150/#1154,
   #1149 (this one), #1176 (issue, ours latest).
3. R2b chain=%d + Scenario L tallies: ride on real traffic days
   (chain= live in the binary; quiet so far — 0 lines, no disk saves).
4. OQ3 (ds4_dist_run relevance) gates tokenizer fix #4; optional
   production TRACE_PATH day (live_text append firing rate — fix #2
   measured ~0, bounded).

## Closed this week (do not re-open without the revisit conditions)
- GATED-SCAN: banked negative (PLAN-GATED-SCAN.md; f16 audit ~400x over
  drift gate + 2.3x flop wall; revisit conditions recorded).
- Detok table (tokenizer fix #3): SHIPPED c922e14 (0/129,280 mismatches,
  28.7x faster, 1.58 MiB, goldens OK).
- live_text sizing (OQ4): measured ~0 realized win (append never fires —
  token seam; fix #2 stays shipped, value captured by fix #3).
- live_text instrumentation 6af15aa + DS4_LIVE_TEXT_FULL knob 04c421a.
