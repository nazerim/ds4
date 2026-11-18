#!/bin/zsh
# Chunk-shape discriminator: is the completion a function of prefill CHUNK SHAPE alone?
#
# Same fork binary, same byte-identical prompts, two engine configs differing in ONE flag:
#   phase 1  --kv-disk-dir <dir>  -> cold prefill splits at cold_store_len: 8192+2048+1459
#                                    also yields resume@10240 (1459) and resume@8192 (3507)
#   phase 2  no --kv-disk-dir     -> no cold store, so no split: cold runs 8192+3507
#
#   H2 (checkpoint faithful, shape decides)  =>  cold_off == resume@8192  and  cold_off != cold_on
#   H1 (continued checkpoint unfaithful)     =>  cold_off != resume@8192
#
# ALWAYS restores production on exit.
set -u
REPO=/Users/naz/Projects/ds4
HERE=$REPO/.codebase-memory/20261118-kvstorm
PORT=8003
KV=/Volumes/FireCuda520/ds4-ab-shape
LOG=$HERE/runs/chunkshape.log
REPS=${REPS:-6}
TARGET=${TARGET:-12000}
MAXTOK=${MAXTOK:-256}
UNIQ=${UNIQ:-SHAPE-TEST}
CPTF=$HERE/runs/shape-cpt.txt
say() { echo "$(date '+%m-%d %H:%M:%S') $*" | tee -a $LOG; }

