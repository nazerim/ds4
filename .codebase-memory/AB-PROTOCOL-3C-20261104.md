# AB-PROTOCOL 3C — live A/B for the retrained DSpark conf head

Status: PROTOCOL v3 (night 20261104, S6 + S4b review) — arm B = CONFCAL2
per independent review AB-GATE-RULING (v2 lever rework closed the
`DSPARK_MODEL=<path>` IGNORED blocker; arms were identical). Nothing
executed. Morning operator gate + one live probe required before scoring.

## What is being A/B'd
`mtp.2.confidence_head.proj.weight` in the DSpark support GGUF — official
q8_0 [4352,1] vs **confcal2** (`support_AB_confcal2.gguf`, sha
`5c0286dd…701013`): linear-4352 over the FULL engine feature
[h_d | markov_w1(prev_d)], NO bias — the ACTUAL engine computation
(pairing byte-verified: prev_0=seed, prev_d=props[d-1]); val AUC .8523
vs official .8360 (+1.63pp CI [+1.15,+2.11], all 4 slots), vs confcal1b
.8453 (+0.70pp CI [+0.44,+0.96]); ECE .0115; AUC unchanged through the
deployed q8_0 bytes (6 gate flips). Fallback arm B: confcal1b
(`support_confcal1b.gguf`, sha `3365e6b4…72adc6cf`, h-only, .8453).
Official head at the same split: .8360 but gate-.45 coverage 1.000
= INERT (S3b replay); confcal2 cuts 11.2% of val rows.

## Hard rules (S5 review, still binding)
1. Official file immutable (`0807a67f…679bc877`); swaps are copy-based via
   `tools/repack_dspark_tensor.py`; ledger records official sha + output
   sha side by side. ARM B CURRENT: `5c0286dde23c6c39df29f3408322c072ee27
   dfae414dedab6c6b4639ad701013` (confcal2, blade
   `/Volumes/FireCuda520/ds4-train/repack_test/support_AB_confcal2.gguf`,
   reviewer-built + sha-verified); fallback confcal1b `3365e6b4…72adc6cf`.
   Regenerate if weights change.
2. confidence_proj binds AT BOOT -> every arm switch is a restart; no
   paired stats across a swap; re-anchor the window after the banner.
3. Deployable head form only: confcal2 IS the engine form (linear over
   [h|markov_w1(prev)], no bias, [4352,1] DENSE — validator-pinned; the
   markov half is load-bearing: logit mass up to 4.73). confcal1b
   (zero markov half) remains the conservative fallback. NOT deployable:
   s4full1/s4full2 V1Head form (w1/w2 drift would change model_fp and
   invalidate disk-KV).
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
# edit ONLY the DSPARK_PATHS "vision" entry (line 213 - index 1 =
# "vision", NOT index 0 "0731": editing the wrong entry leaves the
# vision boot on the official path = silently identical arms; the
# mandatory banner check is the guard):
#   -> /Volumes/FireCuda520/ds4-train/repack_test/support_AB_confcal2.gguf
cd /Users/naz/Projects/ds4   # repo root: relative gguf/, ./log, $SERVER_CMD
DS4_DSPARK_STATS=1 bash /tmp/ds4-server-ab.sh restart-collect   # ARM B
./ds4-server.sh restart-collect                                  # ARM A
```
`DS4_DSPARK_STATS=1` MUST appear in BOTH arms' boot commands (engine gate
`ds4_dspark_stats_enabled`, ds4.c:75280; ZERO 'ds4: DSpark stats' lines
exist in log history — the env was never set). Banner must show
`support: <path>, confidence: 0.45, exact: 1` (echo at ~416 — TERMINAL
banner only; it is NOT written to log/ds4.log, do not grep the log for
it; log-side confirmation = line-1 'DSpark support model detected: …').
Record banner line + pid + both shas in the ledger. Rollback = plain
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
A1. Build arm-B file (ALREADY BUILT + sha-verified for confcal2 — reuse
    `/Volumes/FireCuda520/ds4-train/repack_test/support_AB_confcal2.gguf`,
    do NOT rebuild blind): `python3 tools/repack_dspark_tensor.py
    gguf/…Vision-Exp-DSpark-support.gguf --repack
    'mtp.2.confidence_head.proj.weight=/Volumes/FireCuda520/ds4-train/
    labels3c/run_confcal2/confcal1_weights.npz#w_full_1x4352'
    --out <BLADE>/ds4-train/repack_test/support_AB_confcal2.gguf`; verify
    printed changed-byte window (4,624 B) + dequant max|delta| < 1e-3
    (measured 6.37e-4) and output sha == `5c0286dd…701013`.
A1b. OFFLINE re-baseline BEFORE booting arm B (rule: retrained drafter
    head => re-baseline): tests/dspark_traffic_probe.sh +
    dspark_temp_battery.sh spawn their OWN engine (refuse while one runs)
    and hardcode SUPPORT=official — run with the engine DOWN and the
    SUPPORT override pointed at support_AB_confcal2.gguf. This is NOT a live A/B
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
    recorded; accept/draft_len_hist parsed from the REAL `k:count` pair
    format (ds4_format_len_hist, ds4.c:75285-75300 — zero bins skipped)
    into 17-bin lists, bins 0..16).
A4. Restore arm A via plain `./ds4-server.sh restart-collect`.

## Decision rule (pre-registered, v2 + v3 coverage definition)
Scored cycles only (discard first scored rep as warm-informal). Arm B
advances iff pooled over scored B vs A: (i) pooled accept rate
(`accepted_draft/proposed`) + accept-LEN histogram shift toward longer
runs at >= equal **COVERAGE = DSpark ENGAGEMENT (stats cycles /
first_tokens)**, NOT gate coverage — the official gate is INERT
(offline gate-coverage 1.000), so a literal gate-coverage reading of
"equal coverage" is unsatisfiable (review MINOR 3); histograms included,
(ii) B's gate actually engages (proposed/cycles similar to A, accept_rate
direction consistent with the offline +1.63pp AUC and the 1,004
gate-.45 disagreements vs official; confcal2 cuts 11.2% of offline rows
— engagement should stay in A's band, cut drafts are the low-confidence
ones), (iii) tok/s:
DIRECTIONAL ONLY (mean/median/p95 recorded; n≈28 samples under live
traffic with background users — NOT a gate; a >5% mean regression in
BOTH scored windows is a STOP). (iv) quality ladder unchanged (from A1b
offline re-baseline). Any tie/mixed => stay official, iterate training.
Both arms run with parity/recs capture ON (symmetric I/O; the
DS4_TD_PARITY_MAX=200000 cap is capture-only, cannot alter drafting).

### Threshold watch item (AB-GATE-RULING, keep .45)
confcal2 scores under-predict by ~1.5-2.5pp below 0.5 (offline gate
region pred .4606 vs obs .4854; obs at nominal [.425,.475] = .4664): at
the nominal .45 gate the TRUE accept rate is ≈.466-.485, i.e. arm B's
gate is marginally CONSERVATIVE — it trims a few borderline-good drafts,
not extra bad ones; this direction cannot inflate accept-rate
dishonestly. Expect B coverage slightly below the offline .8883.
IF live B coverage undershoots A by >4pp or the accept-length histogram
degrades, STOP and re-inspect calibration — do NOT "fix" the threshold.

## Rollback
One command: `./ds4-server.sh restart-collect` (pristine script = official
support path). Repacked file stays on blade for forensics; never in the
repo, never overwriting `gguf/`.
