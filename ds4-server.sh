#!/bin/bash
set -euo pipefail

# ds4-server management script
# Copy to ds4 directory and run: ./ds4-server.sh [start|stop|restart|status]

SERVER_CMD="./ds4-server"
PID_FILE="./ds4-server.pid"
# model-aware ctx default (1M for DeepSeek, 262k for Qwen3.8) resolved in
# start_server; env CTX overrides both.
CTX="${CTX:-}"
PORT="${PORT:-}"   # per-model default resolved in start_server (DeepSeek 8001, Qwen 8002)
# Auth-gated reverse proxy in front of ds4-server. Keep the server on loopback;
# clients hit the proxy's LAN address. Requires DS4_API_KEY (refuses to start without).
PROXY_CMD="./auth_proxy.py"
PROXY_PID_FILE="./auth_proxy.pid"
PROXY_PORT="${PROXY_PORT:-8100}"   # LAN-facing proxy port (8001=DeepSeek, 8002=Qwen stay loopback)
PROXY_HOST="${PROXY_HOST:-0.0.0.0}"
# Bind address. Default 127.0.0.1 (loopback). Override with HOST env, e.g.
# HOST=0.0.0.0 or HOST=192.168.1.20 to expose the server on the LAN.
HOST="${HOST:-127.0.0.1}"   # loopback by design; LAN clients go through auth_proxy (start-proxy)
KV_DIR="/tmp/ds4-kv"
# KV disk budget in MiB. Default 131072 (128 GiB): a single ultra-long conversation
# (328k+ tokens, e.g. ctx=512000) holds a ~50-70 GiB continued-anchor ladder, and
# two such conversations must coexist without one retiring the other's KV.  The
# retire-grace policy (KV_CACHE_RETIRE_GRACE) protects recently-live lineages, but
# the budget must still fit both ladders.  SSD endurance is a non-issue at the
# observed ~0.3 TiB/day write rate on a 2 TB drive.  See DS4FORK.md "KVCACHE —
# Deep Divergence Investigation" and PLAN-KV-REWRITE.md.
KV_SIZE="${KV_SIZE:-131072}"
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
#    sampling; set DSPARK_EXACT=1 for --mtp-exact-sampling (target distribution).
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
QWEN_PORT="${QWEN_PORT:-8002}"
QWEN_PID_FILE="./ds4-server-qwen.pid"
QWEN_KV_DIR="${QWEN_KV_DIR:-/tmp/ds4-kv-qwen}"
# Cache-miss trace: set TRACE_PATH to a file path (e.g. ./log/ds4.trace) to make
# the server write the exact cache-decision + first-mismatch token window for
# every request. Used for debugging KV divergence (see DS4FORK.md KVCACHE —
# Deep Divergence Investigation). Empty = tracing off.
TRACE_PATH="${TRACE_PATH:-}"

# Vision image budget (see README, ds4_server.c): N > 0 opts into auto-reduce
# of over-budget multimodal histories (keep last N, drop oldest). The fork
# default is 128; the upstream default (unset in the server binary) rejects
# requests over 16 images. 0 = disable auto-reduce in the server too.
DS4_VISION_KEEP_IMAGES="${DS4_VISION_KEEP_IMAGES:-128}"
export DS4_VISION_KEEP_IMAGES

# Alternative model map: short name -> full GGUF path
# Add entries here for each model variant. Use `start-<name>` / `restart-<name>`.
# Uses parallel indexed arrays (bash 3.2 compatible — macOS default).
MODEL_KEYS=("0731" "vision" "qwen")
MODEL_PATHS=(
    "gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"
    "gguf/DeepSeek-V4-Flash-Vision-Exp-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8.gguf"
    "$QWEN_MODEL"
)

