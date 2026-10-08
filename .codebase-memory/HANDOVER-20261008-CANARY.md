# HANDOVER 2026-10-08 (canary window) — PR1149 decision state + queue

Context-limit checkpoint. Read this first after compaction.

## Repo / production state
- `main` @ 781e615, clean. Branch `pr1149-ab` @ e71b1b6 (pushed) =
  upstream #1149 cherry-picks + A/B bench arms + step-2 probe + review doc.
- Production UP: ds4-server (qwen) PID 2132, smoke OK. On-disk
  `./ds4-server` = MAIN build (do not restart into the branch binary by
  accident; the canary builds it explicitly).
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
- **OPERATOR DECISION PENDING**: (a) accept + re-anchor goldens for
  ~3-5% prefill wall; (b) merge dark; (c) decline [review leans (c),
  HC-f16 precedent]. Operator asked: will (a) beat performance / worth
  testing both — answered YES (kernel-measured; e2e live pending) and
  agreed to a live canary first.
- **Step 3 = live canary A/B (planned, runbook appended to the review
  doc)**: same branch binary, kill switch on/off, fresh KV, production
  config; measures prefill_probe cold t/s (headline), TIMING=2 attn
  bucket (`DS4_QWEN4_TIMING=2`, ds4.c:59308), battery acceptance. Then
  the operator decides with live numbers. Canary commits nothing;
  goldens move only on deliberate re-capture.

## Queue after the canary
1. Operator (a)/(b)/(c) call on PR1149 (see review doc).
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
