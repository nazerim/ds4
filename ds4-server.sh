#!/bin/bash
set -euo pipefail

# ds4-server management script
# Copy to ds4 directory and run: ./ds4-server.sh [start|stop|restart|status]

SERVER_CMD="./ds4-server"
# Default-profile pid file; per-family profiles (qwen, v41) own their own and
# reassign PID_FILE in start_server. Keep the literal only here.
DEFAULT_PID_FILE="./ds4-server.pid"
PID_FILE="$DEFAULT_PID_FILE"
# model-aware ctx default (1M DeepSeek, 262k Qwen3.8, 256k V4.1) resolved in
# start_server; env CTX overrides all.
CTX="${CTX:-}"
PORT="${PORT:-}"   # per-model default resolved in start_server (DeepSeek 8001, Qwen 8002, V4.1 8003)
# Auth-gated reverse proxy in front of ds4-server. Keep the server on loopback;
# clients hit the proxy's LAN address. Requires DS4_API_KEY (refuses to start without).
PROXY_CMD="./auth_proxy.py"
PROXY_PID_FILE="./auth_proxy.pid"
PROXY_PORT="${PROXY_PORT:-8100}"   # LAN-facing proxy port (8001=DeepSeek, 8002=Qwen, 8003=V4.1 stay loopback)
PROXY_HOST="${PROXY_HOST:-0.0.0.0}"
# Bind address. Default 127.0.0.1 (loopback). Override with HOST env, e.g.
# HOST=0.0.0.0 or HOST=192.168.1.20 to expose the server on the LAN.
HOST="${HOST:-127.0.0.1}"   # loopback by design; LAN clients go through auth_proxy (start-proxy)
# KV store lives on the external FireCuda520 blade, mirroring the qwen profile
# (QWEN_KV_DIR) — separate dirs keep budgets honest and the engine's fingerprint
# gate would reject foreign-model checkpoints anyway.  Budget default 262144
# (256 GiB, same scale as qwen; halved from 512 GiB per operator 2026-11-02):
# a single ultra-long conversation
# (328k+ tokens; the DeepSeek ctx default is 1048576, where ladders run
# ~100-140 GiB) holds a continued-anchor ladder, and two such conversations
# should coexist — worst-case twin 140s run tight against 256, the
# retire-grace policy (KV_CACHE_RETIRE_GRACE) protects recently-live
# lineages while the trim settles.  SSD endurance is a non-issue at the observed
# ~0.3 TiB/day write rate on a 2 TB drive.  See DS4FORK.md "KVCACHE —
# Deep Divergence Investigation" and PLAN-KV-REWRITE.md.
KV_DIR="${KV_DIR:-/Volumes/FireCuda520/ds4-kv-deepseek}"
KV_SIZE="${KV_SIZE:-262144}"
# KV anchor retention: small_dense keeps ALL anchors ≤ this token count sticky
# (default 16384).  Raising it bounds head divergences (e.g. opencode re-renders
# an edited AGENTS.md near the head) to restart from a deeper anchor instead of
# falling to the 16k base — see DS4FORK.md "KVCACHE — Deep Divergence Investigation".
KV_SMALL_DENSE="${KV_SMALL_DENSE:-49152}"
# Retire-grace (seconds): a lineage whose leaf was touched within this window is
# exempt from PHASE C retirement (frontier pinning) — stops session-switch churn
# where a just-live session's whole ladder was retired mid-switch.  0 = disabled.
KV_RETIRE_GRACE="${KV_RETIRE_GRACE:-3600}"
# Divergence anchors: after a miss that loads anchor A < common, store a cold
# anchor at exactly `common` once the rebuild reaches it, so a future identical
# miss starts from `common` instead of A.  0 = disabled.
KV_MAX_DIVERGENCE_ANCHORS="${KV_MAX_DIVERGENCE_ANCHORS:-8}"
LOG_DIR="./log"
LOG_FILE="$LOG_DIR/ds4.log"
TOKENS="${TOKENS:-}"   # default completion cap when a request omits max_tokens (not a context limit); model-aware default resolved in start_server
# Two DISTINCT speculative-decoding pathways, not interchangeable:
#  - DSpark  (--dspark): block drafter; REQUIRES the 0731 support GGUF and 0731
#    main models only (checkpoint-specific). Non-greedy uses opportunistic
#    sampling; default ON since 2026-11-06 (--mtp-exact-sampling, target
#    distribution); DSPARK_EXACT=0 restores the legacy opportunistic mode.
#  - Legacy MTP (--mtp-draft): one-stage nextn drafter (May 2026 GGUF). Pass the
#    drafter via --mtp-model WITHOUT --dspark; ds4 detects the kind by tensor
#    names. Upstream: DSpark replaces this for the 0731 checkpoint.
DSPARK_MODEL="gguf/DeepSeek-V4-Flash-DSpark-support-0731.gguf"
MTP_MODEL="gguf/DeepSeek-V4-Flash-MTP-Q4K-Q8_0-F32.gguf"
# Vision encoder sidecar.  The default model (./ds4flash.gguf, re-linked by
# `./download_model.sh ds4f-vision-q2-q4`) is the DeepSeek V4 Flash Vision-Exp
# checkpoint and is served WITH vision.  start-0731 and other text-only models
# never receive --vision (the encoder does not match their checkpoint).
VISION_ENCODER="gguf/DeepSeek-V4-Flash-Vision-Encoder.gguf"
# Qwen3.8 Flash Next (qwen4exp2): start-qwen / restart-qwen.  Q4_K on a 128 GB
# M5 Max with 262144 ctx (oMLX-proven); built-in MTP needs no second file; the
# vision tower is a separate mmproj.  Own pid/log/KV dir so switching models
# never re-purposes DeepSeek disk checkpoints (the engine's fingerprint gate
# rejects them anyway, but separate dirs keep budgets honest).
QWEN_MODEL="${QWEN_MODEL:-gguf/Qwen3.8-Flash-Next-Q4.gguf}"
QWEN_VISION="${QWEN_VISION:-gguf/mmproj-Qwen3.8-Flash-Next-Q8_0.gguf}"
# YaRN: native context is 262144; QWEN_CTX=524288 pairs with
# DS4_QWEN4_YARN_FACTOR=2 below (static YaRN, HF recipe, mscale ~1.07 -
# the model card's own beyond-262k extension, at a slight quality cost on
# short prompts).  Set QWEN_CTX=262144 + QWEN_YARN=0 for native-only.
QWEN_CTX="${QWEN_CTX:-524288}"
QWEN_YARN="${QWEN_YARN:-2}"
# QWEN_EXACT_VERIFY=1 (default since 2026-10-03): SINGLE-TREE spec verify -
# T=1 matvecs share the row-invariant mv_ext tree with speculative verify,
# so spec output is bit-identical to serial by construction (grouped MoE
# exact path + row-exact paired HC gate/mix, metal pair_rowe kernel).
# Costs ~8% wall vs the drifting default (60.0s vs 55.4s on the
# single-session acceptance battery) and defines the canonical golden tree
# (tests/test-vectors/qwen38-flashnext).  QWEN_EXACT_VERIFY=0 restores the
# historical drifting dispatch for perf baselining only.
QWEN_EXACT_VERIFY="${QWEN_EXACT_VERIFY:-1}"
QWEN_TOKENS="${QWEN_TOKENS:-65536}"
# Batched MTP across concurrent sessions (upstream server flag); 0 = off.
# Qwen3.8 on Metal needs BOTH --batched-session and --mtp to speculate in
# batches (docs/SERVER.md); QWEN_MTP=0 drops --mtp (plain batched target
# decoding).
# Concurrency 1 is the intended 512k mode; 2 is the guardrail (8 resident
# 512k sessions request 255 GiB of buffers against a ~122 GiB Metal wired
# ceiling).  At 262k ctx, 8 fits fine - override via env either way.
if [ -z "${QWEN_BATCH_SESSION:-}" ]; then
  if [ "${QWEN_CTX}" -gt 262144 ] 2>/dev/null; then QWEN_BATCH_SESSION=2; else QWEN_BATCH_SESSION=8; fi
