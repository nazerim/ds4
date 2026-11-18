#!/bin/zsh
# Cache-divergence A/B: cold prefill vs disk-checkpoint resume, same binary and prompts.
# Stops production, starts an isolated engine (own port, own empty KV dir), runs the probe,
# and ALWAYS restores production on exit.
set -u
REPO=/Users/naz/Projects/ds4
HERE=$REPO/.codebase-memory/20261118-kvstorm
PORT=8003
KV=/Volumes/FireCuda520/ds4-ab-cdiv
LOG=$HERE/runs/cdiv.log
NPROMPT=${NPROMPT:-12}
MAXTOK=${MAXTOK:-32}
TURNS=${TURNS:-0}
MODE=${MODE:-fork}      # fork = production build+flags; upstream = b3f5018 build with its own flag dialect
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
say "stopping production (idle-verified first) MODE=$MODE wt=$WT"
./ds4-server.sh stop >> $LOG 2>&1
sleep 4
[ -e "$KV" ] && rm -rf "$KV"
mkdir -p "$KV"
unset DS4_API_KEY QWEN_API_KEY QWEN_SUBSCRIPTION_KEY

if [ -e $HERE/runs/cdiv-server.log ]; then mv $HERE/runs/cdiv-server.log $HERE/runs/cdiv-server-prev.log; fi
: > $HERE/runs/cdiv-server.log      # so the preflight hit counter starts from zero
say "starting isolated engine on :$PORT"
cd $WT || exit 2
if [ "$MODE" = "upstream" ]; then
  # b3f5018 has no --kv-cache-small-dense and no divergence anchors; those are fork-only.
  FLAGS=(--kv-disk-space-mb 32768)
else
  FLAGS=(--kv-disk-space-mb 32768 --kv-cache-small-dense 49152 --kv-cache-max-divergence-anchors 8)
fi
./ds4-server --model gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx 65536 --tokens 4096 \
  --host 127.0.0.1 --port $PORT --kv-disk-dir "$KV" $FLAGS \
  >> $HERE/runs/cdiv-server.log 2>&1 &
SPID=$!

up=0
for i in {1..120}; do
  curl -s -m 5 -o /dev/null http://127.0.0.1:$PORT/v1/models && { up=1; break; }
  kill -0 $SPID 2>/dev/null || break
  sleep 5
done
[ $up = 1 ] || { say "engine did not come up"; tail -5 $HERE/runs/cdiv-server.log | tee -a $LOG; exit 3; }
# PREFLIGHT: prove the disk path is actually exercised before spending the window.
# An earlier version compared cold against cold (prompts were under the 2048-token store
# step), ran in 34 s, and reported 100% agreement -- a meaningless result that only looked
# fine because nobody checked the hit counter. Do not repeat that.
h0=$(grep -ac 'kv cache hit' $HERE/runs/cdiv-server.log)
say "preflight: one prompt, expecting a store and a disk hit"
# The preflight must NOT reuse a corpus prompt: it shares this engine and this KV dir with
# the measured run, so a repeated prompt would already be cached and pass 1 would not be cold.
python3 -I $HERE/cachediver_probe.py --port $PORT --n 1 --attach \
  --unique-prefix PREFLIGHT-DO-NOT-REUSE --out $HERE/runs/cdiv-preflight.jsonl >> $LOG 2>&1
h1=$(grep -ac 'kv cache hit' $HERE/runs/cdiv-server.log)
if [ "$h1" -le "$h0" ]; then
  say "PREFLIGHT FAILED: no disk hit; nothing was storable or loadable. Aborting, prod restored by trap."
  grep -a 'kv cache stored' $HERE/runs/cdiv-server.log | tail -3 | tee -a $LOG
  exit 4
fi
say "preflight ok (disk-hit lines $h0 -> $h1): mode=$([ $TURNS -gt 0 ] && echo "multi-turn x$TURNS x3 convs" || echo "$NPROMPT single-turn"), max_tokens=$MAXTOK"

rc=0
if [ "$NPROMPT" -gt 0 ]; then
  python3 -I $HERE/cachediver_probe.py --port $PORT --n $NPROMPT --max-tokens $MAXTOK --attach \
    --unique-prefix ${UNIQ_TAG:-main} --out $HERE/runs/cachediver-single.jsonl >> $LOG 2>&1
  say "single-turn probe exit=$?"; rc=$?
fi
if [ "$TURNS" -gt 0 ]; then
  python3 -I $HERE/cachediver_probe.py --port $PORT --turns $TURNS --max-tokens $MAXTOK --attach \
    --out $HERE/runs/cachediver-multi.jsonl >> $LOG 2>&1
  say "multi-turn probe exit=$?"; rc=$?
fi

hits=$(grep -ac 'kv cache hit' $HERE/runs/cdiv-server.log)
cold=$(grep -ac 'live kv cache miss' $HERE/runs/cdiv-server.log)
say "server-side: disk-hit lines=$hits  live-miss lines=$cold (hits should be >= $NPROMPT)"
say "done"
