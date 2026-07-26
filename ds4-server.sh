#!/bin/bash
set -euo pipefail

# ds4-server management script
# Copy to ds4 directory and run: ./ds4-server.sh [start|stop|restart|status]

SERVER_CMD="./ds4-server"
PID_FILE="./ds4-server.pid"
CTX=100000
PORT=8001
KV_DIR="/tmp/ds4-kv"
KV_SIZE=32768
LOG_DIR="./log"
LOG_FILE="$LOG_DIR/ds4.log"
TOKENS=384000
MTP_MODEL="gguf/DeepSeek-V4-Flash-DSpark-support.gguf"
MTP_DRAFT=1
MTP_MARGIN=3

start_server() {
  local enable_mtp="${1:-}"

  if [ -f "$PID_FILE" ]; then
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

  # Rotate previous log if it exists
  if [ -f "$LOG_FILE" ]; then
    local ts
    ts=$(date +%Y%m%d-%H%M%S)
    cp "$LOG_FILE" "$LOG_DIR/ds4.log.$ts"
    echo "Rotated previous log to $LOG_DIR/ds4.log.$ts"
  fi

  echo "Starting ds4-server on port $PORT (ctx: $CTX)..."
  if [ "$enable_mtp" = "mtp" ]; then
    echo "MTP speculative decoding enabled"
  fi
  echo "Logging to $LOG_FILE"

  # Build MTP arguments
  MTP_ARGS=()
  if [ "$enable_mtp" = "mtp" ]; then
    MTP_ARGS+=(--mtp "$MTP_MODEL" --dspark --mtp-draft "$MTP_DRAFT" --mtp-margin "$MTP_MARGIN")
  fi

  $SERVER_CMD \
    --ctx "$CTX" \
    --tokens "$TOKENS" \
    --port "$PORT" \
    --kv-disk-dir "$KV_DIR" \
    --kv-disk-space-mb "$KV_SIZE" \
    "${MTP_ARGS[@]}" \
    > "$LOG_FILE" 2>&1 &

  local pid=$!
  echo "$pid" > "$PID_FILE"
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
    return 1
  fi

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
      echo "Force killing ds4-server (PID: $pid)..."
      kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$PID_FILE"
    echo "ds4-server stopped"
  else
    echo "Process $pid not running. Cleaning up PID file."
    rm -f "$PID_FILE"
  fi
}

status_server() {
  if [ -f "$PID_FILE" ]; then
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

case "${1:-}" in
  start)
    start_server
    ;;
  start-mtp)
    start_server mtp
    ;;
  stop)
    stop_server
    ;;
  restart)
    stop_server && start_server
    ;;
  restart-mtp)
    stop_server && start_server mtp
    ;;
  status)
    status_server
    ;;
  *)
    echo "Usage: $0 {start|start-mtp|stop|restart|restart-mtp|status}"
    echo ""
    echo "Options:"
    echo "  start       - Start ds4-server"
    echo "  start-mtp   - Start ds4-server with MTP speculative decoding"
    echo "  stop        - Stop ds4-server"
    echo "  restart     - Restart ds4-server"
    echo "  restart-mtp - Restart ds4-server with MTP speculative decoding"
    echo "  status      - Check if ds4-server is running"
    echo ""
    echo "MTP model: $MTP_MODEL"
    echo "MTP tuning (edit script variables):"
    echo "  MTP_DRAFT  - Max autoregressive draft tokens (default: 1)"
    echo "  MTP_MARGIN - Verifier confidence margin (default: 3)"
    exit 1
    ;;
esac