fi
QWEN_MTP="${QWEN_MTP:-1}"
# Prefill chunk used while another generation is in flight (server default: 128).
# Raising it trades interleaving frequency for prefill throughput: a bigger slice
# means FEWER interleaved decode steps, so instantaneous decode t/s during a
# concurrent prefill can be lower, but the prefill finishes several times sooner and
# decode stops being starved sooner.  Measured with 128: a resumed 137k-token session
# re-prefilled 88479 tokens in 74.3 s (~1200 t/s when the engine is idle, ~400 t/s
# when a generation is active).  Set QWEN_MIXED_QUANTUM=128 to restore upstream
# behaviour.  GLM-5.3 already floors this at 1024 in the server, so 512 is well inside
# the supported range for that family.
QWEN_MIXED_QUANTUM="${QWEN_MIXED_QUANTUM:-512}"
QWEN_PORT="${QWEN_PORT:-8002}"
QWEN_PID_FILE="./ds4-server-qwen.pid"
QWEN_KV_DIR="${QWEN_KV_DIR:-/Volumes/FireCuda520/ds4-kv-qwen}"
# DeepSeek V4.1 Flash: start-v41 / restart-v41. Own GGUF/tokenizer/graph - V4
# Flash weights and DSpark support files are NOT interchangeable with it. On a
# 128 GB machine the only viable mode is SSD streaming (152 GiB main weights;
# the 189 GiB Engram rows always stream from the file either way), so the GGUF
# must live on the fast INTERNAL SSD (gguf/ here), never the blade. Vision is
# Metal-only and optional (text serves without the encoder). Own pid/log/KV
# profile, mirroring the qwen split. See docs/MODELS.md#deepseek-v41-flash.
V41_MODEL="${V41_MODEL:-gguf/DeepSeek-V4.1-Flash-1017-IQ2_XXS-Q2_K-imatrix.gguf}"
V41_VISION="${V41_VISION:-gguf/DeepSeek-V4.1-Flash-Vision.gguf}"
# 512k DEFAULT since 2026-11-09 finalization (was 262144; the env-override era
# is over - doom R3 validated 319,646 depth on this box: >262k band decode
# 23.5 t/s, accept 74.3%, age-flat). Static ctx buffers 13.5 GiB at 512k
# (ds41_graph_bytes); the real budget is the SSD-streaming expert cache
# (auto-trims 73.96->70.96 GiB) under the Metal wired ceiling, not KV.
# Engine hard ceiling: ctx > 1048576 rejected (model trained natively 1M).
# Each concurrent slot needs its own context memory AND contends expert-cache
# loads on one SSD feed - single slot is the DECIDED mode for streaming on
# this box (operator 2026-11-06), not a pending experiment. No --batched-session.
V41_CTX="${V41_CTX:-524288}"
V41_TOKENS="${V41_TOKENS:-65536}"
V41_PORT="${V41_PORT:-8003}"
V41_PID_FILE="./ds4-server-v41.pid"
V41_KV_DIR="${V41_KV_DIR:-/Volumes/FireCuda520/ds4-kv-v41}"
# 256 GiB (262144 MiB) - ALIGNED with ds4 (KV_SIZE) and qwen (QWEN_KV_SIZE)
# per operator finalization 2026-11-09: all three lane KV stores budget 256 GiB
# each on the blade. Ceilings, not reservations; ~4.2 GiB per full-512k session
# plus 1-4 GiB anchor ladders. CAUTION: 3x256 GiB of ceilings vs ~483 GiB blade
# free - never let all three lanes run hot simultaneously.
V41_KV_SIZE="${V41_KV_SIZE:-262144}"
# 1 = --ssd-streaming (the 128 GB mode); 0 = fully resident (needs 256 GB+).
V41_SSD_STREAM="${V41_SSD_STREAM:-1}"
# Cache-miss trace: set TRACE_PATH to a file path (e.g. ./log/ds4.trace) to make
# the server write the exact cache-decision + first-mismatch token window for
# every request. Used for debugging KV divergence (see DS4FORK.md KVCACHE —
# Deep Divergence Investigation). Empty = tracing off.
TRACE_PATH="${TRACE_PATH:-}"

# Vision image budget (see README, ds4_server.c): N > 0 opts into auto-reduce
# of over-budget multimodal histories (keep last N, drop oldest). The upstream
# default (unset in the server binary) rejects requests over 16 images.
# DEFAULT 256 since 2026-11-10 (operator; was 128): transcript drop-1-per-turn
# past KEEP is catastrophic at depth (prefix break => ~30 min/turn re-prefill
# at 360k), while reduce never fires below SERVER_MAX_IMAGES=256 - attestation/
# trailer capacity is 256 (dynamic), so KEEP=256 = serve whole image histories
# unmodified and keep disk-resilience, and client compaction rewrites history
# long before the count matters. 0 = disable auto-reduce in the server too.
DS4_VISION_KEEP_IMAGES="${DS4_VISION_KEEP_IMAGES:-256}"
export DS4_VISION_KEEP_IMAGES

# Hard thinking-token budget (server default; enforced in ds4_server.c): caps
# thinking tokens per span at N; at the cap the server injects a short wrap-up
# line + close tag and the model continues into its visible answer. 0/unset =
# no cap. Per-request overrides: reasoning_budget_tokens / thinking_budget_tokens
# (alias) / Anthropic thinking.budget_tokens; a named 0 opts that request out.
# Details: .codebase-memory/THINKING-BUDGET-RESULTS-20261102.md
DS4_THINKING_BUDGET_TOKENS="${DS4_THINKING_BUDGET_TOKENS:-16384}"
export DS4_THINKING_BUDGET_TOKENS

# Thinking-loop tripwire (server default ON; ds4_server.c): force-closes a
# thinking span once exact token-level repetition sustains >=1200 tokens
# (per-request: thinking_tripwire / tripwire_min_tokens; named false opts
# out). Default history: 2400 at ship; lowered to 1200 same night from the
# 16k live specimen (drift-converging loop would have tripped at ~10.4k vs
# a 15.7k near-miss / 16k budget chop; healthy-days control: 0/122 segments
# even at 600). CAPTURE ERA LABEL: from starts using this script onward,
# thinking spans may close early on loops -> trainer loops.py needs the
# tripwire regex (span geometry same as budget chop lines). Details:
# .codebase-memory/PLAN-TRIPWIRE-20261102.md + TRIPWIRE-RESULTS-20261102.md
# + TRIPWIRE-FOLLOWUP-RESULTS-20261103.md
DS4_THINK_TRIPWIRE="${DS4_THINK_TRIPWIRE:-1}"
export DS4_THINK_TRIPWIRE
DS4_TRIPWIRE_MIN_TOKENS="${DS4_TRIPWIRE_MIN_TOKENS:-1200}"
export DS4_TRIPWIRE_MIN_TOKENS

