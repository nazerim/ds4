# AB-PROTOCOL 3C — live A/B for the retrained DSpark conf head

Status: PROTOCOL **v4** (20261105 evening) — v3 executed and VERDICTED
(stay-official on toy-bench traffic, ledger `23bc4dd`); the regime
replay that followed PROVED the advantage lives at >=32k contexts
(cut-true-rate .36-.40 vs base .76-.81 = 62%-precision true-reject
excision; +2.27pp CI [+2.03,+2.51] AUC advantage LOCATED in big ctx;
official gate inert .9997-.9999 there; era-stable; ledger `4b08bb0`).
v4 = BIG-context decision leg + small-ctx parity leg, arm B =
**confcal3**. Nothing executed yet; operator gate + P0 required.

## What is being A/B'd (v4)
`mtp.2.confidence_head.proj.weight` in the DSpark support GGUF —
official q8_0 [4352,1] vs **confcal3**
(`support_AB_confcal3.gguf`, blade
`/Volumes/FireCuda520/ds4-train/repack_test/`, sha
`a25cf9eea460c5add0462da1be8d3ce06758c6e364a9b578f498637388dd4698`):
engine-form linear-4352 [h|markov_w1(prev)], trained cross-era on
207,624 cycles (days 13-15, 14 pids, 480,177 train rows). Val AUC
.8501 vs official .8276 (+2.25pp CI [+2.03,+2.48]), wins all 4 val
slots (+1.13/+2.58/+2.05/+3.62pp val-vs-val) and every val-bearing
pid slice. Deploy bytes verified: dequant rescore .8501, 62/49,281
gate flips (calibration-silent). Fallbacks: confcal2
(`5c0286dd…701013`), confcal1b (`3365e6b4…72adc6cf`). WATCH ITEM
FLIPPED vs v3: confcal3 gate OVER-predicts +1.6pp region-wide (n=7656
pred .4659 obs .4502 = marginally LOOSE; expect B cut-coverage ~1-2pp
milder than the offline .9138 -> B may keep slightly MORE drafts than
offline suggests).

## Hard rules (S5 review, still binding)
1. Official file immutable (`0807a67f…679bc877`); swaps are copy-based via
   `tools/repack_dspark_tensor.py`; ledger records official sha + output
   sha side by side. ARM B CURRENT: `a25cf9eea460c5add0462da1be8d3ce0
   6758c6e364a9b578f498637388dd4698` (confcal3, blade
   `/Volumes/FireCuda520/ds4-train/repack_test/support_AB_confcal3.gguf`,
   builder-verified: window/dequant/.8501-through-bytes rescore);
   fallbacks confcal2 `5c0286dd…701013`, confcal1b `3365e6b4…72adc6cf`.
   Regenerate if weights change.
2. confidence_proj binds AT BOOT -> every arm switch is a restart; no
   paired stats across a swap; re-anchor the window after the banner.
3. Deployable head form only: confcal2/3 ARE the engine form (linear over
   [h|markov_w1(prev)], no bias, [4352,1] DENSE — validator-pinned; the
   markov half is load-bearing: logit mass up to 4.73). confcal1b
   (zero markov half) remains the conservative fallback. NOT deployable:
   s4full1/s4full2 V1Head form (w1/w2 drift would change model_fp and
   invalidate disk-KV).
4. Gate semantics (v4 review fix - direction was INVERTED in v3 text):
   engine BREAKS the draft stream when `sigmoid(logit) < .45`
   (ds4.c:37064-37067; keep iff sigmoid >= threshold, matching
   eval3c `pred = s >= thresh`). Threshold is on the SIGMOID, not the
   logit.
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
#   -> /Volumes/FireCuda520/ds4-train/repack_test/support_AB_confcal3.gguf
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
A1. Build arm-B file (ALREADY BUILT + VERIFIED for confcal3 — reuse
    `/Volumes/FireCuda520/ds4-train/repack_test/support_AB_confcal3.gguf`,
    sha `a25cf9ee…88dd4698`; do NOT rebuild blind): `python3
    tools/repack_dspark_tensor.py gguf/…Vision-Exp-DSpark-support.gguf
    --repack 'mtp.2.confidence_head.proj.weight=/Volumes/FireCuda520/
    ds4-train/labels3c/run_confcal3/confcal1_weights.npz#w_full_1x4352'
    --out <BLADE>/ds4-train/repack_test/support_AB_confcal3.gguf`; verify
    printed changed-byte window (4,624 B @ 4,077,251,392) + dequant
    max|delta| < 1e-3 (measured 9.29e-4) and output sha == a25cf9ee…
    (re-verification of the deployed-bytes rescore: import
    `q8_0_dequant` from the repack tool — do NOT hand-roll the 34 B
    block layout, it bit twice).
