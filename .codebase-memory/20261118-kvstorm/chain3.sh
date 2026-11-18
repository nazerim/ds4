#!/bin/zsh
# RESUME of chain2 after an accidental kill: f16a is already complete, so it is skipped. u16a restarts from scratch.
# grace-3600 fork arm. ALWAYS restores production at the end. No time cutoff (user: run to completion).
HERE=${0:A:h}
LOG=$HERE/runs/chain3.log
say() { echo "$(date '+%m-%d %H:%M:%S') $*" | tee -a $LOG; }
restore_prod() {
  cd /Users/naz/Projects/ds4
  pkill -f 'ds4-server --model gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx 65536' 2>/dev/null
  sleep 5
  say "restoring production via ./ds4-server.sh start-qwen"
  ./ds4-server.sh start-qwen >> $LOG 2>&1
  for i in {1..60}; do
    curl -s -m 5 -o /dev/null http://127.0.0.1:8002/v1/models && { say "prod answering on :8002"; return; }
    sleep 5
  done
  say "WARNING: prod did not answer on :8002 within 5 min"
}
trap restore_prod EXIT
export BUDGET_MB=16384
G0=(--kv-cache-small-dense 49152 --kv-cache-retire-grace-seconds 0 --kv-cache-max-divergence-anchors 8)
G3=(--kv-cache-small-dense 49152 --kv-cache-retire-grace-seconds 3600 --kv-cache-max-divergence-anchors 8)
FORK=/Users/naz/Projects/ds4; UP=/Users/naz/Projects/ds4-ab-upstream
say "chain3 start (budget 16384 MiB)"
say "arm u16a";  $HERE/run-arm.sh u16a $UP   700 50 >> $LOG 2>&1;      say "u16a exit=$?"
say "arm u16b";  $HERE/run-arm.sh u16b $UP   700 50 >> $LOG 2>&1;      say "u16b exit=$?"
say "arm f16b";  $HERE/run-arm.sh f16b $FORK 700 50 $G0 >> $LOG 2>&1; say "f16b exit=$?"
say "arm g16";   $HERE/run-arm.sh g16  $FORK 130 20 $G3 >> $LOG 2>&1; say "g16 exit=$?"
say "chain3 done"
