# AB-PROTOCOL 3C — live A/B for the retrained DSpark conf head

Status: PROTOCOL ONLY (night 20261103/04, S6). Nothing here has been
executed. Morning operator + independent review before first live run.

## What is being A/B'd
`mtp.2.confidence_head.proj.weight` in the DSpark **support GGUF** —
official q8_0 [4352,1] head vs the confcal1b replacement (linear-4096 h-
half + zero markov half, no bias; S3 gate: val AUC .8453 vs .5448 baseline,
ECE .0109; q8_0 requant proven calibration-silent, S5).

## Hard rules (S5 independent review — binding)
1. **Official file immutable.** All swaps are copy-based
   (`tools/repack_dspark_tensor.py`). Record in the ledger, side by side:
   official sha256 `0807a67f…679bc877` and the repacked output sha256
   (currently `3365e6b4…72adc6cf` for run_confcal1b — REGENERATE if weights
   change).
2. **Boot-time binding.** The engine reads confidence_proj once at boot
   (matvec_any over [h4096|markov256], ds4.c:37202, NO bias term). A head
   swap therefore REQUIRES a restart: quiesce the collector
   (`./ds4-server.sh stop`), swap, boot, re-anchor the measurement window
   AFTER the banner `exact: 1`. No paired stats across the swap.
3. **Deployable head form only:** linear-4096 h-half, zero markov half,
   no bias (validator pins DENSE [4352,1], ds4.c:6359). Any S4 head must
   keep this form until prev-token features exist engine-side.
4. **Gate semantics:** comparison cuts iff `sigmoid(logit) >= .45` — the
   threshold lives on the sigmoid (DSPARK_CONFIDENCE, ds4-server.sh default
   .45), never on the raw logit.
5. **Primary live metrics:** acceptance-length histogram + draft coverage
   (and tok/s), NOT AUC. The offline proxy (eval3c) is corroborating only.
   Known risk to watch: gate-region under-prediction ~1.6pp (pred .4666 vs
   obs .4824) and within-day-only calibration (conv-disjoint val, single
   pid/day) — cross-day drift is UNPROVEN.
6. `trainer/tests/test_gguf_dq.py::test_q8_0_*` must stay GREEN before any
   repack tool change (pins the quantization convention).
7. Quality guard: conf head swap changes the ladder — re-baseline
   model_fp + the bounding probes (ds4-server.sh comment: "Re-baseline if
   the drafter head is ever retrained", 2026-10-29 note).

## Procedure (per arm)
A0. Preconditions: tests green; disk space for one support-GGUF copy
    (~6 GB on blade); engine log rotation snapshot (log/ds4.log).
A1. Build arm-B file: `python3 tools/repack_dspark_tensor.py
    gguf/…Vision-Exp-DSpark-support.gguf --repack
    'mtp.2.confidence_head.proj.weight=<weights.npz>#w_full_1x4352'
    --out <BLADE>/ds4-train/repack_test/support_AB.gguf`
    Verify printed changed-byte window + dequant max|delta| < 1e-3.
A2. Arm order **A→B→A→B** (two full cycles; first cycle = warm-informal,
    second = scored). Arm switch =
    `DSPARK_MODEL=<path> ./ds4-server.sh restart-collect`
    (collect env preserved: DSPARK_EXACT=1, parity dir pinned, DS4_TRAIN_DUMP).
    Banner MUST show `support: <path>` + `exact: 1`; record pid + boot ts
    in the ledger; wait >= 5 min post-boot before scoring (cache warm).
A3. Bench: `uv run python tools/bench_ab_3c.py --port 8002 --out arm_X.json`
    (fixed prompt set, streamed tok/s; parse `ds4: DSpark stats` lines from
    log/ds4.log into cycles/accept_rate/avg_accept/accept_len histogram —
    enable with DS4_DSPARK_STATS=1 at boot).
A4. Restore default arm via plain `./ds4-server.sh restart-collect`
    (DSPARK_MODEL unset = official support path).

## Decision rule (pre-registered)
Scored cycles only. Arm B advances iff, pooling the two scored B windows
vs the pooled A windows: (i) accept-token-weighted `avg_accept` >= +2%
relative, (ii) no p95 tok/s regression > 2%, (iii) quality ladder unchanged
(model_fp + probes), (iv) accept-length histogram shift directionally
consistent with the offline gate-region stats (more >=2-runs at equal
coverage). Any tie or mixed signal => stay official, iterate training.

## Rollback
One command: `./ds4-server.sh restart-collect` (default support path). The
repacked file stays on blade for forensics; never in the repo, never
overwriting `gguf/`.
