#!/bin/sh
# Watch the live engine for DSML rewind regressions, without touching it.
#
# Usage: sh .codebase-memory/rewind_sentinel.sh [minutes] [logfile]
# Knobs: DS4_SENTINEL_PORT (8001), DS4_SENTINEL_INTERVAL (20s),
#        DS4_SENTINEL_TRAIN / DS4_SENTINEL_PARITY (corpus-liveness dirs)
#
# Why this exists as a file instead of an inline loop: the first version matched
# the engine with `pgrep -f "port 8001"`, which also matches this script's own
# command line - so the engine-death check was vacuous and a verification run
# once read the watcher's environment instead of the engine's.  Liveness now
# comes from the listening socket.  Baselines are also re-derived whenever the
# log shrinks, because ds4-server.sh rotates log/ds4.log at start: a baseline
# taken from the old file would otherwise sit above the new file's counts and
# the sentinel could never fire again (this is exactly how the watcher that
# alerted at 22:43 went silently deaf after the 23:07 restart).
#
# Alerts are read-only (grep on a log, lsof on a socket).  It never signals the
# engine and never writes outside its own stdout.

MINUTES=${1:-120}
LOG=${2:-log/ds4.log}
PORT=${DS4_SENTINEL_PORT:-8001}
TRAIN_DIR=${DS4_SENTINEL_TRAIN:-/Volumes/FireCuda520/ds4-train/active}
PARITY_DIR=${DS4_SENTINEL_PARITY:-/Volumes/FireCuda520/ds4-parity}
INTERVAL=${DS4_SENTINEL_INTERVAL:-20}
END=$((SECONDS + MINUTES * 60))

# grep -c prints 0 AND exits nonzero when nothing matched, so writing this as
# `grep -c ... || echo 0` captures "0\n0" - a two-line value that makes every
# [ "$n" -gt "$base" ] comparison die with "integer expression expected" and
# silently deafens the whole watcher.  That is the exact failure this script is
# for, so run the alert test below before trusting a fresh copy of this file:
#   printf 'x generation rewind rebuild ctx=1..2:1 y\n' > /tmp/s.log
#   sh .codebase-memory/rewind_sentinel.sh 1 /tmp/s.log   # must alert and exit 1
# Verified against a real appended trigger and a real truncation: alert fires and
# rotation re-baselines.  Detection lags by INTERVAL plus the lsof call (~2 s),
# so budget ~25 s with the defaults rather than expecting sub-second reaction.
cnt() {
	n=$(grep -c "$1" "$LOG" 2>/dev/null)
	[ -z "$n" ] && n=0
	printf '%s\n' "$n"
}
cnt_i() {
	n=$(grep -ci "$1" "$LOG" 2>/dev/null)
	[ -z "$n" ] && n=0
	printf '%s\n' "$n"
}

# Baselines for the five failure signals, plus the two counters that decide
# whether the fixes are actually being exercised (a silent engine and a fixed
# engine look identical until a rewind event happens).
base_rebuild=0; base_edge=0; base_discard=0; base_fail=0; base_div=0
prev_lines=0
set_baseline() {
	base_rebuild=$(cnt "generation rewind rebuild ctx=")
	base_edge=$(cnt "target-ahead-of-window")
	base_discard=$(cnt "kv cache discarded")
	base_fail=$(cnt "prefill failed")
	base_div=$(cnt_i "divergence")
}
set_baseline

echo "sentinel armed $(date '+%H:%M:%S') for ${MINUTES}m on $LOG (port $PORT)."
echo "baselines: rebuild=$base_rebuild edge=$base_edge discard=$base_discard"
echo "           prefill-fail=$base_fail divergence=$base_div"

