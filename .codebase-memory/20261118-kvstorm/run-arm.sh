#!/bin/zsh
# usage: run-arm.sh <name> <worktree> <requests> <max-minutes> [extra ds4-server flags...]
# Starts an isolated ds4-server (own port, own empty KV dir, no API key, no vision/mtp),
# replays the kvstorm schedule, stops the server, writes a summary. Never touches
# the production cache dir or port.
set -u
NAME=$1; WT=$2; NREQ=$3; MINS=$4; shift 4
HERE=${0:A:h}
OUT=$HERE/runs/$NAME; mkdir -p $OUT
KV=/Volumes/FireCuda520/ds4-ab-$NAME
PORT=8003
BUDGET_MB=${BUDGET_MB:-6144}
CTX=${CTX:-65536}
[ -e "$KV" ] && { echo "refusing: $KV exists"; exit 2; }
mkdir -p $KV
unset DS4_API_KEY QWEN_API_KEY QWEN_SUBSCRIPTION_KEY

cd $WT || exit 2
./ds4-server --model gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx $CTX --tokens 4096 \
  --host 127.0.0.1 --port $PORT --kv-disk-dir $KV --kv-disk-space-mb $BUDGET_MB \
  "$@" > $OUT/server.log 2>&1 &
SPID=$!
echo "$NAME server pid=$SPID args: --ctx $CTX --kv-disk-space-mb $BUDGET_MB $*" | tee $OUT/meta.txt

up=0
for i in {1..120}; do
  if curl -s -m 5 -o /dev/null http://127.0.0.1:$PORT/v1/models; then up=1; break; fi
  kill -0 $SPID 2>/dev/null || break
  sleep 5
done
[ $up = 1 ] || { echo "server did not come up"; tail -5 $OUT/server.log; kill $SPID 2>/dev/null; exit 3; }
echo "ready after ~$((i*5))s" | tee -a $OUT/meta.txt

cd /Users/naz/Projects/ds4
python3 -I $HERE/kvstorm.py --url http://127.0.0.1:$PORT/v1/chat/completions \
  --out $OUT/requests.jsonl --requests $NREQ --max-minutes $MINS
echo "generator exit=$?" | tee -a $OUT/meta.txt

kill $SPID; wait $SPID 2>/dev/null
du -sh $KV | tee -a $OUT/meta.txt
ls -1 $KV | wc -l | tr -d ' ' | sed 's/^/kv files left: /' | tee -a $OUT/meta.txt
python3 -I $HERE/summarize.py $OUT > $OUT/summary.txt 2>&1; cat $OUT/summary.txt
