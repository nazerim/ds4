#!/bin/zsh
# Resume-DEPTH experiment: does the answer depend on which checkpoint depth survived?
# Stops production, runs one isolated engine, ALWAYS restores production on exit.
set -u
REPO=/Users/naz/Projects/ds4
HERE=$REPO/.codebase-memory/20261118-kvstorm
PORT=8003
KV=/Volumes/FireCuda520/ds4-ab-depth
LOG=$HERE/runs/resumedepth.log
REPS=${REPS:-12}
MAXTOK=${MAXTOK:-256}
TARGET=${TARGET:-12000}
MODE=${MODE:-fork}          # fork = production build; upstream = b3f5018 build (different flag dialect)
WT=${WT:-$REPO}
say() { echo "$(date '+%m-%d %H:%M:%S') $*" | tee -a $LOG; }

restore_prod() {
  pkill -f "ds4-server --model gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx 65536 --tokens 4096 --host 127.0.0.1 --port $PORT" 2>/dev/null
  sleep 5
  cd $REPO
  say "restoring production"
  ./ds4-server.sh start-qwen >> $LOG 2>&1
  for i in {1..60}; do
    curl -s -m 5 -o /dev/null http://127.0.0.1:8002/v1/models && { say "prod answering on :8002"; return; }
    sleep 5
  done
  say "WARNING: prod did not answer within 5 min"
}
trap restore_prod EXIT

cd $REPO || exit 2
say "stopping production; resume-depth experiment (reps=$REPS target=$TARGET maxtok=$MAXTOK)"
./ds4-server.sh stop >> $LOG 2>&1
sleep 4
rm -rf "$KV"; mkdir -p "$KV"
: > $HERE/runs/depth-server.log
unset DS4_API_KEY QWEN_API_KEY QWEN_SUBSCRIPTION_KEY

cd $WT || exit 2
# Pin the continued-interval explicitly for BOTH builds. The shallow fallback checkpoint that
# step C resumes from is created at this interval, so leaving it to per-build defaults would make
# fork and upstream differ in the thing under test rather than in the code under test.
INTERVAL=(--kv-cache-continued-interval-tokens 8192)
if [ "$MODE" = "upstream" ]; then
  # b3f5018 rejects --kv-cache-small-dense and has no divergence anchors
  KVFLAGS=($INTERVAL)
else
  KVFLAGS=(--kv-cache-small-dense 49152 --kv-cache-max-divergence-anchors 8 $INTERVAL)
fi
./ds4-server --model gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx 65536 --tokens 4096 \
  --host 127.0.0.1 --port $PORT --kv-disk-dir "$KV" --kv-disk-space-mb 32768 \
  $KVFLAGS \
  >> $HERE/runs/depth-server.log 2>&1 &
SPID=$!
up=0
for i in {1..120}; do
  curl -s -m 5 -o /dev/null http://127.0.0.1:$PORT/v1/models && { up=1; break; }
  kill -0 $SPID 2>/dev/null || break
  sleep 5
done
[ $up = 1 ] || { say "engine failed to start"; tail -5 $HERE/runs/depth-server.log | tee -a $LOG; exit 3; }
say "engine up; every request is classified from the log, not assumed"

UNIQ=${UNIQ:-$RANDOM} python3 -I $HERE/resume_depth_probe.py --port $PORT \
  --log $HERE/runs/depth-server.log --kvdir "$KV" --reps $REPS --max-tokens $MAXTOK \
  --target-tokens $TARGET --out $HERE/runs/resume-depth.jsonl >> $LOG 2>&1
say "probe exit=$?"
say "done"
