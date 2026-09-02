#!/bin/bash
set -euo pipefail

# ds4-server management script
# Copy to ds4 directory and run: ./ds4-server.sh [start|stop|restart|status]

SERVER_CMD="./ds4-server"
PID_FILE="./ds4-server.pid"
CTX=512000
PORT=8001
# Auth-gated reverse proxy in front of ds4-server. Keep the server on loopback;
# clients hit the proxy's LAN address. Requires DS4_API_KEY (refuses to start without).
PROXY_CMD="./auth_proxy.py"
PROXY_PID_FILE="./auth_proxy.pid"
PROXY_PORT=8002
PROXY_HOST="${PROXY_HOST:-0.0.0.0}"
# Bind address. Default 127.0.0.1 (loopback). Override with HOST env, e.g.
# HOST=0.0.0.0 or HOST=192.168.1.20 to expose the server on the LAN.
HOST="${HOST:-127.0.0.1}"
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
TOKENS=384000
# Two DISTINCT speculative-decoding pathways, not interchangeable:
#  - DSpark  (--dspark): block drafter; REQUIRES the 0731 support GGUF and 0731
#    main models only (checkpoint-specific). Non-greedy uses opportunistic
#    sampling; set DSPARK_EXACT=1 for --mtp-exact-sampling (target distribution).
#  - Legacy MTP (--mtp-draft): one-stage nextn drafter (May 2026 GGUF). Pass the
#    drafter via --mtp-model WITHOUT --dspark; ds4 detects the kind by tensor
#    names. Upstream: DSpark replaces this for the 0731 checkpoint.
DSPARK_MODEL="gguf/DeepSeek-V4-Flash-DSpark-support-0731.gguf"
MTP_MODEL="gguf/DeepSeek-V4-Flash-MTP-Q4K-Q8_0-F32.gguf"
# Cache-miss trace: set TRACE_PATH to a file path (e.g. ./log/ds4.trace) to make
# the server write the exact cache-decision + first-mismatch token window for
# every request. Used for debugging KV divergence (see DS4FORK.md KVCACHE —
# Deep Divergence Investigation). Empty = tracing off.
TRACE_PATH="${TRACE_PATH:-}"

# Alternative model map: short name -> full GGUF path
# Add entries here for each model variant. Use `start-<name>` / `restart-<name>`.
# Uses parallel indexed arrays (bash 3.2 compatible — macOS default).
MODEL_KEYS=("0731")
MODEL_PATHS=(
    "gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"
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
  if [ -f "$LOG_FILE" ]; then
    local ts
    ts=$(date +%Y%m%d-%H%M%S)
    cp "$LOG_FILE" "$LOG_DIR/ds4.log.$ts"
    echo "Rotated previous log to $LOG_DIR/ds4.log.$ts"
  fi

  # Keep only the last MAX_LOG_ROTATIONS rotated logs
  local old_logs
  old_logs=$(ls -1t "$LOG_DIR"/ds4.log.* 2>/dev/null || true)
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

  # Use ${arr[@]+"${arr[@]}"} to safely expand empty arrays on old bash
  $SERVER_CMD \
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
    ${TRACE_ARGS[@]+"${TRACE_ARGS[@]}"} \
    > "$LOG_FILE" 2>&1 &

  local pid=$!
  # Atomic PID file write
  echo "$pid" > "${PID_FILE}.tmp" && mv "${PID_FILE}.tmp" "$PID_FILE"
  echo "ds4-server started (PID: $pid)"

  # Verify the process actually started
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Error: ds4-server failed to start (PID: $pid)"
    rm -f "$PID_FILE"
    return 1
  fi
}

stop_server() {
  if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found. Is ds4-server running?"
    return 0
  fi

  local pid
  pid=$(cat "$PID_FILE")
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
      echo "Warning: Force killing ds4-server (PID: $pid) — KV cache may be corrupted"
      kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$PID_FILE"
    echo "ds4-server stopped"
  else
    echo "Process $pid not running. Cleaning up PID file."
    rm -f "$PID_FILE"
  fi
  return 0
}

status_server() {
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
  if [ -z "${DS4_API_KEY:-}" ]; then
    echo "Error: DS4_API_KEY not set — refusing to start an unauthenticated proxy."
    return 1
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
  echo "Starting auth_proxy on ${PROXY_HOST}:${PROXY_PORT} -> 127.0.0.1:${PORT} (auth required)..."
  python3 "$PROXY_CMD" > "$LOG_DIR/auth_proxy.log" 2>&1 &
  local pid=$!
  echo "$pid" > "${PROXY_PID_FILE}.tmp" && mv "${PROXY_PID_FILE}.tmp" "$PROXY_PID_FILE"
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Error: auth_proxy failed to start (PID: $pid)"
    rm -f "$PROXY_PID_FILE"
    return 1
  fi
  echo "auth_proxy started (PID: $pid)"
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
    rm -f "$PROXY_PID_FILE"
    echo "auth_proxy stopped"
  else
    rm -f "$PROXY_PID_FILE"
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
    stop_server; start_server "" "" ""
    ;;
  restart-mtp)
    stop_server; start_server "" mtp ""
    ;;
  restart-dspark)
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
    echo "  PROXY_PORT        - Proxy port (default: 8002)"
    echo "  DS4_API_KEY       - Bearer token clients must present (required to start proxy)"
    echo ""
    echo "Available models (default: ds4flash.gguf):"
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
    echo "  PROXY_PORT          - Proxy port (default: 8002)"
    echo "  DS4_API_KEY         - Bearer token for the auth proxy (required to start)"
    echo "  TRACE_PATH          - Write cache-decision trace to this file (e.g."
    echo "                        TRACE_PATH=./log/ds4.trace). Empty = off."
    echo "  KV_SMALL_DENSE      - Keep ALL KV anchors ≤ this token count sticky"
    echo "                        (default: 49152). Raise to bound head-divergence rebuilds."
    exit 1
    ;;
esac
