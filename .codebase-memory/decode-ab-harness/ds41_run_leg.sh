#!/bin/zsh
# Sequential A/B legs for the decode-degradation fixes. Run AFTER baseline soak.
# Usage: run_leg.sh <leg> ; legs: d1024 | do | do_d1024 | d4096
set -u
cd /Users/naz/Projects/ds4
LEG=${1:?leg name required}
SOAK="${0:a:h}/ds41_soak.py"
MARKFILE=/tmp/ds41_leg_$LEG.window

declare -A ENVS
ENVS[d1024]="DS4_METAL_STREAM_EXPERT_HOTNESS_DECAY_TOKENS=1024"
ENVS[do]="DS4_STREAM_HOTNESS_DECODE_ONLY=1"
ENVS[do_d1024]="DS4_STREAM_HOTNESS_DECODE_ONLY=1 DS4_METAL_STREAM_EXPERT_HOTNESS_DECAY_TOKENS=1024"
ENVS[d4096]="DS4_METAL_STREAM_EXPERT_HOTNESS_DECAY_TOKENS=4096"
ENVS[hl]="DS4_STREAM_HOTNESS_DECODE_ONLY=1 DS4_METAL_STREAM_EXPERT_HOTNESS_DECAY_TOKENS=1024 DS4_METAL_STREAMING_EXPERT_HOTLIST=/tmp/ds41_hotlist_$LEG.txt"
[ -n "${ENVS[$LEG]:-}" ] || { echo "unknown leg $LEG"; exit 1; }

# hotlist leg needs the recorded file
if [ "$LEG" = "hl" ] && [ ! -s /tmp/ds41_hotlist_$LEG.txt ]; then
  echo "hl leg needs /tmp/ds41_hotlist_$LEG.txt"; exit 1
fi

echo "=== leg $LEG start $(date +%m-%d\ %H:%M:%S)" | tee -a /tmp/ds41_legs.log
echo "env: ${ENVS[$LEG]}" | tee -a /tmp/ds41_legs.log
date +%s > $MARKFILE.start
{ eval "env ${ENVS[$LEG]} DS4_MOE_RECORD_SELECTED_HOTLIST=/tmp/ds41_hotlist_$LEG.txt ./ds4-server.sh restart-v41" >/tmp/ds41_restart_$LEG.log 2>&1
for i in $(seq 1 60); do sleep 5; tail -1 log/ds4-v41.log | grep -q 'listening on\|serving' && break; grep -q 'listening on http://127.0.0.1:8003' <(tail -5 log/ds4-v41.log) && break; done
tail -2 /tmp/ds41_restart_$LEG.log
python3 $SOAK $LEG 24 2>&1 | tee /tmp/ds41_soak_$LEG.log
date +%s > $MARKFILE.end
echo "=== leg $LEG done $(date +%m-%d\ %H:%M:%S)" | tee -a /tmp/ds41_legs.log; }
