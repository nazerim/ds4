#!/bin/zsh
# u400: upstream with a budget that never evicts (ceiling for upstream's store/lookup design).
# u16fix: upstream + minimal fresh-entry recency fix, 16 GiB. Waits up to 120 min for /tmp/evictfix.ready.
# ALWAYS restores production on exit.
HERE=${0:A:h}; LOG=$HERE/runs/chain4.log
say() { echo "$(date '+%m-%d %H:%M:%S') $*" | tee -a $LOG; }
restore_prod() {
  cd /Users/naz/Projects/ds4
  pkill -f 'ds4-server --model gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx 65536' 2>/dev/null; sleep 5
  say "restoring production via ./ds4-server.sh start-qwen"
  ./ds4-server.sh start-qwen >> $LOG 2>&1
  for i in {1..60}; do
    curl -s -m 5 -o /dev/null http://127.0.0.1:8002/v1/models && { say "prod answering on :8002"; return; }
    sleep 5
  done
  say "WARNING: prod did not answer on :8002 within 5 min"
}
trap restore_prod EXIT
say "chain4 start"
say "arm u400";  BUDGET_MB=409600 $HERE/run-arm.sh u400 /Users/naz/Projects/ds4-ab-upstream 430 75 >> $LOG 2>&1; say "u400 exit=$?"
for i in {1..240}; do [ -f /tmp/evictfix.ready ] && break; sleep 30; done
if [ -f /tmp/evictfix.ready ]; then
  say "arm u16fix"; BUDGET_MB=16384 $HERE/run-arm.sh u16fix /Users/naz/Projects/ds4-ab-evictfix 430 60 >> $LOG 2>&1; say "u16fix exit=$?"
else say "skip u16fix: /tmp/evictfix.ready never appeared"; fi
say "chain4 done"
