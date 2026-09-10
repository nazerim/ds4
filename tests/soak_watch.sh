#!/bin/zsh
# soak_watch.sh - watchdog for ds4-server: sudden-death forensics + KV soak health.
#
# 1) HEALTH (60 s cadence): counts of the soak watchlist signatures in
#    log/ds4.log. Any nonzero DELTA since the last check is logged as ALERT
#    immediately; a full one-line summary (incl. largest stored frontier,
#    disk file count, /tmp free) is written hourly.
#    Watchlist: frontier-contradicted (guard fires - good if a loop was real,
#    bad if frequent), tokenizer-fingerprint rejects, "exceeds budget"
#    (1M deep-save pressure), Metal "Insufficient Memory" (dual-engine tell),
#    kv cache miss storms (same common= >= 5 times).
# 2) DEATH: when the PID dies, captures log tail + process table and
#    classifies planned ("shutdown requested") vs sudden (killer-watch).
#
# Writes to the REPO log dir, not /tmp (/tmp was wiped Sep 6 taking evidence).
# Relaunch after planned server restarts: nohup tests/soak_watch.sh &!
REPO=${REPO:-/Users/naz/Projects/ds4}
PIDFILE="$REPO/ds4-server.pid"
OUT="$REPO/log/soak-watch.log"
LOG="$REPO/log/ds4.log"
ts() { date '+%m%d %H:%M:%S'; }
cnt() { grep -cE "$1" "$LOG" 2>/dev/null; }
p() { typeset -i v=${1:-0}; print -P "$v"; }

PID=$(cat "$PIDFILE" 2>/dev/null)
[ -z "$PID" ] && { echo "$(ts) no pidfile" >>"$OUT"; exit 1; }
p_fc=$(p "$(cnt 'frontier-contradicted')")
p_fp=$(p "$(cnt 'tokenizer-fingerprint')")
p_ob=$(p "$(cnt 'exceeds budget')")
p_mem=$(p "$(cnt 'Insufficient Memory')")
p_hb=0   # hourly counter
echo "$(ts) watching PID $PID (fc=$p_fc fp=$p_fp ob=$p_ob mem=$p_mem)" >>"$OUT"

while kill -0 "$PID" 2>/dev/null; do
  sleep "${SI:-60}"
  fc=$(p "$(cnt 'frontier-contradicted')")
  fp=$(p "$(cnt 'tokenizer-fingerprint')")
  ob=$(p "$(cnt 'exceeds budget')")
  mem=$(p "$(cnt 'Insufficient Memory')")
  if (( fc > p_fc || fp > p_fp || ob > p_ob || mem > p_mem )); then
    echo "$(ts) ALERT soak: fc=$p_fc->$fc fp=$p_fp->$fp ob=$p_ob->$ob mem=$p_mem->$mem" >>"$OUT"
    echo "$(ts)   context: $(grep -E 'frontier-contradicted|tokenizer-fingerprint|exceeds budget|Insufficient Memory' "$LOG" | tail -3)" >>"$OUT"
    p_fc=$fc; p_fp=$fp; p_ob=$ob; p_mem=$mem
  fi
  p_fc=$fc; p_fp=$fp; p_ob=$ob; p_mem=$mem
  (( p_hb += 1 ))
  if (( p_hb >= ${SHB:-60} )); then
    p_hb=0
    maxtok=$(grep -o 'stored tokens=[0-9]*' "$LOG" | cut -d= -f2 | sort -n | tail -1)
    files=$(ls /tmp/ds4-kv/*.kv 2>/dev/null | wc -l | tr -d ' ')
    free=$(df -g /tmp | awk 'NR==2{print $4}')
    echo "$(ts) health pid=$PID fc=$fc fp=$fp ob=$ob mem=$mem max_saved=${maxtok:-0} kv_files=$files tmp_free=${free}G" >>"$OUT"
  fi
done

# died: classify + capture
{
  echo "=== $PID gone at $(ts) ==="
  if grep -q "shutdown requested" "$LOG" 2>/dev/null && \
     [ "$(grep -c 'shutdown requested' "$LOG")" -gt 0 ] && \
     tail -20 "$LOG" | grep -q "shutdown requested"; then
    echo "classification: PLANNED (shutdown requested in log tail)"
  else
    echo "classification: SUDDEN - external kill suspected, capturing state:"
    ps -A -o pid,ppid,lstart,command | grep -iE "ds4-server|launchd|cron|osascript" | grep -v grep | head -12
  fi
  echo "last log lines:"
  tail -5 "$LOG"
  echo "next pidfile (if restarted): $(cat "$PIDFILE" 2>/dev/null)"
} >>"$OUT"
