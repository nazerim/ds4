#!/bin/zsh
# Waits for the running fork-g0 arm, then runs U1, U2, F2 (A/B/B/A with F1), then ALWAYS
# restores production exactly as the launcher defines it. An arm that would START after
# CUTOFF is skipped so prod is back well before morning.
HERE=${0:A:h}
CUTOFF=${CUTOFF:-0615}          # HHMM, local
LOG=$HERE/runs/chain.log
mkdir -p $HERE/runs
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

while pgrep -f 'run-arm.sh fork-g0' > /dev/null; do sleep 20; done
say "fork-g0 finished"

FORKFLAGS=(--kv-cache-small-dense 49152 --kv-cache-retire-grace-seconds 0 --kv-cache-max-divergence-anchors 8)
for spec in "u1 /Users/naz/Projects/ds4-ab-upstream" "u2 /Users/naz/Projects/ds4-ab-upstream" "f2 /Users/naz/Projects/ds4"; do
  set -- ${=spec}; name=$1; wt=$2
  [ "$(date +%H%M)" -ge "$CUTOFF" ] && { say "skip $name: past cutoff $CUTOFF"; continue; }
  say "start arm $name"
  if [ $name = f2 ]; then
    $HERE/run-arm.sh $name $wt 700 60 $FORKFLAGS >> $LOG 2>&1
  else
    $HERE/run-arm.sh $name $wt 700 60 >> $LOG 2>&1
  fi
  say "arm $name exit=$?"
done
say "all arms done"