# --- Data collection mode (start-collect / restart-collect) ---
# One-key arming of soak corpus + parity dumps for the Vision-Exp dspark
# EXACT-sampling era (decision #10 + bank runbook in .codebase-memory/
# DSPARK-STATE-20261102.md). Every value is overridable from the outside,
# e.g. start a fresh parity experiment dir:
#   DS4_TD_PARITY=/Volumes/FireCuda520/ds4-parity/20261104-s4 ./ds4-server.sh restart-collect
# CRITICAL: a PLAIN start/restart runs with ZERO capture (no --dspark ->
# no draft cycles) AND flips the model fingerprint, invalidating every
# disk-KV file (deep sessions then re-prefill from token 0). Collection
# restarts must go through this arm or the full 4-env incantation.
COLLECT_TRAIN_DUMP="${DS4_TRAIN_DUMP:-/Volumes/FireCuda520/ds4-train/active}"
COLLECT_PARITY_DIR="${DS4_TD_PARITY:-/Volumes/FireCuda520/ds4-parity/20261103-s3}"
COLLECT_PARITY_MAX="${DS4_TD_PARITY_MAX:-200000}"
COLLECT_EXACT="${DSPARK_EXACT:-1}"
# Rewind-window fix (PLAN-DSML-REWIND).  A single-token decode used to destroy
# the armed speculative-rewind window, and the exact-sampling resample replays
# its boundary token through that same path - so every DSML turn after the first
# mode-boundary crossing re-prefilled the whole context (measured: 601 s for
# 212,546 tokens, twice in one turn, the second one discarded by a client
# timeout).  1 keeps the window covering its block; 0 restores the previous
# engine's decisions exactly.  TP leader and worker must be given the same value.
COLLECT_REWIND_RESUME="${DS4_REWIND_RESUME:-1}"

arm_collect_env() {
    if [ ! -d /Volumes/FireCuda520 ]; then
        echo "Error: /Volumes/FireCuda520 not mounted - refusing to start"
        echo "       armed; capture would fail SILENTLY at first fopen." >&2
        return 1
    fi
    mkdir -p "$COLLECT_TRAIN_DUMP" "$COLLECT_PARITY_DIR" || return 1
    DS4_TRAIN_DUMP="$COLLECT_TRAIN_DUMP"
    DS4_TD_PARITY="$COLLECT_PARITY_DIR"
    DS4_TD_PARITY_MAX="$COLLECT_PARITY_MAX"
    DSPARK_EXACT="$COLLECT_EXACT"
    DS4_REWIND_RESUME="$COLLECT_REWIND_RESUME"
    export DS4_TRAIN_DUMP DS4_TD_PARITY DS4_TD_PARITY_MAX DSPARK_EXACT DS4_REWIND_RESUME
    echo "collect arm: TRAIN_DUMP=$DS4_TRAIN_DUMP"
    echo "             PARITY=$DS4_TD_PARITY (cap $DS4_TD_PARITY_MAX per process)"
    echo "             DSPARK_EXACT=$DSPARK_EXACT (0 = seed-batch era, mirror-invalid data)"
    echo "             DS4_REWIND_RESUME=$DS4_REWIND_RESUME (0 = pre-fix rewind window, full rebuilds on DSML turns)"
    echo "verify after start: banner 'DSpark ... exact: $DSPARK_EXACT'; capture banners +"
    echo "  train_pid<N>.recs appear only on the first TOOL-BEARING request (dspark"
    echo "  drafts tool traffic only). Check: grep -E 'capture ACTIVE|draft dump"
    echo "  ACTIVE' $LOG_FILE"
}

# Alternative model map: short name -> full GGUF path
# Add entries here for each model variant. Use `start-<name>` / `restart-<name>`.
# Uses parallel indexed arrays (bash 3.2 compatible — macOS default).
MODEL_KEYS=("0731" "vision" "qwen" "v41")
MODEL_PATHS=(
    "gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"
    "gguf/DeepSeek-V4-Flash-Vision-Exp-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8.gguf"
    "$QWEN_MODEL"
    "$V41_MODEL"
)

# Alternative DSpark (draft) model map: short name -> full GGUF path.
# Used by start-<model>-dspark / restart-<model>-dspark. If a key has no
# entry, the default DSPARK_MODEL above is used instead.
DSPARK_KEYS=("0731" "vision" "v41")
DSPARK_PATHS=(
    "/Users/naz/Projects/ds4/gguf/DeepSeek-V4-Flash-DSpark-support-0731.gguf"
    "gguf/DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf"
    "gguf/DeepSeek-V4.1-Flash-DSpark.gguf"
)
# Per-model legacy MTP drafter map (start-<model>-mtp).
MTP_KEYS=()
MTP_PATHS=()