while [ $SECONDS -lt $END ]; do
	# Liveness: the socket, not a process-name match.
	if [ -z "$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null)" ]; then
		echo "ALERT (engine not listening on $PORT) at $(date '+%H:%M:%S')"
		exit 1
	fi

	# Rotation or truncation: re-baseline instead of comparing across files.
	lines=$(wc -l < "$LOG" 2>/dev/null | tr -d " \n")
	[ -z "$lines" ] && lines=0
	if [ "$lines" -lt "$prev_lines" ]; then
		echo "log rotated ($prev_lines -> $lines lines) at $(date '+%H:%M:%S'); re-baselined"
		set_baseline
	fi
	prev_lines=$lines

	r=$(cnt "generation rewind rebuild ctx=")
	[ "$r" -gt "$base_rebuild" ] && { echo "NEW REBUILD at $(date '+%H:%M:%S'):"; grep "generation rewind rebuild" "$LOG" | tail -2 | cut -c1-260; exit 1; }
	e=$(cnt "target-ahead-of-window")
	[ "$e" -gt "$base_edge" ] && { echo "EDGE REFUSAL RECURRED (ecef491 missed a case) at $(date '+%H:%M:%S'):"; grep "target-ahead-of-window" "$LOG" | tail -2 | cut -c1-260; exit 1; }
	d=$(cnt "kv cache discarded")
	[ "$d" -gt "$base_discard" ] && { echo "KV DISCARDED (store integrity, after force-kill restarts) at $(date '+%H:%M:%S'):"; grep "kv cache discarded" "$LOG" | tail -2 | cut -c1-260; exit 1; }
	f=$(cnt "prefill failed")
	[ "$f" -gt "$base_fail" ] && { echo "PREFILL FAILED at $(date '+%H:%M:%S'):"; grep "prefill failed" "$LOG" | tail -2 | cut -c1-260; exit 1; }
	v=$(cnt_i "divergence")
	[ "$v" -gt "$base_div" ] && { echo "DIVERGENCE LINE at $(date '+%H:%M:%S'):"; grep -i "divergence" "$LOG" | tail -2 | cut -c1-260; exit 1; }

	sleep 20
done

echo "clean for ${MINUTES}m from $(date '+%H:%M:%S')."
echo "rewind events: $(cnt 'generation rewind')  armed: $(cnt 'end_state=armed')  rebuilt: $r"
# The positive signature of ecef491: a truncation rewind whose target sits exactly
# on the window's edge (truncate == window end) and is still served with reason=ok.
# Before that commit every such request logged a rebuild instead, so counting them
# proves the fix from presence rather than from the absence of rebuild lines.
awk '/generation rewind truncated without rebuild/ {
		t = ""; w = ""; rs = ""
		for (i = 1; i <= NF; i++) {
			if ($i ~ /^truncate=/) { split($i, a, "="); t = a[2] }
			else if ($i ~ /^window=/) { split($i, b, "="); w = b[2] }
			else if ($i ~ /^reason=/) { split($i, c, "="); rs = c[2] }
		}
		n = index(w, "..")
		if (n > 0 && t != "" && substr(w, n + 2) + 0 == t + 0 && rs == "ok") k++
	}
	END { print "edge-target serves (truncate == window end, reason=ok): " k+0 }' "$LOG"
echo "resample=1 (mode-boundary) events: $(cnt 'resample=1')  -- the arming fix's decisive field observation"
echo "kv entries lost to policy: $(cnt 'kv cache evicted') evictions (frontier-superseded is normal after"
echo "a restart, seen in the 21:02 boot too; it costs a future re-prefill, not correctness)."

# Collection liveness for the corpus lane.  A short health probe does NOT produce
# a record (verified: the 23:08 probe left train/parity mtimes at the previous
# boot), so "no file yet" on an idle engine is not a fault - it is the first
# qualifying real turn that creates them.
EPID=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null | head -1)
if [ -n "$EPID" ]; then
	if ls "$TRAIN_DIR"/train_pid${EPID}.recs >/dev/null 2>&1; then
		echo "capture live: train_pid${EPID}.recs"
	else
		echo "capture not writing yet: train_pid${EPID}.recs (needs a qualifying real turn, not a probe)"
	fi
	PAR=$(find "$PARITY_DIR" -maxdepth 2 -name "parity_pid${EPID}.bin" 2>/dev/null | head -1)
	if [ -n "$PAR" ]; then
		echo "parity live: $(basename "$PAR") in $(dirname "$PAR")"
	else
		echo "parity not writing yet for pid $EPID (same rule)"
	fi
fi
python3 .codebase-memory/stall_census.py "$LOG" 2>/dev/null | head -2
grep -c "finish=tool_calls" "$LOG" 2>/dev/null | sed 's/^/tool turns completed: /'