A1b. OFFLINE re-baseline BEFORE booting arm B (rule: retrained drafter
    head => re-baseline): tests/dspark_traffic_probe.sh +
    dspark_temp_battery.sh spawn their OWN engine (refuse while one runs)
    and hardcode SUPPORT=official — run with the engine DOWN and the
    SUPPORT override pointed at support_AB_confcal3.gguf. **v4 REVIEW
    MUST-FIX: the scripts HARD-ASSIGN SUPPORT (traffic_probe.sh:10,
    temp_battery.sh:8) - an external env SUPPORT is silently ignored
    (same failure class as the v1 boot lever!). Use the proven v3
    method: cp both to /tmp, sed the SUPPORT line to the confcal3
    blade path, run the copies (with cd pinned to the repo root).**
    v3 ran this for confcal2 (RC 0/0, 8/10 shapes above baseline,
    toolcall shape -15%); CONFCAL3 NEEDS ITS OWN A1b RUN before its
    first boot. This is NOT a live A/B step. (A0 old "log snapshot"
    step dropped: rotate_logs already cp-snapshots every boot.)
A2. Arm order A→B→A→B (first cycle informal, second SCORED). Switch via
    the boot lever above. Banner check + >=5 min post-boot before scoring
    (warm). Engine = 127.0.0.1:8001 (the collect/DSpark profile; 8002 is
    qwen WITHOUT DSpark — benching it yields no stats ever).
A3. `python3 tools/bench_ab_3c.py --port 8001 --arm X --allow-no-stats
    --out armX.json` (model id auto-discovered from /v1/models;
    **v3-campaign correction: the engine flushes DSpark stats at
    session-free = pool evict/shutdown, so each boot ERA emits ONE
    aggregate line into ITS log, snapshotted by the next boot's
    rotation — score by parsing the rotated era file
    (`--skip-requests --offset 0`), never the live log mid-era**;
    shrink-detected byte offsets retained; finish_reason +
    prompt_tokens recorded; accept/draft_len_hist parsed from the REAL
    `k:count` pair format into 17-bin lists; small-context PARITY leg
    uses the existing prompt set. BIG LEG: `python3
    tools/bench_ab_3c.py --port 8001 --big-leg --big-temps 0 --out
    bigX.json` from the REPO ROOT (deterministic 50k/65k canned seeds,
    6 growing tool turns, per-turn content_sha256 - scorer drops+flags
    divergent pairs before touching tok/s).
    **CAPTURE REDIRECT RULE (dev-smoke-learned 20261105):
    ds4-server.sh resolves `${DS4_TRAIN_DUMP:-real}` /
    `${DS4_TD_PARITY:-real}` - ABSENT env RE-ARMS the real corpus;
    "unset to disable" (v4 text earlier) is WRONG for script-mediated
    boots. Redirect BOTH to /tmp dirs for any dev/smoke boot;
    DS4_TD_PARITY_MAX=0 never works (coerces to 4096).**
A4. Restore arm A via plain `./ds4-server.sh restart-collect`.

## Decision rule (pre-registered, v4 — two-leg traffic)
**Legs:** BIG (verdict-bearing): tool chains at ctx >=32k (seed ~40-60k
tokens of static text, then multi-turn growth with tool results
appended; fixed-seed paired t=0 core + t=1 reps). PARITY
(non-verdict): the v3 small-prompt set — guards official's claimed
<8k home; only a >5% paired-t0 drop there can STOP, it cannot advance B.
Arm B advances iff scored BIG-leg B vs A: (i) PAIRED t=0 big-ctx tok/s
mean delta >= 0 — the engine-level net of cut-vs-accelerate, the one
question offline cannot answer; (ii) proposed-per-cycle drop >= 8%
(verify-burn efficiency; offline expectation ~9% cut at >=32k with 63%
true-reject precision — a MUCH larger drop signals the flipped watch
item below); (iii) kept-set accept (accepted/proposed) not more than
1pp below A; (iv) accept-len hist full-5 share not down >3pp vs A;
(v) quality: finish_reason distribution sane + A1b (confcal3 file)
ladder unchanged. STOP-CONDITIONS: >5% paired-t0 big-leg regression in
EITHER scored window; **B gate-KEEP-coverage above the offline
expectation .9138 by >4pp (i.e. > .9538)** — v4 review fix: "overshoots
A" was vacuous (official keep-coverage ~.9998, inert; B can never
exceed it) — the loose-gate signature is B keeping materially MORE
than offline predicts; any engine errors/tripwires in the era line;
sentinel anomaly. Mixed/tie => stay official. Thresholds NOT moved mid-flight:
v4 evaluates confcal3 as-is.
Both arms run with parity/recs capture ON (symmetric I/O; the
DS4_TD_PARITY_MAX=200000 cap is capture-only, cannot alter drafting).

### Watch item v4 (flip of v3's, keep .45)
On the broad pool confcal3 OVER-predicts below-gate (gate region
n=7656: pred .4659 vs obs .4502 = +1.6pp; v3/confcal2 was -2.5pp
UNDER). Direction: marginally LOOSE — expect live B cut-coverage
slightly MILD (~1-2pp above offline .9138) and B kept-set accept
slightly BELOW the offline ideal; this direction cannot hide a
regression (it shows up as condition (ii) undershooting 8%). IF
(ii) undershoots badly OR (iii) breaks: STOP and inspect
large-context calibration on the sealed era; do NOT move .45.

## Rollback
One command: `./ds4-server.sh restart-collect` (pristine script = official
support path). Repacked file stays on blade for forensics; never in the
repo, never overwriting `gguf/`.
