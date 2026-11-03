# AB-PROTOCOL 3C — live A/B for the retrained DSpark conf head

Status: PROTOCOL v2 (night 20261104, S6) — REWORKED after independent
review verdict REWORK (blocker: `DSPARK_MODEL=<path>` is IGNORED by
ds4-server.sh; arms would have been identical). Nothing executed. Morning
operator gate + one live probe required before scoring.

## What is being A/B'd
`mtp.2.confidence_head.proj.weight` in the DSpark support GGUF — official
q8_0 [4352,1] vs confcal1b (linear-4096 h-half, zero markov half, no bias;
val AUC .8453 vs .5448 baseline, ECE .0109; q8_0 requant calibration-silent
per S5; official head at the same split: .8360 but gate-.45 coverage 1.000
= INERT, confcal1b engages 9.2% of rows — S3b replay).

## Hard rules (S5 review, still binding)
1. Official file immutable (`0807a67f…679bc877`); swaps are copy-based via
   `tools/repack_dspark_tensor.py`; ledger records official sha + output
   sha side by side (current repacked sha `3365e6b4…72adc6cf` — regenerate
   if weights change).
2. confidence_proj binds AT BOOT -> every arm switch is a restart; no
   paired stats across a swap; re-anchor the window after the banner.
3. Deployable head form only: linear-4096, NO bias, zero markov half
   (all-zero block bytes = safe neutral) until S4 features exist
   engine-side.
4. Gate semantics: cut iff `sigmoid(logit) >= .45` (threshold on sigmoid).
5. Judge on acceptance stats (pooled accept rate, accept-LENGTH histogram,
   coverage), not AUC; tok/s is directional under live traffic.
6. `trainer/tests/test_gguf_dq.py::test_q8_0_*` green before any repack
   tool change.

## Boot lever (review BLOCKER — the v1 lever was dead)
`DSPARK_MODEL=…` is clobbered at ds4-server.sh:59 (hard assign, no `:-`)
AND overridden by `spec_used=$spec_model_path` in start_server. Correct
lever (no repo edit):
```
cp ds4-server.sh /tmp/ds4-server-ab.sh
# edit ONLY the DSPARK_PATHS "vision" entry (~line 203):
#   -> /Volumes/FireCuda520/ds4-train/repack_test/support_AB.gguf
cd /Users/naz/Projects/ds4   # repo root: relative gguf/, ./log, $SERVER_CMD
DS4_DSPARK_STATS=1 bash /tmp/ds4-server-ab.sh restart-collect   # ARM B
./ds4-server.sh restart-collect                                  # ARM A
```
`DS4_DSPARK_STATS=1` MUST appear in BOTH arms' boot commands (engine gate
`ds4_dspark_stats_enabled`, ds4.c:75280; ZERO 'ds4: DSpark stats' lines
exist in log history — the env was never set). Banner must show
`support: <path>, confidence: 0.45, exact: 1` (echo at ~416). Record
banner line + pid + both shas in the ledger. Rollback = plain
`./ds4-server.sh restart-collect` (pristine script boots the official
path; verified).

## P0 probe gate (before ANY scoring)
Boot arm A with stats env; fire ONE request incl. the tool-bearing prompt
via `python3 tools/bench_ab_3c.py --arm probe --skip-requests` after a
manual sweep, or the script's full sweep, and REQUIRE: >=1
`ds4: DSpark stats` line with cycles>0. If plain prompts yield cycles==0
(tool-traffic gating of the scheduler), score ONLY the tool-bearing
prompts (the conf head was calibrated on thinking/tool-agent traffic — a
plain-chat A/B would be an invisible experiment; script hard-errors on
both failure modes).

## Procedure (per arm)
A1. Build arm-B file: `python3 tools/repack_dspark_tensor.py
    gguf/…Vision-Exp-DSpark-support.gguf --repack
    'mtp.2.confidence_head.proj.weight=<weights.npz>#w_full_1x4352'
    --out <BLADE>/ds4-train/repack_test/support_AB.gguf`; verify printed
    changed-byte window + dequant max|delta| < 1e-3.
A1b. OFFLINE re-baseline BEFORE booting arm B (rule: retrained drafter
    head => re-baseline): tests/dspark_traffic_probe.sh +
    dspark_temp_battery.sh spawn their OWN engine (refuse while one runs)
    and hardcode SUPPORT=official — run with the engine DOWN and the
    SUPPORT override pointed at support_AB.gguf. This is NOT a live A/B
    step. (A0 old "log snapshot" step dropped: rotate_logs already
    cp-snapshots every boot.)
A2. Arm order A→B→A→B (first cycle informal, second SCORED). Switch via
    the boot lever above. Banner check + >=5 min post-boot before scoring
    (warm). Engine = 127.0.0.1:8001 (the collect/DSpark profile; 8002 is
    qwen WITHOUT DSpark — benching it yields no stats ever).
A3. `python3 tools/bench_ab_3c.py --port 8001 --arm X --out armX.json`
    (model id auto-discovered from /v1/models; byte-offset stats window
    from bench start — boot truncates the log, shrink detected;
    sessions==0 / cycles==0 hard errors; finish_reason + prompt_tokens
    recorded; accept_len_hist parsed to int lists).
A4. Restore arm A via plain `./ds4-server.sh restart-collect`.

## Decision rule (pre-registered, v2)
Scored cycles only (discard first scored rep as warm-informal). Arm B
advances iff pooled over scored B vs A: (i) pooled accept rate
(`accepted_draft/proposed`) + accept-LEN histogram shift toward longer
runs at >= equal coverage (histograms included), (ii) B's gate actually
engages (proposed/cycles similar, accept_rate direction consistent with
the offline +.0093 AUC and 9.2% gate-disagreement), (iii) tok/s:
DIRECTIONAL ONLY (mean/median/p95 recorded; n≈28 samples under live
traffic with background users — NOT a gate; a >5% mean regression in
BOTH scored windows is a STOP). (iv) quality ladder unchanged (from A1b
offline re-baseline). Any tie/mixed => stay official, iterate training.
Both arms run with parity/recs capture ON (symmetric I/O; the
DS4_TD_PARITY_MAX=200000 cap is capture-only, cannot alter drafting).

## Rollback
One command: `./ds4-server.sh restart-collect` (pristine script = official
support path). Repacked file stays on blade for forensics; never in the
repo, never overwriting `gguf/`.