# Alternative DSpark (draft) model map: short name -> full GGUF path.
# Used by start-<model>-dspark / restart-<model>-dspark. If a key has no
# entry, the default DSPARK_MODEL above is used instead.
DSPARK_KEYS=("0731")
DSPARK_PATHS=(
    "/Users/naz/Projects/ds4/gguf/DeepSeek-V4-Flash-DSpark-support-0731.gguf"
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
            echo "${DSPARK_PATHS[$i]}"
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
DSPARK_CONFIDENCE="${DSPARK_CONFIDENCE:-0.6}"
# DSPARK_EXACT=1 adds --mtp-exact-sampling: at non-zero temperature the drafts
# follow the ordinary target distribution instead of opportunistic greedy-suffix
# matching (default confidence 0.8 when unset explicitly).
DSPARK_EXACT="${DSPARK_EXACT:-0}"
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
  resolved_link=$(resolve_default_link)
  case "${model_path:-$resolved_link}" in
    *Qwen3.8*|*qwen3.8*)
      IS_QWEN=1
      CTX="${CTX:-$QWEN_CTX}"
      if [ "$QWEN_YARN" != "0" ] && [ "$CTX" -gt 262144 ]; then
        export DS4_QWEN4_YARN_FACTOR="$QWEN_YARN"
      fi
      TOKENS="${TOKENS:-$QWEN_TOKENS}"
      PORT="${PORT:-$QWEN_PORT}"
      PID_FILE="$QWEN_PID_FILE"
      KV_DIR="$QWEN_KV_DIR"
      LOG_FILE="$LOG_DIR/ds4-qwen.log"
      if [ -n "$TRACE_PATH" ] && [ "$TRACE_PATH" != "$LOG_DIR/ds4-qwen.trace" ]; then
        echo "Note: TRACE_PATH overridden for the qwen runtime: $TRACE_PATH -> $LOG_DIR/ds4-qwen.trace"
      fi
      TRACE_PATH="$LOG_DIR/ds4-qwen.trace"
      ;;
    *)
      IS_QWEN=0
      CTX="${CTX:-1048576}"
      TOKENS="${TOKENS:-384000}"
      PORT="${PORT:-8001}"
      ;;
  esac
  local OTHER_PID_FILE
  if [ "${IS_QWEN}" = "1" ]; then OTHER_PID_FILE="./ds4-server.pid"; else OTHER_PID_FILE="$QWEN_PID_FILE"; fi
  if [ -f "$OTHER_PID_FILE" ]; then
    local other_pid
    other_pid=$(cat "$OTHER_PID_FILE")
    if kill -0 "$other_pid" 2>/dev/null; then
      echo "Error: the other model is loaded (PID $other_pid, $OTHER_PID_FILE)."
      echo "       One engine at a time - use restart[-qwen] to stop it and switch,"
      echo "       or: $0 stop"
      return 1
    fi
    rm -f "$OTHER_PID_FILE"
  fi

  if [ -f "$PID_FILE" ]; then
    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      echo "ds4-server is already running (PID: $pid)"
      return 1
    else
      rm -f "$PID_FILE"
    fi
  fi

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
  DEFAULT_MODEL_RESOLVED=$(readlink "ds4flash.gguf" 2>/dev/null || echo "ds4flash.gguf")
  VISION_ARGS=()
  if [ "${IS_QWEN:-0}" = "1" ]; then
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
    echo "Qwen3.8: vision encoder $QWEN_VISION, batched-session $QWEN_BATCH_SESSION, mtp $QWEN_MTP"
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
        if [ "$spec_mode" = "dspark" ] && [ "$spec_used" = "$DSPARK_MODEL" ]; then
          echo "Error: the default Vision-Exp model needs its own drafter"
          echo "       (gguf/DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf), not the 0731 one."
          echo "       Use start-0731-dspark for text, or set DSPARK_MODEL to the Vision-Exp support GGUF."
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

  # Checkpoint-specific drafters: the default DSpark support GGUF is 0731-only.
  if [ "$spec_mode" = "dspark" ] && [ "$spec_used" = "$DSPARK_MODEL" ]; then
    case "${model_path:-$(resolve_default_link)}" in
      *Vision-Exp*|*Qwen3.8*|*qwen3.8*)
        echo "Error: DSpark default support GGUF is checkpoint-specific (0731)."
        echo "       This model needs its own drafter (Qwen3.8: built-in MTP, no DSpark)."
        return 1 ;;
    esac
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
    if [ "$DSPARK_EXACT" = "1" ]; then
      MTP_ARGS+=(--mtp-exact-sampling)
    fi
    MTP_ARGS+=(--dspark-confidence "$DSPARK_CONFIDENCE")
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
  for pf in "./ds4-server.pid" "$QWEN_PID_FILE"; do
    [ "$pf" = "$PID_FILE" ] || FOUND_EXTRA="$FOUND_EXTRA $pf"
  done
  if [ ! -f "$PID_FILE" ] && [ ! -f "$QWEN_PID_FILE" ]; then
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
  for efile in "./ds4-server.pid" "$QWEN_PID_FILE"; do
    if [ -f "$efile" ] && kill -0 "$(cat "$efile")" 2>/dev/null; then
      ep=$(cat "$efile.port" 2>/dev/null || echo "")
      if [ -z "$ep" ]; then
        case "$efile" in
          *qwen*) ep="$QWEN_PORT" ;;
          *) ep=8001 ;;
        esac
      fi
      upstream_port="$ep"
      case "$efile" in *qwen*) upname="Qwen3.8" ;; *) upname="DeepSeek" ;; esac
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
      rm -f "$PROXY_PID_FILE"
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
  local mp="$1" target
  if [ -n "$mp" ]; then target="$mp"; else target=$(resolve_default_link); fi
  if [ ! -f "$target" ]; then
    echo "Error: model file not found: $target"
    return 1
  fi
  case "$target" in
    *Qwen3.8*|*qwen3.8*)
      if [ ! -f "$QWEN_VISION" ]; then
        echo "Error: Qwen vision encoder not found: $QWEN_VISION (run ./download_model.sh qwen38-vision)"
        return 1
      fi ;;
    *Vision-Exp*)
      if [ ! -f "$VISION_ENCODER" ]; then
        echo "Error: DeepSeek vision encoder not found: $VISION_ENCODER"
        return 1
      fi ;;
  esac
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
    if preflight_engine ""; then stop_server; start_server "" "" ""; fi
    ;;
  restart-mtp)
    if preflight_engine ""; then stop_server; start_server "" mtp ""; fi
    ;;
  restart-dspark)
    if preflight_engine ""; then stop_server; start_server "" dspark ""; fi
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
      preflight_engine "$model_path" || exit 1
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
    echo "                        NOTE: upstream (8001 DeepSeek / 8002 Qwen) is"
    echo "                        detected at proxy start - run restart-proxy"
    echo "                        after switching engines."
    echo "  DS4_API_KEY       - Bearer token clients must present (required to start proxy)"
    echo ""
    echo "Available models (default: ds4flash.gguf = Vision-Exp, served with --vision $VISION_ENCODER):"
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
    echo "  DSPARK_CONFIDENCE   - DSpark confidence threshold 0..1 (default: 0.6)"
    echo "  DSPARK_EXACT        - 1 = --mtp-exact-sampling (target distribution at"
    echo "                        non-zero temperature; default 0 = opportunistic)"
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
    echo "                        the last N images (default: 128; 0 = reject"
    echo "                        requests over 16 images instead)."
    echo "  KV_SMALL_DENSE      - Keep ALL KV anchors ≤ this token count sticky"
    echo "                        (default: 49152). Raise to bound head-divergence rebuilds."
    exit 1
    ;;
esac