restore_prod() {
  pkill -f "ds4-server --model gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx 65536" 2>/dev/null
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

start_engine() {   # $1 = "on"|"off", $2 = logfile
  local slog=$2
  : > $slog
  if [ "$1" = "on" ]; then
    rm -rf "$KV"; mkdir -p "$KV"
    ./ds4-server --model gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx 65536 --tokens 4096 \
      --host 127.0.0.1 --port $PORT --kv-disk-dir "$KV" --kv-disk-space-mb 32768 \
      --kv-cache-small-dense 49152 --kv-cache-max-divergence-anchors 8 \
      --kv-cache-continued-interval-tokens 8192 >> $slog 2>&1 &
  else
    # identical argv minus the disk dir: kv flags are then inert (kv_cache_open is not called)
    ./ds4-server --model gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx 65536 --tokens 4096 \
      --host 127.0.0.1 --port $PORT --kv-disk-space-mb 32768 \
      --kv-cache-small-dense 49152 --kv-cache-max-divergence-anchors 8 \
      --kv-cache-continued-interval-tokens 8192 >> $slog 2>&1 &
  fi
  ENGINE_PID=$!
  for i in {1..120}; do
    curl -s -m 5 -o /dev/null http://127.0.0.1:$PORT/v1/models && return 0
    kill -0 $ENGINE_PID 2>/dev/null || return 1
    sleep 5
  done
  return 1
}

stop_engine() {
  [ -n "${ENGINE_PID:-}" ] || return 0
  kill $ENGINE_PID 2>/dev/null; wait $ENGINE_PID 2>/dev/null
  ENGINE_PID=""
  sleep 3
}

cd $REPO || exit 2
say "stopping production; chunk-shape experiment (reps=$REPS target=$TARGET uniq=$UNIQ)"
./ds4-server.sh stop >> $LOG 2>&1
sleep 4
unset DS4_API_KEY QWEN_API_KEY QWEN_SUBSCRIPTION_KEY

say "phase 1: disk ON (cold store splits the prefill)"
start_engine on $HERE/runs/shape-on-server.log || { say "phase1 engine failed"; tail -5 $HERE/runs/shape-on-server.log | tee -a $LOG; exit 3; }
UNIQ=$UNIQ python3 -I $HERE/resume_depth_probe.py --port $PORT \
  --log $HERE/runs/shape-on-server.log --kvdir "$KV" --reps $REPS \
  --target-tokens $TARGET --max-tokens $MAXTOK --cpt-file $CPTF \
  --out $HERE/runs/shape-on-depth.jsonl >> $LOG 2>&1
rc1=$?
say "phase 1 probe exit=$rc1 cpt=$(cat $CPTF 2>/dev/null)"
stop_engine
[ $rc1 = 0 ] || { say "phase 1 failed, aborting (prod restored by trap)"; exit 4; }

say "phase 2: disk OFF (no cold store, so no split)"
start_engine off $HERE/runs/shape-off-server.log || { say "phase2 engine failed"; tail -5 $HERE/runs/shape-off-server.log | tee -a $LOG; exit 3; }
python3 -I $HERE/chunkshape_probe.py --port $PORT \
  --log $HERE/runs/shape-off-server.log --reps $REPS --target-tokens $TARGET \
  --uniq $UNIQ --cpt-file $CPTF --max-tokens $MAXTOK --expect-disk off \
  --out $HERE/runs/shape-off.jsonl >> $LOG 2>&1
rc2=$?
say "phase 2 probe exit=$rc2"
stop_engine

python3 - "$HERE" <<'PY'
import json
import sys

here = sys.argv[1]
on = [json.loads(l) for l in open(here + "/runs/shape-on-depth.jsonl")]
off = [json.loads(l) for l in open(here + "/runs/shape-off.jsonl")]
off_by_i = {r["i"]: r for r in off}

print("\n=== CHUNK SHAPES OBSERVED ===")
for r in on[:2]:
    print("  disk ON  cold     : ctx=%s P=%s" % (r["A_ctx_from"], r["P"]))
for r in off[:2]:
    print("  disk OFF cold     : ctx=%s P=%s chunks=%s"
          % (r["ctx_from"], r["P"], "+".join(map(str, r["chunks"]))))

ad = cd = ac = 0
valid = 0
mismatch = []
print("\n=== PER-PROMPT (A=cold-on, C=resume@8192, D=cold-off) ===")
for r in on:
    i = r["i"]
    o = off_by_i.get(i)
    if not o:
        print("  i=%-2d no disk-off counterpart" % i)
        continue
    if o["P"] != r["P"]:
        mismatch.append((i, r["P"], o["P"]))
        print("  i=%-2d PROMPT LENGTHS DIFFER (%s vs %s): not the same prompt, skipping"
              % (i, r["P"], o["P"]))
        continue
    valid += 1
    A, C, D = r["textA"], r["textC"], o["text"]
    ad += (A == D)
    cd += (C == D)
    ac += (A == C)
    print("  i=%-2d P=%-6s A==C:%-5s A==D:%-5s C==D:%-5s"
          % (i, r["P"], A == C, A == D, C == D))

if mismatch:
    print("\n  WARNING: %d prompts differed in length across phases -- sizing drift"
          % len(mismatch))
print("\n=== VERDICT (valid comparisons: %d) ===" % valid)
if not valid:
    print("  INCONCLUSIVE: nothing comparable")
    sys.exit(0)
# ac/ad/cd count SAMENESS. Fixture-testing caught these two lines printing it inverted.
print("  A vs C  cold-on vs resume@8192 : same=%d differ=%d  (known: resume depth changes output)"
      % (ac, valid - ac))
print("  A vs D  cold-on vs cold-off    : same=%d differ=%d  (does the cold STORE SPLIT alone change output?)"
      % (ad, valid - ad))
print("  C vs D  resume@8192 vs cold-off: same=%d differ=%d  (is the mid-prefill checkpoint a faithful snapshot?)"
      % (cd, valid - cd))
if cd == valid and ad == 0:
    print("\n  => SUPPORTS H2: the 'continued' checkpoint restores state faithfully; the answer")
    print("     tracks the prefill CHUNK SCHEDULE, and enabling the disk cache changes that schedule.")
elif cd < valid:
    print("\n  => SUPPORTS H1: cold-with-no-split != resume@8192 even though both run a 3507 tail,")
    print("     so the mid-prefill checkpoint does NOT restore the state cold prefill had at 8192.")
else:
    print("\n  => MIXED: read runs/shape-on-depth.jsonl and runs/shape-off.jsonl directly.")
PY
say "done"