resolve_default_link() {
  # Fully expand the ds4flash.gguf symlink chain (readlink resolves one hop).
  local t="ds4flash.gguf" next
  while [ -L "$t" ]; do
    next=$(readlink "$t")
    case "$next" in /*) t="$next" ;; *) t="$(dirname "$t")/$next" ;; esac
  done
  printf '%s\n' "$t"
}

# Blade guard: a /Volumes KV path whose mount is gone must NOT mkdir onto
# the internal SSD (silent fallback that bit us on 2026-10-03 - the store
# was "empty" because it had been created under an unmounted volume root).
# Compare the device id of the volume root against the SSD's root device:
# an unmounted-but-present /Volumes/<name> dir resolves to disk3s1s1, not
# the blade, so a plain -d check would wrongly pass. Checked pre-stop in
# preflight_engine (restart with an unmounted blade must not kill a live
# engine) AND again here post-profile-resolution before any mkdir.
kv_volume_ok() {
  local dir="$1"
  case "$dir" in
    /Volumes/*) ;;
    *) return 0 ;;
  esac
  local kv_vol="${dir#/Volumes/}"
  kv_vol="/Volumes/${kv_vol%%/*}"
  if [ ! -d "$kv_vol" ] || \
     [ "$(df -P "$kv_vol" 2>/dev/null | awk 'NR==2{print $1}')" = \
       "$(df -P / | awk 'NR==2{print $1}')" ]; then
    echo "Error: KV volume not mounted: $kv_vol (KV dir $dir)."
    echo "       Mount the blade or point KV_DIR/QWEN_KV_DIR/V41_KV_DIR at a live path."
    return 1
  fi
  return 0
}

# Speculation-family gate. The default support files are checkpoint-specific
# V4 Flash artifacts (0731 DSpark support / May-2026 nextn drafter); per-model
# entries live in DSPARK_KEYS (specp = resolved per-model path, empty => the
# checkpoint-specific default would be used). Runs pre-stop in preflight_engine
# and again in start_server (plain-start arm) - keep both arms in sync here.
spec_gate_ok() {
  local target="$1" specm="$2" specp="${3:-}"
  [ -n "$specm" ] || return 0
  case "$target" in
    *V4.1*|*v4.1*)
      # V4.1 has its own drafter (DSPARK_KEYS "v41", built by
      # gguf-tools/deepseek41_dspark.py). The 0731/nextn defaults are V4 Flash
      # artifacts, so an EMPTY specp (pathway default) must be refused here.
      case "$specm" in
        mtp)
          echo "Error: the legacy nextn drafter is a V4 Flash artifact."
          echo "       V4.1 uses its own DSpark drafter: start-v41-dspark."
          return 1 ;;
        dspark)
          if [ -z "$specp" ]; then
            echo "Error: V4.1 needs its own support GGUF (DSPARK_KEYS 'v41' not resolved)."
            echo "       Build it: gguf-tools/deepseek41_dspark.py (see docs/SPECULATIVE_DECODING.md)."
            return 1
          fi ;;
      esac ;;
    *Qwen*|*qwen*|*QWEN*)
      case "$specm" in
        mtp)
          echo "Error: the legacy nextn drafter is a V4 Flash artifact."
          echo "       Qwen3.8 uses its built-in MTP (start-qwen already sets --mtp)."
          return 1 ;;
        dspark)
          if [ -z "$specp" ]; then
            echo "Error: Qwen3.8 has no DSpark drafter (built-in MTP only)."
            return 1
          fi ;;
      esac ;;
    *Vision*)
      # (broad pattern mirrors the deleted plain-start guard: any Vision-named
      # checkpoint refuses the 0731 default support; V4.1's own Vision file is
      # caught by the earlier *V4.1* arm.)
      if [ "$specm" = "dspark" ] && [ -z "$specp" ]; then
        echo "Error: the Vision checkpoint needs its own DSpark drafter (0731 support is checkpoint-specific)."
        echo "       Use start-vision-dspark (resolves the per-model support entry)."
        return 1
      fi ;;
  esac
  return 0
}

lookup_model() {
    local key="$1"
    local i
    for i in "${!MODEL_KEYS[@]}"; do
        if [ "${MODEL_KEYS[$i]}" = "$key" ]; then
            echo "${MODEL_PATHS[$i]}"
            return 0
        fi
    done
    return 1
}

lookup_mtp_model() {
    local key="$1"
    local i
    for i in "${!MTP_KEYS[@]}"; do
        if [ "${MTP_KEYS[$i]}" = "$key" ]; then
            echo "${MTP_PATHS[$i]}"
            return 0
        fi
    done
    return 1
}

lookup_dspark_model() {
    local key="$1"
    local i
    for i in "${!DSPARK_KEYS[@]}"; do
        if [ "${DSPARK_KEYS[$i]}" = "$key" ]; then
            echo "${DSPARK_PATHS[$i]:-}"
            return 0
        fi
    done
    return 1
}
# Legacy MTP only: max autoregressive draft tokens. The server speculation gate
# needs > 1, so depth 1 (the old default) silently disables speculation.
MTP_DRAFT="${MTP_DRAFT:-2}"
MTP_MARGIN="${MTP_MARGIN:-3}"
# 0.6 balances speculation acceptance vs verification cost; 0.9 was too
# conservative, rejecting most drafts and negating MTP throughput gains.
# 0.45 (2026-10-29 bounding pass): Vision-Exp drafter + real traffic => +1.2%
# temp0 / +3.6% temp1 (6/6 probes) vs 0.6, model_fp UNCHANGED (no ladder-
# rebuild tax; only the dspark on/off toggle changes fp).  Re-baseline if the
# drafter head is ever retrained (PLAN-CAPTURE-PIPELINE-20261029.md).
DSPARK_CONFIDENCE="${DSPARK_CONFIDENCE:-0.45}"
# DSpark scheduler engagement window: Mac default is 4 probe-cycles (noisy per
# ds4.c comment; TP/ROCm fast-paths already use 16).  12 = measured smoother
# re-engage without losing the low-acceptance escape.  Runtime env read.
DSPARK_SCHED_WINDOW="${DS4_DSPARK_SCHEDULER_WINDOW:-12}"
# DSPARK_EXACT=1 adds --mtp-exact-sampling: at non-zero temperature the drafts
# follow the ordinary target distribution instead of opportunistic greedy-suffix
# matching (default confidence 0.8 when unset explicitly).
# DEFAULT 0 since 2026-11-09 finalization (was 1): noexact is the validated
# production lane - tool-spec +6.7% med / all-spec 30/30 speculating / corewars
# overnight 67.2% / doom R3 73.9% over 42k cycles at 319k depth. Set
# DSPARK_EXACT=1 for exact-sampling lanes (collect-era parity, mirror-valid
# data - see arm_collect_env banner).
DSPARK_EXACT="${DSPARK_EXACT:-0}"
# Lane-standard dspark envs (2026-11-09 finalization):
#  TOOLLESS_NOEXACT=1 - tool-less chat speculation on noexact lanes (DSML
#    suppression mask/restore at the 4 sampling sites); code default stays off,
#    the lane opts in. STATS=1 prints per-request `dspark req:` lines - all
#    lane analysis (collect/watch scripts, band tables) depends on them.
DS4_DSPARK_TOOLLESS_NOEXACT="${DS4_DSPARK_TOOLLESS_NOEXACT:-1}"
DS4_DSPARK_STATS="${DS4_DSPARK_STATS:-1}"
export DS4_DSPARK_TOOLLESS_NOEXACT DS4_DSPARK_STATS
MAX_LOG_ROTATIONS=10

# Debug mode: set DEBUG=1 to enable verbose output
if [ "${DEBUG:-0}" = "1" ]; then
  set -x
fi

rotate_logs() {
  local base
  base=$(basename "$LOG_FILE")
  if [ -f "$LOG_FILE" ]; then
    local ts
    ts=$(date +%Y%m%d-%H%M%S)
    cp "$LOG_FILE" "$LOG_DIR/$base.$ts"
    echo "Rotated previous log to $LOG_DIR/$base.$ts"
  fi

  # Keep only the last MAX_LOG_ROTATIONS rotated logs (per log family)
  local old_logs
  old_logs=$(ls -1t "$LOG_DIR/$base".* 2>/dev/null || true)
  if [ -n "$old_logs" ]; then
    echo "$old_logs" | tail -n +$((MAX_LOG_ROTATIONS + 1)) | while IFS= read -r f; do
      rm -f "$f"
    done
  fi
}

start_server() {
  local model_path="${1:-}"
  # spec mode: "" (none) | "mtp" (legacy one-stage) | "dspark" (block drafter)
  local spec_mode="${2:-}"
  local spec_model_path="${3:-}"

  # Model-aware runtime resolution: pid/log/kv/ctx defaults per engine family.
  local resolved_link
  IS_V41=0
  resolved_link=$(resolve_default_link)
  case "${model_path:-$resolved_link}" in
    *V4.1*|*v4.1*)
      # DeepSeek V4.1 Flash: own profile; SSD streaming is this box's memory
      # mode (see V41_* header). The rewind-window gate is inert here.
      IS_QWEN=0
      IS_V41=1
      CTX="${CTX:-$V41_CTX}"
      TOKENS="${TOKENS:-$V41_TOKENS}"
      PORT="${PORT:-$V41_PORT}"
      PID_FILE="$V41_PID_FILE"
      KV_DIR="$V41_KV_DIR"
      KV_SIZE="$V41_KV_SIZE"
      LOG_FILE="$LOG_DIR/ds4-v41.log"
      ;;
    *Qwen3.8*|*qwen3.8*)
      IS_QWEN=1
      CTX="${CTX:-$QWEN_CTX}"
      if [ "$QWEN_YARN" != "0" ] && [ "$CTX" -gt 262144 ]; then
        export DS4_QWEN4_YARN_FACTOR="$QWEN_YARN"
      fi
      if [ "$QWEN_EXACT_VERIFY" = "1" ]; then
        export DS4_QWEN4_VERIFY_SINGLE_TREE=1
        export DS4_QWEN4_MOE_GROUP_EXACT=1
        export DS4_QWEN4_VERIFY_HC_PAIR=1
      fi
      TOKENS="${TOKENS:-$QWEN_TOKENS}"
      PORT="${PORT:-$QWEN_PORT}"
      PID_FILE="$QWEN_PID_FILE"
      KV_DIR="$QWEN_KV_DIR"
      KV_SIZE="${QWEN_KV_SIZE:-262144}"   # qwen ladder store lives on the external blade
      LOG_FILE="$LOG_DIR/ds4-qwen.log"
      # Cache-miss trace is opt-in for the qwen runtime too: start-qwen no
      # longer pins a default path. Set TRACE_PATH to enable (the value is
      # respected as given; the canonical qwen location is
      # $LOG_DIR/ds4-qwen.trace). Empty = off, as documented.
      ;;
    *)
      IS_QWEN=0
      CTX="${CTX:-1048576}"
      TOKENS="${TOKENS:-384000}"
      PORT="${PORT:-8001}"
      # Rewind-window fix (PLAN-DSML-REWIND): a single-token decode cleared the
      # armed speculative-rewind window, and the exact-sampling resample decodes
      # through that path, so DSML turns re-prefilled the whole kept context
      # (measured 601 s for 212,546 tokens, twice in one turn).  Default ON for
      # this family so a plain restart[-vision-dspark] keeps it; an inherited or
      # explicit DS4_REWIND_RESUME=0 restores the previous behaviour exactly.
      # The gate is inert for the Qwen/GLM/DS4.1 families, so it is not set there.
      export DS4_REWIND_RESUME="${DS4_REWIND_RESUME:-1}"
      ;;
  esac
  # Speculation-family gate, before any state touch (mirrored pre-stop in
  # preflight_engine for the restart arm).
  spec_gate_ok "${model_path:-$resolved_link}" "$spec_mode" "$spec_model_path" || return 1
  # One engine at a time: every engine's pid file except this profile's.
  local OTHER_PID_FILES other_pid
  if [ "${IS_V41}" = "1" ]; then
    OTHER_PID_FILES="$DEFAULT_PID_FILE $QWEN_PID_FILE"
  elif [ "${IS_QWEN}" = "1" ]; then
    OTHER_PID_FILES="$DEFAULT_PID_FILE $V41_PID_FILE"
  else
    OTHER_PID_FILES="$QWEN_PID_FILE $V41_PID_FILE"
  fi
  local OTHER_PID_FILE
  for OTHER_PID_FILE in $OTHER_PID_FILES; do
    if [ -f "$OTHER_PID_FILE" ]; then
      other_pid=$(cat "$OTHER_PID_FILE")
      if kill -0 "$other_pid" 2>/dev/null; then
        echo "Error: the other model is loaded (PID $other_pid, $OTHER_PID_FILE)."
        echo "       One engine at a time - use restart[-qwen|-v41] to stop it and switch,"
        echo "       or: $0 stop"
        return 1
      fi
      rm -f "$OTHER_PID_FILE" "$OTHER_PID_FILE.port"
    fi
  done

  if [ -f "$PID_FILE" ]; then
    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      echo "ds4-server is already running (PID: $pid)"
      return 1
    else
      rm -f "$PID_FILE" "$PID_FILE.port"
    fi
  fi

  # Blade guard (see kv_volume_ok): the profile's KV dir is resolved above.
  kv_volume_ok "$KV_DIR" || return 1

  # Ensure KV cache and log directories exist
  mkdir -p "$KV_DIR" "$LOG_DIR"

  # Rotate and clean up old logs
  rotate_logs

  # Choose the support/draft model (per-model override, else pathway default)
  local spec_used=""
  case "$spec_mode" in
    mtp)    spec_used="$MTP_MODEL" ;;
    dspark) spec_used="$DSPARK_MODEL" ;;
  esac
  if [ -n "$spec_mode" ] && [ -n "$spec_model_path" ]; then
    spec_used="$spec_model_path"
  fi

  # Validate the support model exists if requested
  if [ -n "$spec_mode" ] && [ ! -f "$spec_used" ]; then
    echo "Error: $spec_mode support model not found: $spec_used"
    return 1
  fi

  if [ -n "$model_path" ]; then
    echo "Starting ds4-server on port $PORT with model $model_path (ctx: $CTX)..."
  else
    echo "Starting ds4-server on port $PORT (ctx: $CTX)..."
  fi
  case "$spec_mode" in
    mtp)
      echo "Legacy MTP speculative decoding enabled (drafter: $spec_used, draft: $MTP_DRAFT)"
      ;;
    dspark)
      echo "DSpark speculative decoding enabled (support: $spec_used, confidence: $DSPARK_CONFIDENCE, exact: $DSPARK_EXACT)"
      ;;
  esac
  echo "Logging to $LOG_FILE"

  # Vision arguments: the default model (./ds4flash.gguf) is Vision-Exp once
  # `download_model.sh ds4f-vision-*` has relinked it; serve it WITH the
  # encoder, and only then.  A Vision-Exp link without the encoder is fatal
  # (image support would silently vanish).
  DEFAULT_MODEL_RESOLVED="$resolved_link"   # same full-chain resolution as the profile case
  VISION_ARGS=()
  if [ "${IS_V41:-0}" = "1" ]; then
    # V4.1 Flash: streaming memory mode + optional matching encoder. The V4
    # Flash encoders do NOT work with V4.1; text serves fine without one.
    if [ "$V41_SSD_STREAM" = "1" ]; then
      VISION_ARGS+=(--ssd-streaming)
      echo "V4.1 Flash: SSD streaming on (V41_SSD_STREAM=0 only on 256 GB+ resident hosts)"
    fi
    if [ -f "$V41_VISION" ]; then
      VISION_ARGS+=(--vision "$V41_VISION")
      echo "V4.1 vision encoder attached: $V41_VISION (Metal-only path)"
    else
      echo "Note: V4.1 vision encoder not found ($V41_VISION) - text only."
      echo "      Enable with: ./download_model.sh ds41f-vision"
    fi
  elif [ "${IS_QWEN:-0}" = "1" ]; then
    if [ ! -f "$QWEN_VISION" ]; then
      echo "Error: Qwen vision encoder not found: $QWEN_VISION"
      echo "       Run: ./download_model.sh qwen38-vision"
      return 1
    fi
    VISION_ARGS+=(--vision "$QWEN_VISION")
    if [ "$QWEN_BATCH_SESSION" != "0" ]; then
      VISION_ARGS+=(--batched-session "$QWEN_BATCH_SESSION")
    fi
    if [ "$QWEN_MTP" != "0" ]; then
      VISION_ARGS+=(--mtp)
    fi
    VISION_ARGS+=(--mixed-prefill-quantum "$QWEN_MIXED_QUANTUM")
    echo "Qwen3.8: vision encoder $QWEN_VISION, batched-session $QWEN_BATCH_SESSION, mtp $QWEN_MTP, mixed-prefill-quantum $QWEN_MIXED_QUANTUM"
  elif [[ "${model_path:-}" == *Vision-Exp* ]]; then
    # Explicit Vision-Exp path (e.g. while ds4flash.gguf points at Qwen3.8).
    if [ ! -f "$VISION_ENCODER" ]; then
      echo "Error: Vision-Exp model but encoder not found: $VISION_ENCODER"
      return 1
    fi
    VISION_ARGS+=(--vision "$VISION_ENCODER")
    echo "Vision encoder attached: $VISION_ENCODER"
  elif [ -z "$model_path" ]; then
    case "$DEFAULT_MODEL_RESOLVED" in
      *Vision*)
        if [ ! -f "$VISION_ENCODER" ]; then
          echo "Error: default model is Vision-Exp but encoder not found: $VISION_ENCODER"
          echo "       Run: ./download_model.sh ds4f-vision-encoder"
          return 1
        fi
        VISION_ARGS+=(--vision "$VISION_ENCODER")
        echo "Vision encoder attached: $VISION_ENCODER"
        ;;
      *)
        echo "Note: default model is text-only ($(basename "$DEFAULT_MODEL_RESOLVED")); no --vision"
        ;;
    esac
  fi

  # Build model argument
  MODEL_ARGS=()
  if [ -n "$model_path" ]; then
    MODEL_ARGS+=(--model "$model_path")
  fi

  # Build speculative-decoding arguments (pathway-specific; --mtp is shared)
  MTP_ARGS=()
  if [ "$spec_mode" = "mtp" ]; then
    # Legacy one-stage nextn drafter: no --dspark. --mtp-draft must be > 1 or
    # the server's speculation gate never fires.
    MTP_ARGS+=(--mtp-model "$spec_used" --mtp-draft "$MTP_DRAFT" --mtp-margin "$MTP_MARGIN")
  elif [ "$spec_mode" = "dspark" ]; then
    # DSpark: block size comes from the support model metadata; --mtp-draft /
    # --mtp-margin are legacy flags and are NOT passed here.
    MTP_ARGS+=(--mtp-model "$spec_used" --dspark)
    # Propagate the measured scheduler window (12) so a plain
    # restart-vision-dspark reproduces the tuned config; ds4 reads it via getenv.
    export DS4_DSPARK_SCHEDULER_WINDOW="$DSPARK_SCHED_WINDOW"
    if [ "$DSPARK_EXACT" = "1" ]; then
      MTP_ARGS+=(--mtp-exact-sampling)
    fi
    MTP_ARGS+=(--dspark-confidence "$DSPARK_CONFIDENCE")
  fi

  # Optional extra server flags (space-separated), e.g.
  #   SERVER_EXTRA_ARGS="--mtp-timing" ./ds4-server.sh restart-qwen
  EXTRA_ARGS=()
  if [ -n "${SERVER_EXTRA_ARGS:-}" ]; then
    # shellcheck disable=SC2206
    EXTRA_ARGS=(${SERVER_EXTRA_ARGS})
  fi

  # Build trace argument
  TRACE_ARGS=()
  if [ -n "$TRACE_PATH" ]; then
    mkdir -p "$(dirname "$TRACE_PATH")"
    TRACE_ARGS+=(--trace "$TRACE_PATH")
  fi

  # Detach into a new session: an engine must outlive whatever terminal or
  # automation (timeout cleanup, ssh session loss) happened to start it.
  # Without setsid the server inherits the caller's process group and a
  # killed parent sends it SIGTERM ("mystery shutdown requested" deaths).
  local SETSID=()
  if command -v perl >/dev/null 2>&1; then
    SETSID=(perl -MPOSIX -e 'POSIX::setsid(); exec @ARGV' --)
  fi
  # Use ${arr[@]+"${arr[@]}"} to safely expand empty arrays on old bash
  ${SETSID[@]+"${SETSID[@]}"} $SERVER_CMD \
    ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
    --ctx "$CTX" \
    --tokens "$TOKENS" \
    --host "$HOST" \
    --port "$PORT" \
    --kv-disk-dir "$KV_DIR" \
    --kv-disk-space-mb "$KV_SIZE" \
    --kv-cache-small-dense "$KV_SMALL_DENSE" \
    --kv-cache-retire-grace-seconds "$KV_RETIRE_GRACE" \
    --kv-cache-max-divergence-anchors "$KV_MAX_DIVERGENCE_ANCHORS" \
    ${MTP_ARGS[@]+"${MTP_ARGS[@]}"} \
    ${VISION_ARGS[@]+"${VISION_ARGS[@]}"} \
    ${TRACE_ARGS[@]+"${TRACE_ARGS[@]}"} \
    ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
    > "$LOG_FILE" 2>&1 &

  local pid=$!
  # Atomic PID file write
  echo "$pid" > "${PID_FILE}.tmp" && mv "${PID_FILE}.tmp" "$PID_FILE"
  echo "$PORT" > "${PID_FILE}.port"
  echo "ds4-server started (PID: $pid)"

  # Verify the process actually started
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Error: ds4-server failed to start (PID: $pid)"
    rm -f "$PID_FILE"
    return 1
  fi

  # A live auth_proxy follows the engine: restart it only when the upstream
  # port actually changed (engine switch), so same-engine restarts are not
  # interrupted by a proxy blip.
  if [ -f "$PROXY_PID_FILE" ] && kill -0 "$(cat "$PROXY_PID_FILE")" 2>/dev/null; then
    prev_upstream=$(cat "$PROXY_PID_FILE.upstream" 2>/dev/null || echo "?")
    if [ "$prev_upstream" != "$PORT" ]; then
      echo "auth_proxy live: upstream $prev_upstream -> $PORT, restarting it"
      if ! restart_proxy; then
        echo "Warning: auth_proxy follow-restart FAILED - LAN path is down; run '$0 restart-proxy' manually"
      fi
    fi
  fi
}

stop_server() {
  local pf
  FOUND_EXTRA=""
  for pf in "$DEFAULT_PID_FILE" "$QWEN_PID_FILE" "$V41_PID_FILE"; do
    [ "$pf" = "$PID_FILE" ] || FOUND_EXTRA="$FOUND_EXTRA $pf"
  done
  if [ ! -f "$PID_FILE" ] && [ ! -f "$QWEN_PID_FILE" ] && [ ! -f "$V41_PID_FILE" ]; then
    echo "No PID file found. Is ds4-server running?"
    return 0
  fi
  stop_one_pidfile "$PID_FILE"
  for pf in $FOUND_EXTRA; do stop_one_pidfile "$pf"; done
  return 0
}

FOUND_EXTRA=""
stop_one_pidfile() {
  local pf="$1"
  [ -f "$pf" ] || return 0
  local pid
  pid=$(cat "$pf")
  if kill -0 "$pid" 2>/dev/null; then
    echo "Stopping ds4-server (PID: $pid)..."
    kill "$pid"

    # Wait up to 10 seconds for graceful shutdown
    local i=0
    while [ $i -lt 10 ]; do
      if ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
      sleep 1
      i=$((i + 1))
    done

    # Force kill if still running
    if kill -0 "$pid" 2>/dev/null; then
      echo "Warning: Force killing ds4-server (PID: $pid) - KV cache may be corrupted"
      kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$pf" "$pf.port"
    echo "ds4-server stopped"
  else
    echo "Process $pid not running. Cleaning up PID file."
    rm -f "$pf" "$pf.port"
  fi
  return 0
}

status_server() {
  if [ -f "$V41_PID_FILE" ] && [ "$V41_PID_FILE" != "$PID_FILE" ]; then
    local vpid
    vpid=$(cat "$V41_PID_FILE")
    if kill -0 "$vpid" 2>/dev/null; then
      echo "ds4-server (v4.1 flash) is running (PID: $vpid)"
      return 0
    fi
  fi
  if [ -f "$QWEN_PID_FILE" ] && [ "$QWEN_PID_FILE" != "$PID_FILE" ]; then
    local qpid
    qpid=$(cat "$QWEN_PID_FILE")
    if kill -0 "$qpid" 2>/dev/null; then
      echo "ds4-server (qwen) is running (PID: $qpid)"
      return 0
    fi
  fi
  if [ -f "$PID_FILE" ]; then
    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      echo "ds4-server is running (PID: $pid)"
      return 0
    else
      echo "ds4-server is not running (stale PID file)"
      return 1
    fi
  else
    echo "ds4-server is not running"
    return 1
  fi
}

start_proxy() {
  local SETSID=()
  if command -v perl >/dev/null 2>&1; then
    SETSID=(perl -MPOSIX -e 'POSIX::setsid(); exec @ARGV' --)
  fi
  # Key source: the 0600 key file is CANONICAL when present - a stale exported
  # DS4_API_KEY in a long-lived shell silently overriding the real desktop key
  # has bitten twice. Env var is the fallback (no file). Never committed.
  if [ -f "${DS4_API_KEY_FILE:-$HOME/.config/ds4/desktop.key}" ]; then
    DS4_API_KEY=$(cat "${DS4_API_KEY_FILE:-$HOME/.config/ds4/desktop.key}")
    export DS4_API_KEY
    _kf_perms=$(stat -f %Lp "${DS4_API_KEY_FILE:-$HOME/.config/ds4/desktop.key}")
    if [ $((8#$_kf_perms & 8#77)) -ne 0 ]; then
      echo "Warning: key file is group/world-readable ($_kf_perms) - chmod 600 it"
    fi
  fi
  if [ -z "${DS4_API_KEY:-}" ]; then
    echo "Error: DS4_API_KEY not set (and no key file) — refusing to start an unauthenticated proxy."
    return 1
  fi
  # Upstream follows whichever engine is actually loaded, at the port the
  # engine was ACTUALLY started on (per-pid .port file; env-default fallback).
  local upstream_port="" upname="none" efile ep
  for efile in "$DEFAULT_PID_FILE" "$QWEN_PID_FILE" "$V41_PID_FILE"; do
    if [ -f "$efile" ] && kill -0 "$(cat "$efile")" 2>/dev/null; then
      ep=$(cat "$efile.port" 2>/dev/null || echo "")
      if [ -z "$ep" ]; then
        case "$efile" in
          *qwen*) ep="$QWEN_PORT" ;;
          *v41*) ep="$V41_PORT" ;;
          *) ep=8001 ;;
        esac
      fi
      upstream_port="$ep"
      case "$efile" in *qwen*) upname="Qwen3.8" ;; *v41*) upname="V4.1 Flash" ;; *) upname="DeepSeek" ;; esac
      break   # one engine at a time; first live pid file wins (deterministic)
    fi
  done
  if [ -z "$upstream_port" ]; then
    echo "Warning: no engine is loaded; auth_proxy will refuse upstream until one starts (assuming 8001)"
    upstream_port=8001; upname="DeepSeek (not live)"
  fi
  if [ -f "$PROXY_PID_FILE" ]; then
    local pid
    pid=$(cat "$PROXY_PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      echo "auth_proxy is already running (PID: $pid)"
      return 1
    else
      rm -f "$PROXY_PID_FILE" "$PROXY_PID_FILE.upstream"
    fi
  fi
  echo "Starting auth_proxy on ${PROXY_HOST}:${PROXY_PORT} -> 127.0.0.1:${upstream_port} (${upname}, auth required)..."
  BIND_HOST="$PROXY_HOST" BIND_PORT="$PROXY_PORT" \
    UPSTREAM_HOST=127.0.0.1 UPSTREAM_PORT="$upstream_port" \
    ${SETSID[@]+"${SETSID[@]}"} python3 "$PROXY_CMD" > "$LOG_DIR/auth_proxy.log" 2>&1 &
  local pid=$!
  echo "$pid" > "${PROXY_PID_FILE}.tmp" && mv "${PROXY_PID_FILE}.tmp" "$PROXY_PID_FILE"
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Error: auth_proxy failed to start (PID: $pid)"
    tail -2 "$LOG_DIR/auth_proxy.log" 2>/dev/null || true
    rm -f "$PROXY_PID_FILE" "$PROXY_PID_FILE.upstream"
    return 1
  fi
  echo "$upstream_port" > "$PROXY_PID_FILE.upstream"
  echo "auth_proxy started (PID: $pid)"
}

restart_proxy() {
  stop_proxy
  start_proxy
}

stop_proxy() {
  if [ ! -f "$PROXY_PID_FILE" ]; then
    echo "No proxy PID file found. Is auth_proxy running?"
    return 0
  fi
  local pid
  pid=$(cat "$PROXY_PID_FILE")
  if kill -0 "$pid" 2>/dev/null; then
    echo "Stopping auth_proxy (PID: $pid)..."
    kill "$pid"
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PROXY_PID_FILE" "$PROXY_PID_FILE.upstream"
    echo "auth_proxy stopped"
  else
    rm -f "$PROXY_PID_FILE" "$PROXY_PID_FILE.upstream"
  fi
  return 0
}

status_proxy() {
  if [ -f "$PROXY_PID_FILE" ]; then
    local pid
    pid=$(cat "$PROXY_PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      echo "auth_proxy is running (PID: $pid)"
      return 0
    else
      echo "auth_proxy is not running (stale PID file)"
      return 1
    fi
  else
    echo "auth_proxy is not running"
    return 1
  fi
}

# Fail loudly BEFORE stopping a running engine: a bad path or missing encoder
# must not leave the box with no engine and a stale proxy upstream.
preflight_engine() {
  local mp="$1" target specm="${2:-}" specp="${3:-}" kvd
  if [ -n "$mp" ]; then target="$mp"; else target=$(resolve_default_link); fi
  # Rejections that must land BEFORE a live engine is stopped (restart arm).
  if [ ! -f "$target" ]; then
    echo "Error: model file not found: $target"
    case "$target" in
      *V4.1*|*v4.1*) echo "       Run: ./download_model.sh ds41f-q2 (streams from the internal SSD)" ;;
      *Qwen*|*qwen*) echo "       Run: ./download_model.sh qwen38-q4k" ;;
    esac
    return 1
  fi
  # Speculation family (no resolved per-model support => the default would be
  # a wrong-checkpoint artifact), and a resolved support file that is missing
  # on disk - both fatal before the stop, matching the in-function checks.
  spec_gate_ok "$target" "$specm" "$specp" || return 1
  if [ -n "$specp" ] && [ ! -f "$specp" ]; then
    echo "Error: $specm support model not found: $specp"
    return 1
  fi
  # Enforcement arms mirror start_server's profile globs EXACTLY (a wider
  # pattern here would impose encoder/KV demands on paths the profile would
  # not route to this family). Hint lines above may stay broad.
  case "$target" in
    *Qwen3.8*|*qwen3.8*)
      kvd="$QWEN_KV_DIR"
      if [ ! -f "$QWEN_VISION" ]; then
        echo "Error: Qwen vision encoder not found: $QWEN_VISION (run ./download_model.sh qwen38-vision)"
        return 1
      fi ;;
    *V4.1*|*v4.1*)
      kvd="$V41_KV_DIR" ;;   # vision encoder optional for V4.1 (text serves without)
    *Vision-Exp*)
      kvd="$KV_DIR"
      if [ ! -f "$VISION_ENCODER" ]; then
        echo "Error: DeepSeek vision encoder not found: $VISION_ENCODER"
        return 1
      fi ;;
    *)
      kvd="$KV_DIR" ;;
  esac
  # Profile KV volume must be mounted too (blade guard, pre-stop).
  kv_volume_ok "$kvd" || return 1
  return 0
}

case "${1:-}" in
  start)
    start_server "" "" ""
    ;;
  start-mtp)
    start_server "" mtp ""
    ;;
  start-dspark)
    start_server "" dspark ""
    ;;
  stop)
    stop_server
    ;;
  restart)
    preflight_engine "" "" || exit 1
    stop_server; start_server "" "" ""
    ;;
  restart-mtp)
    preflight_engine "" mtp || exit 1
    stop_server; start_server "" mtp ""
    ;;
  restart-dspark)
    preflight_engine "" dspark || exit 1
    stop_server; start_server "" dspark ""
    ;;
  status)
    status_server
    ;;
  start-proxy)
    start_proxy
    ;;
  stop-proxy)
    stop_proxy
    ;;
  restart-proxy)
    stop_proxy; start_proxy
    ;;
  status-proxy)
    status_proxy
    ;;
  start-collect)
    arm_collect_env || exit 1
    exec "$0" start-vision-dspark
    ;;
  restart-collect)
    arm_collect_env || exit 1
    exec "$0" restart-vision-dspark
    ;;
  start-*|restart-*)
    action="$1"
    do_restart=0

    # Strip command prefix to get model suffix
    if [[ "$action" == start-* ]]; then
      suffix="${action#start-}"
    else
      suffix="${action#restart-}"
      do_restart=1
    fi

    # Check for the speculative-decoding variant: -mtp (legacy) or -dspark
    spec_mode=""
    if [[ "$suffix" == *-dspark ]]; then
      spec_mode="dspark"
      suffix="${suffix%-dspark}"
    elif [[ "$suffix" == *-mtp ]]; then
      spec_mode="mtp"
      suffix="${suffix%-mtp}"
    fi

    # Look up model path
    model_path=$(lookup_model "$suffix") || true
    if [ -z "$model_path" ]; then
      echo "Error: unknown model '$suffix'. Available models: ${MODEL_KEYS[*]}"
      exit 1
    fi

    # Look up per-model support path (empty => pathway default)
    spec_model_path=""
    if [ "$spec_mode" = "dspark" ]; then
      spec_model_path=$(lookup_dspark_model "$suffix") || true
    elif [ "$spec_mode" = "mtp" ]; then
      spec_model_path=$(lookup_mtp_model "$suffix") || true
    fi

    if [ "$do_restart" = 1 ]; then
      preflight_engine "$model_path" "$spec_mode" "$spec_model_path" || exit 1
      stop_server; start_server "$model_path" "$spec_mode" "$spec_model_path"
    else
      start_server "$model_path" "$spec_mode" "$spec_model_path"
    fi
    ;;
  *)
    echo "Usage: $0 {start|start-mtp|start-dspark|start-<model>|stop|restart|restart-mtp|restart-dspark|restart-<model>|status"
    echo "       |start-proxy|stop-proxy|restart-proxy|status-proxy}"
    echo ""
    echo "Options:"
    echo "  start               - Start ds4-server (default model)"
    echo "  start-mtp           - Start with LEGACY one-stage MTP speculation (--mtp-draft, no --dspark)"
    echo "  start-dspark        - Start with DSpark block speculation (--mtp ... --dspark)"
    echo "  start-<model>       - Start ds4-server with an alternative model"
  echo "  start-qwen          - Qwen3.8 Flash Next Q4_K + vision + batched MTP (ctx 262k; QWEN_* env-overridable)"
  echo "  start-v41           - DeepSeek V4.1 Flash Q2 + SSD streaming (ctx 256k; V41_* env;"
  echo "                        vision auto-attached when the ds41f-vision encoder is present)"
  echo "  start-collect       - Vision-Exp + dspark EXACT + TRAIN_DUMP/PARITY capture armed"
  echo "                        (restart-collect = same with stop; DS4_TD_*/DSPARK_EXACT env-"
  echo "                        overridable; NEVER plain start/restart while collecting -"
  echo "                        zero capture + fp flip kills all disk-KV)"
    echo "  start-<model>-mtp   - Alternative model + legacy MTP drafter"
    echo "  start-<model>-dspark - Alternative model + DSpark support"
    echo "  stop                - Stop ds4-server"
    echo "  restart             - Restart ds4-server (default model)"
    echo "  restart-mtp         - Restart with legacy MTP speculative decoding"
    echo "  restart-dspark      - Restart with DSpark speculative decoding"
    echo "  restart-<model>     - Restart ds4-server with an alternative model"
    echo "  restart-<model>-mtp / -dspark - as above, with restart"
    echo "  status             - Check if ds4-server is running"
    echo "  start-proxy        - Start the auth-gated proxy on PROXY_HOST:PROXY_PORT"
    echo "                        (requires DS4_API_KEY)"
    echo "  stop-proxy         - Stop the auth proxy"
    echo "  restart-proxy      - Restart the auth proxy"
    echo "  status-proxy       - Check if the auth proxy is running"
    echo ""
    echo "Auth proxy: keep ds4-server on loopback; remote clients hit the proxy."
    echo "  PROXY_HOST        - Proxy bind address (default: 0.0.0.0)"
    echo "  PROXY_PORT        - Proxy port (default: 8100)"
    echo "                        NOTE: upstream (8001 DeepSeek / 8002 Qwen / 8003 V4.1) is"
    echo "                        detected at proxy start - run restart-proxy"
    echo "                        after switching engines."
    echo "  DS4_API_KEY       - Bearer token clients must present (required to start proxy)"
    echo ""
    echo "Available models (plain start serves ds4flash.gguf - whatever download_model.sh linked"
    echo "  last; Vision-Exp gets --vision $VISION_ENCODER, V4.1 its own streaming profile):"
    for i in "${!MODEL_KEYS[@]}"; do
      echo "  ${MODEL_KEYS[$i]}  -> ${MODEL_PATHS[$i]}"
    done
    echo ""
    echo "Legacy MTP drafter: $MTP_MODEL"
    echo "DSpark support model: $DSPARK_MODEL"
    echo "Per-model DSpark support (used by start-<model>-dspark):"
    for i in "${!DSPARK_KEYS[@]}"; do
      echo "  ${DSPARK_KEYS[$i]}  -> ${DSPARK_PATHS[$i]}"
    done
    echo "MTP/DSpark tuning (script variables or env overrides):"
    echo "  MTP_DRAFT           - Legacy MTP only: max draft tokens (default: 2; server"
    echo "                        gate requires > 1 for speculation to fire)"
    echo "  MTP_MARGIN          - Legacy MTP verifier confidence margin (default: 3)"
    echo "  DSPARK_CONFIDENCE   - DSpark confidence threshold 0..1 (default: 0.45, 2026-10-29 tuned)"
    echo "  DS4_DSPARK_SCHEDULER_WINDOW - engagement probe window (default: 12)"
    echo "  DSPARK_EXACT        - 1 = --mtp-exact-sampling (target distribution at"
    echo "                        non-zero temperature; default 1, 0 = legacy opportunistic)"
    echo ""
    echo "Environment:"
    echo "  DEBUG=1             - Enable verbose output"
    echo "  HOST=ADDR           - Bind address (default: 127.0.0.1). Use 0.0.0.0 or a"
    echo "                        LAN IP like 192.168.1.20 to expose the server"
    echo "                        on the network for remote clients."
    echo "  PROXY_HOST          - Proxy bind address (default: 0.0.0.0)"
    echo "  PROXY_PORT          - Proxy port (default: 8100)"
    echo "  DS4_API_KEY         - Bearer token for the auth proxy (required to start)"
    echo "  TRACE_PATH          - Write cache-decision trace to this file (e.g."
    echo "                        TRACE_PATH=./log/ds4.trace). Empty = off."
    echo "  DS4_VISION_KEEP_IMAGES - Auto-reduce over-budget image histories to"
    echo "                        the last N images (default: 256; 0 = reject"
    echo "                        requests over 16 images instead)."
    echo "  KV_DIR / KV_SIZE    - DeepSeek KV store dir + disk budget MiB (defaults:"
    echo "                        /Volumes/FireCuda520/ds4-kv-deepseek, 262144 = 256 GiB;"
    echo "                        QWEN_KV_DIR/QWEN_KV_SIZE mirror it per profile)"
    echo "  V41_*               - V4.1 profile: V41_MODEL/V41_VISION/V41_CTX (524288)/"
    echo "                        V41_TOKENS (65536)/V41_PORT (8003)/V41_KV_DIR/"
    echo "                        V41_KV_SIZE (262144 = 256 GiB);"
    echo "                        V41_SSD_STREAM=0 for full residency (256 GB+ hosts only)."
    echo "  DS4_THINK_TRIPWIRE  - '1' enables the thinking-loop tripwire server-wide"
    echo "                        (default: 1). DS4_TRIPWIRE_MIN_TOKENS sets the"
    echo "                        repetition-volume (default 1200, range 600-2560)."
    echo "  KV_SMALL_DENSE      - Keep ALL KV anchors ≤ this token count sticky"
    echo "                        (default: 49152). Raise to bound head-divergence rebuilds."
    exit 1
    ;;
esac
