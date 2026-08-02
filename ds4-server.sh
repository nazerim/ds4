#!/bin/bash
set -euo pipefail

# ds4-server management script
# Copy to ds4 directory and run: ./ds4-server.sh [start|stop|restart|status]

SERVER_CMD="./ds4-server"
PID_FILE="./ds4-server.pid"
CTX=256000
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
KV_SIZE=32768
LOG_DIR="./log"
LOG_FILE="$LOG_DIR/ds4.log"
TOKENS=384000
MTP_MODEL="gguf/DeepSeek-V4-Flash-DSpark-support.gguf"

# Alternative model map: short name -> full GGUF path
# Add entries here for each model variant. Use `start-<name>` / `restart-<name>`.
# Uses parallel indexed arrays (bash 3.2 compatible — macOS default).
MODEL_KEYS=("0731")
MODEL_PATHS=(
    "/Users/naz/.omlx/models/jmilnz/DeepSeek-V4-Flash-0731-antirez-ds4-GGUF/DeepSeek-V4-Flash-0731-Layers37-42Q4K-mixed-realimatrix-v2.gguf"
)

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
MTP_DRAFT="${MTP_DRAFT:-1}"
MTP_MARGIN="${MTP_MARGIN:-3}"
# 0.6 balances speculation acceptance vs verification cost; 0.9 was too
# conservative, rejecting most drafts and negating MTP throughput gains.
DSPARK_CONFIDENCE="${DSPARK_CONFIDENCE:-0.6}"
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
  local enable_mtp="${2:-}"

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

  # Validate MTP model exists if requested
  if [ "$enable_mtp" = "mtp" ] && [ ! -f "$MTP_MODEL" ]; then
    echo "Error: MTP model not found: $MTP_MODEL"
    return 1
  fi

  if [ -n "$model_path" ]; then
    echo "Starting ds4-server on port $PORT with model $model_path (ctx: $CTX)..."
  else
    echo "Starting ds4-server on port $PORT (ctx: $CTX)..."
  fi
  if [ "$enable_mtp" = "mtp" ]; then
    echo "MTP speculative decoding enabled"
  fi
  echo "Logging to $LOG_FILE"

  # Build model argument
  MODEL_ARGS=()
  if [ -n "$model_path" ]; then
    MODEL_ARGS+=(--model "$model_path")
  fi

  # Build MTP arguments
  MTP_ARGS=()
  if [ "$enable_mtp" = "mtp" ]; then
    MTP_ARGS+=(--mtp "$MTP_MODEL" --dspark --mtp-draft "$MTP_DRAFT" --mtp-margin "$MTP_MARGIN")
    MTP_ARGS+=(--dspark-confidence "$DSPARK_CONFIDENCE")
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
    ${MTP_ARGS[@]+"${MTP_ARGS[@]}"} \
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
    start_server "" ""
    ;;
  start-mtp)
    start_server "" mtp
    ;;
  stop)
    stop_server
    ;;
  restart)
    stop_server; start_server "" ""
    ;;
  restart-mtp)
    stop_server; start_server "" mtp
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

    # Check for -mtp variant
    enable_mtp=""
    if [[ "$suffix" == *-mtp ]]; then
      enable_mtp="mtp"
      suffix="${suffix%-mtp}"
    fi

    # Look up model path
    model_path=$(lookup_model "$suffix") || true
    if [ -z "$model_path" ]; then
      echo "Error: unknown model '$suffix'. Available models: ${MODEL_KEYS[*]}"
      exit 1
    fi

    if [ "$do_restart" = 1 ]; then
      stop_server; start_server "$model_path" "$enable_mtp"
    else
      start_server "$model_path" "$enable_mtp"
    fi
    ;;
  *)
    echo "Usage: $0 {start|start-mtp|start-<model>|stop|restart|restart-mtp|restart-<model>|status"
    echo "       |start-proxy|stop-proxy|restart-proxy|status-proxy}"
    echo ""
    echo "Options:"
    echo "  start              - Start ds4-server (default model)"
    echo "  start-mtp          - Start ds4-server with MTP speculative decoding"
    echo "  start-<model>      - Start ds4-server with an alternative model"
    echo "  start-<model>-mtp  - Start ds4-server with an alternative model + MTP"
    echo "  stop               - Stop ds4-server"
    echo "  restart            - Restart ds4-server (default model)"
    echo "  restart-mtp        - Restart ds4-server with MTP speculative decoding"
    echo "  restart-<model>    - Restart ds4-server with an alternative model"
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
    echo "MTP model: $MTP_MODEL"
    echo "MTP/DSpark tuning (script variables or env overrides):"
    echo "  MTP_DRAFT           - Max autoregressive draft tokens (default: 1)"
    echo "  MTP_MARGIN          - Verifier confidence margin (default: 3)"
    echo "  DSPARK_CONFIDENCE   - DSpark confidence threshold 0..1 (default: 0.6)"
    echo ""
    echo "Environment:"
    echo "  DEBUG=1             - Enable verbose output"
    echo "  HOST=ADDR           - Bind address (default: 127.0.0.1). Use 0.0.0.0 or a"
    echo "                        LAN IP like 192.168.1.20 to expose the server"
    echo "                        on the network for remote clients."
    echo "  PROXY_HOST          - Proxy bind address (default: 0.0.0.0)"
    echo "  PROXY_PORT          - Proxy port (default: 8002)"
    echo "  DS4_API_KEY         - Bearer token for the auth proxy (required to start)"
    exit 1
    ;;
esac
