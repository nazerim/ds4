#!/bin/zsh
# Is ds4 deterministic ACROSS PROCESSES? Same binary, same fixed prompts, two separate engine
# processes, each with its own empty KV dir (so no cache continuity at all between rounds).
#
# Why: the eviction arms u16a and u400 (identical binary, identical schedule) disagreed on
# 193 of 440 replies, yet the controlled cold-vs-disk-hit test shows 0 divergence within one
# process on BOTH builds. The remaining candidate is cross-process nondeterminism -- expert
# cache warm-up order, kernel selection, thread scheduling -- which is a different problem
# from "the KV cache changes the answer" and is largely benign for a local model.
#
# ALWAYS restores production on exit.
set -u
REPO=/Users/naz/Projects/ds4
HERE=$REPO/.codebase-memory/20261118-kvstorm
PORT=8003
LOG=$HERE/runs/crossproc.log
NPROMPT=${NPROMPT:-12}
MAXTOK=${MAXTOK:-768}
MODE=${MODE:-fork}
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
say "stopping production (MODE=$MODE, $NPROMPT prompts x 2 separate processes)"
./ds4-server.sh stop >> $LOG 2>&1
sleep 4
unset DS4_API_KEY QWEN_API_KEY QWEN_SUBSCRIPTION_KEY

if [ "$MODE" = "upstream" ]; then FLAGS=(--kv-disk-space-mb 32768); else
  FLAGS=(--kv-disk-space-mb 32768 --kv-cache-small-dense 49152 --kv-cache-max-divergence-anchors 8); fi

for round in 1 2; do
  KV=/Volumes/FireCuda520/ds4-ab-xp$round
  rm -rf "$KV"; mkdir -p "$KV"
  : > $HERE/runs/xp-server-$round.log
  cd $WT || exit 2
  say "round $round: starting engine (fresh empty KV dir)"
  ./ds4-server --model gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx 65536 --tokens 4096 \
    --host 127.0.0.1 --port $PORT --kv-disk-dir "$KV" $FLAGS \
    >> $HERE/runs/xp-server-$round.log 2>&1 &
  SPID=$!
  up=0
  for i in {1..120}; do
    curl -s -m 5 -o /dev/null http://127.0.0.1:$PORT/v1/models && { up=1; break; }
    kill -0 $SPID 2>/dev/null || break
    sleep 5
  done
  [ $up = 1 ] || { say "round $round engine failed"; tail -5 $HERE/runs/xp-server-$round.log | tee -a $LOG; exit 3; }
  cd $REPO
  python3 -I $HERE/cachediver_probe.py --port $PORT --n $NPROMPT --max-tokens $MAXTOK --attach \
    --out $HERE/runs/crossproc-round$round.jsonl >> $LOG 2>&1
  say "round $round probe exit=$? (pid $SPID)"
  kill $SPID 2>/dev/null; wait $SPID 2>/dev/null
  sleep 3
done

python3 - "$HERE" <<'PY'
import json
import sys

here = sys.argv[1]


def load(name):
    """key by (prompt index, prompt_tokens) so a length change is itself a mismatch signal"""
    out = {}
    with open(here + "/runs/" + name, encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            out[(r["i"], r["prompt_tokens"])] = r["cold"]
    return out


a = load("crossproc-round1.jsonl")
b = load("crossproc-round2.jsonl")
common = sorted(set(a) & set(b))
same = sum(1 for k in common if a[k] == b[k])
print("\nCROSS-PROCESS (two separate engines, identical prompts, no shared cache state)")
print("  prompts compared: %d   identical: %d   divergent: %d"
      % (len(common), same, len(common) - same))
if len(a) != len(b):
    print("  NOTE: rounds produced %d and %d records" % (len(a), len(b)))
for k in common:
    if a[k] != b[k]:
        first = next((i for i in range(min(len(a[k]), len(b[k]))) if a[k][i] != b[k][i]), 0)
        print("   i=%s P=%s first_diff_char=%d" % (k[0], k[1], first))
PY
say "done"
