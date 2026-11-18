#!/bin/zsh
# Does the disk cache change answers on REALISTIC prompts, not just repetitive synthetic prose?
#
# Every divergence reported so far used a deliberately repetitive corpus, which is the most favourable
# case for amplifying a near-tie. This run repeats the chunk-shape discriminator (disk ON vs disk OFF,
# same binary, byte-identical prompts, no checkpoint read on the OFF side) using real varied prose, and
# reports a rate with similarity, so "differs" cannot quietly mean "differs by one word".
#
# ALWAYS restores production on exit.
set -u
REPO=/Users/naz/Projects/ds4
HERE=$REPO/.codebase-memory/20261118-kvstorm
PORT=8003
KV=/Volumes/FireCuda520/ds4-ab-real
LOG=$HERE/runs/shapereal.log
REPS=${REPS:-6}
TARGET=${TARGET:-11700}
MAXTOK=${MAXTOK:-256}
UNIQ=REAL-$RANDOM
CPTF=$HERE/runs/real-cpt-$UNIQ.txt
CORPUS=${CORPUS:-/tmp/realcorpus.txt}
MODE=${MODE:-fork}      # fork = production build; upstream = b3f5018 build (older flag dialect)
WT=${WT:-$REPO}
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

start_engine() {   # $1 = on|off, $2 = logfile
  local slog=$2
  : > $slog
  local extra=() kvextra=()
  [ "$1" = "on" ] && extra=(--kv-disk-dir "$KV")
  [ "$1" = "on" ] && { rm -rf "$KV"; mkdir -p "$KV"; }
  # b3f5018 has neither --kv-cache-small-dense nor divergence anchors; passing them makes it
  # exit on an unknown argument, so the two builds cannot share one argv.
  if [ "$MODE" = "upstream" ]; then
    kvextra=(--kv-cache-continued-interval-tokens 8192)
  else
    kvextra=(--kv-cache-small-dense 49152 --kv-cache-max-divergence-anchors 8
             --kv-cache-continued-interval-tokens 8192)
  fi
  cd $WT || exit 2
  ./ds4-server --model gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx 65536 --tokens 4096 \
    --host 127.0.0.1 --port $PORT --kv-disk-space-mb 32768 $kvextra $extra >> $slog 2>&1 &
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
  ENGINE_PID=""; sleep 3
}

cd $REPO || exit 2
# Real varied prose, built from repo docs so the run is reproducible without a /tmp scratch file
# (an earlier harness imported its helper from /tmp and broke when it was cleaned).
if [ ! -s "$CORPUS" ]; then
  mkdir -p $(dirname $CORPUS)
  : > "$CORPUS"
  for f in $REPO/.codebase-memory/*.md $REPO/PLAN-*.md $REPO/*.md; do
    [ -f "$f" ] && [ $(wc -c < "$f" | tr -d ' ') -gt 800 ] && cat "$f" >> "$CORPUS" && printf '\n\n' >> "$CORPUS"
  done
  say "corpus built: $(wc -c < $CORPUS | tr -d ' ') chars from repo docs"
fi
[ -s "$CORPUS" ] || { say "no corpus at $CORPUS"; exit 2; }
say "stopping production; realistic-prose chunk-shape test (MODE=$MODE reps=$REPS target=$TARGET uniq=$UNIQ)"
./ds4-server.sh stop >> $LOG 2>&1
sleep 4
unset DS4_API_KEY QWEN_API_KEY QWEN_SUBSCRIPTION_KEY

say "phase 1: disk ON"
start_engine on $HERE/runs/real-on-server.log || { say "phase1 engine failed"; tail -5 $HERE/runs/real-on-server.log | tee -a $LOG; exit 3; }
python3 -I $HERE/chunkshape_probe.py --port $PORT --log $HERE/runs/real-on-server.log \
  --reps $REPS --target-tokens $TARGET --uniq $UNIQ --max-tokens $MAXTOK \
  --corpus-file $CORPUS --cpt-file $CPTF --expect-disk on \
  --out $HERE/runs/real-on.jsonl >> $LOG 2>&1
rc1=$?; stop_engine
say "phase 1 exit=$rc1 cpt=$(cat $CPTF 2>/dev/null)"
[ $rc1 = 0 ] || { say "phase 1 failed (a non-split schedule here means the cold-store split is not universal)"; exit 4; }

say "phase 2: disk OFF"
start_engine off $HERE/runs/real-off-server.log || { say "phase2 engine failed"; tail -5 $HERE/runs/real-off-server.log | tee -a $LOG; exit 3; }
python3 -I $HERE/chunkshape_probe.py --port $PORT --log $HERE/runs/real-off-server.log \
  --reps $REPS --target-tokens $TARGET --uniq $UNIQ --max-tokens $MAXTOK \
  --corpus-file $CORPUS --cpt-file $CPTF --expect-disk off \
  --out $HERE/runs/real-off.jsonl >> $LOG 2>&1
rc2=$?; stop_engine
say "phase 2 exit=$rc2"

python3 - "$HERE" <<'PY'
import difflib
import json
import sys

here = sys.argv[1]
on = {r["i"]: r for r in map(json.loads, open(here + "/runs/real-on.jsonl"))}
off = {r["i"]: r for r in map(json.loads, open(here + "/runs/real-off.jsonl"))}
common = sorted(set(on) & set(off))
print("\n=== REALISTIC PROSE: does --kv-disk-dir change the answer? ===")
same = diff = skipped = 0
sims = []
for i in common:
    a, d = on[i], off[i]
    if a["P"] != d["P"] or a["ctx_from"] != 0 or d["ctx_from"] != 0:
        print("  i=%-2d skipped (P %s/%s, ctx %s/%s) -- not the same cold prompt"
              % (i, a["P"], d["P"], a["ctx_from"], d["ctx_from"]))
        skipped += 1
        continue
    eq = a["text"] == d["text"]
    s = difflib.SequenceMatcher(None, a["text"], d["text"]).ratio()
    sims.append(s)
    same += eq
    diff += not eq
    print("  i=%-2d P=%-6s chunks on=%-14s off=%-14s %-8s similarity=%.3f"
          % (i, a["P"], "+".join(map(str, a["chunks"])), "+".join(map(str, d["chunks"])),
             "SAME" if eq else "DIFFER", s))
n = same + diff
if n:
    print("\n  prompts compared: %d   identical: %d   divergent: %d  -> rate %.0f%%   (skipped %d)"
          % (n, same, diff, 100.0 * diff / n, skipped))
    print("  similarity of divergent pairs: %s" % ["%.3f" % s for s in sims if s < 1.0])
    print("  compare with the repetitive synthetic corpus: 6/6 divergent, median similarity 0.156")
    if diff == 0:
        print("  => on realistic prose, enabling the disk cache did NOT change any answer.")
    elif min(s for s in sims if s < 1.0) < 0.5:
        print("  => realistic prose also diverges, and substantively (not one-word differences).")
    else:
        print("  => realistic prose diverges too, but mildly (all similarities >= 0.5).")
else:
    print("  INCONCLUSIVE: nothing comparable")
PY
rm -f $CPTF
say "done"
