#!/bin/zsh
# Like ds41_run_leg.sh but on the DSpark lane (restart-v41-dspark + stats).
# Legs: dspark_base | dspark_noexact | dspark_do | dspark_dodo1024 | dspark_dodo1024_hl
set -u
cd /Users/naz/Projects/ds4
LEG=${1:?leg name required}
SOAK="${0:a:h}/ds41_soak.py"
declare -A ENVS
ENVS[dspark_base]=""
ENVS[dspark_noexact]="DSPARK_EXACT=0"
ENVS[dspark_do]="DS4_STREAM_HOTNESS_DECODE_ONLY=1"
ENVS[dspark_dodo1024]="DS4_STREAM_HOTNESS_DECODE_ONLY=1 DS4_METAL_STREAM_EXPERT_HOTNESS_DECAY_TOKENS=1024"
ENVS[dspark_dodo1024_hl]="DS4_STREAM_HOTNESS_DECODE_ONLY=1 DS4_METAL_STREAM_EXPERT_HOTNESS_DECAY_TOKENS=1024"
[ -n "${ENVS[$LEG]+set}" ] || { echo "unknown leg $LEG"; exit 1; }

HLARG=""
if [[ "$LEG" == *_hl ]]; then
  # decode-biased hotlist recorded by the previous dspark leg
  cp -f /tmp/ds41_hotlist_${LEG%_hl}.txt /tmp/ds41_hotlist_${LEG}.txt 2>/dev/null || { echo "missing source hotlist"; exit 1; }
  HLARG="DS4_METAL_STREAMING_EXPERT_HOTLIST=/tmp/ds41_hotlist_${LEG}.txt"
fi

echo "=== leg $LEG start $(date +%m-%d\ %H:%M:%S)" | tee -a /tmp/ds41_legs.log
date +%s > /tmp/ds41_leg_$LEG.window.start
{ eval "env DS4_DSPARK_STATS=1 ${ENVS[$LEG]} $HLARG DS4_MOE_RECORD_SELECTED_HOTLIST=/tmp/ds41_hotlist_$LEG.txt ./ds4-server.sh restart-v41-dspark" >/tmp/ds41_restart_$LEG.log 2>&1
for i in $(seq 1 60); do sleep 5; grep -q 'listening on http://127.0.0.1:8003' <(tail -5 log/ds4-v41.log) && break; done
grep -E 'DSpark support|listening' /tmp/ds41_restart_$LEG.log | tail -2
python3 $SOAK $LEG 24 2>&1 | tee /tmp/ds41_soak_$LEG.log
# graceful stop: session release flushes DS4_DSPARK_STATS accept_rate lines
./ds4-server.sh stop >/dev/null 2>&1
sleep 2
grep -E 'accept_rate|accepted_draft' log/ds4-v41.log | tail -2 | cut -c1-160
cp log/ds4-v41.log /tmp/ds41_leg_$LEG.log
date +%s > /tmp/ds41_leg_$LEG.window.end
echo "=== leg $LEG done $(date +%m-%d\ %H:%M:%S)" | tee -a /tmp/ds41_legs.log; }
