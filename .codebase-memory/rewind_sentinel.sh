#!/bin/sh
# Watch the live engine for DSML rewind regressions, without touching it.
#
# Usage:
#   sh .codebase-memory/rewind_sentinel.sh [minutes] [logfile]   # watch, alert, exit 1
#   sh .codebase-memory/rewind_sentinel.sh status [logfile]      # snapshot now, exit 0
#
# Knobs: DS4_SENTINEL_PORT (8001), DS4_SENTINEL_INTERVAL (20s),
#        DS4_SENTINEL_TRAIN / DS4_SENTINEL_PARITY (corpus-liveness dirs)
#
# Why this exists as a file instead of an inline loop: four defects were found in
# the inline versions within one hour, all of them the kind that make a watcher
# silently useless rather than visibly broken.
#   1. `pgrep -f "port 8001"` matches this script's own command line, so the
#      engine-death check was vacuous - and a verification run once read the
#      watcher's environment instead of the engine's and reported a bogus argv
#      DIFF.  Liveness now comes from the listening socket.
#   2. Baselines captured once go stale at every ds4-server.sh start, because it
#      rotates log/ds4.log: the new file's counts sit BELOW the old baseline, so
#      the watcher that alerted at 22:43 went deaf at 23:07 - exactly when a
#      deploy makes watching most valuable.  Re-derived whenever the log shrinks.
#   3. `grep -c ... || echo 0` captures "0\n0", because grep prints 0 AND exits
#      nonzero on no match.  Every `[ "$n" -gt "$base" ]` then died with "integer
#      expression expected" and evaluated false: all five signals unread.  Found
#      because the baseline echo printed doubled numbers - print what you compare.
#   4. DS4_SENTINEL_INTERVAL was declared and never used (sleep had a literal 20),
#      so the fast test I ran to prove the alert path was actually running at the
#      default period, and I mis-attribute the measured 21 s latency to lsof cost.
#
# Alerts are read-only (grep on a log, lsof on a socket, wc on a file).  This
# never signals the engine and never writes outside its own stdout.
#
# Prove a fresh copy before trusting its silence:
#   printf 'x generation rewind rebuild ctx=1..2:1 y\n' > /tmp/s.log   # after armed
#   sh .codebase-memory/rewind_sentinel.sh 1 /tmp/s.log                # must alert
# Appending a rebuild line to a watched log alerts and exits 1; truncating a
# 10-line log to 1 prints "log rotated ... re-baselined" and stays clean.

MODE=${1:-120}
case "$MODE" in
	status) LOG=${2:-log/ds4.log} ;;
	*)      LOG=${2:-log/ds4.log} ;;
esac

PORT=${DS4_SENTINEL_PORT:-8001}
TRAIN_DIR=${DS4_SENTINEL_TRAIN:-/Volumes/FireCuda520/ds4-train/active}
PARITY_DIR=${DS4_SENTINEL_PARITY:-/Volumes/FireCuda520/ds4-parity}
INTERVAL=${DS4_SENTINEL_INTERVAL:-20}

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

# Baselines for the five failure signals, plus the counters that decide whether
# the fixes are being exercised at all (a silent engine and a fixed engine look
# identical until a rewind event happens).
base_rebuild=0; base_edge=0; base_discard=0; base_fail=0; base_div=0
prev_lines=0
set_baseline() {
	base_rebuild=$(cnt "generation rewind rebuild ctx=")
	base_edge=$(cnt "target-ahead-of-window")
	base_discard=$(cnt "kv cache discarded")
	base_fail=$(cnt "prefill failed")
	base_div=$(cnt_i "divergence")
}

# ---------------------------------------------------------------- report ------
# The part that answers "is it working?" from presence rather than absence.
edge_serves() {
	# Positive signature of ecef491: a truncation rewind whose target sits exactly
	# on the window's edge (truncate == window end) and is still served with
	# reason=ok.  Before that commit every such request logged a rebuild, so
	# counting these proves the fix from presence; "no rebuild lines" proves
	# nothing until the geometry recurs.  Validated both ways: 0 on the real
	# 21:12-23:07 boot log, 1 on a synthetic line built from the 22:43 fields.
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
		END { print k+0 }' "$LOG"
}

turn_times() {
	# The symptom the report was actually about: silent multi-minute freezes on
	# deep DSML tool turns.  finish= lines carry the whole turn's seconds, so the
	# distribution IS the user-visible outcome.  Reference points measured from the
	# pre-fix logs: 29.9% of deep turns over 64 s, 13.4% over 300 s.
	python3 - "$LOG" <<'PY'
import re, sys
pat = re.compile(r'\bfinish=\S+.*?([\d.]+)s\s*$')
dpat = re.compile(r'ctx=\d+\.\.(\d+)')
ts = []
for line in open(sys.argv[1], errors='ignore'):
    if 'finish=' not in line:
        continue
    m = pat.search(line)
    if not m:
        continue
    d = dpat.search(line)
    ts.append((float(m.group(1)), int(d.group(1)) if d else 0))
if not ts:
    print("turns completed: 0")
    sys.exit()
sec = sorted(t for t, _ in ts)
n = len(sec)
def pct(p):
    return sec[min(n - 1, int(n * p))]
print(f"turns completed: {n}   median {pct(0.5):.1f}s   p90 {pct(0.9):.1f}s"
      f"   p99 {pct(0.99):.1f}s   max {sec[-1]:.1f}s")
print(f"  >64s: {sum(1 for s, _ in ts if s > 64)} ({100.0 * sum(1 for s, _ in ts if s > 64) / n:.1f}%)"
      f"   >300s: {sum(1 for s, _ in ts if s > 300)}"
      f" ({100.0 * sum(1 for s, _ in ts if s > 300) / n:.1f}%)   deepest {max(d for _, d in ts)}")
PY
}

report() {
	echo "== rewind sentinel report $(date '+%H:%M:%S') on $LOG =="
	if [ "$1" = "clean" ]; then
		echo "watched ${MINUTES}m with no alert."
	else
		echo "(status snapshot, not an alert)"
	fi
	echo "engine: pid $(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null | head -1) listening on $PORT"
	echo "rewind events: $(cnt 'generation rewind')   armed: $(cnt 'end_state=armed')"
	echo "rebuilt (the stall): $(cnt 'generation rewind rebuild ctx=')"
	echo "edge-target serves (truncate == window end, reason=ok): $(edge_serves)"
	echo "edge refusals (must stay 0 post-ecef491): $(cnt 'target-ahead-of-window')"
	echo "resample=1 mode-boundary events: $(cnt 'resample=1')"
	echo "  ^ still 0 means the arming fix's decisive observation has not landed;"
	echo "    the model-free gate test (tests/test_session_state.c) covers that geometry."
	turn_times
	python3 .codebase-memory/stall_census.py "$LOG" 2>/dev/null | head -2
	echo "kv: hit=$(cnt 'kv cache hit') stored=$(cnt 'kv cache stored') evicted=$(cnt 'kv cache evicted')"
	echo "    discarded=$(cnt 'kv cache discarded')  (frontier-superseded evictions are normal"
	echo "    after a restart; they cost a future re-prefill, not correctness)"
	echo "prefill failed: $(cnt 'prefill failed')   divergence lines: $(cnt_i 'divergence')"

	# Collection liveness for the corpus lane.  A short health probe does NOT
	# produce a record (verified: the 23:08 probe left train/parity mtimes at the
	# previous boot), so "not writing yet" on an idle engine is not a fault - the
	# first qualifying real turn creates them.
	EPID=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null | head -1)
	if [ -n "$EPID" ]; then
		if ls "$TRAIN_DIR"/train_pid${EPID}.recs >/dev/null 2>&1; then
			echo "capture live: train_pid${EPID}.recs ($(ls -l "$TRAIN_DIR"/train_pid${EPID}.recs | awk '{print $5}') bytes)"
		else
			echo "capture not writing yet: train_pid${EPID}.recs (needs a qualifying real turn)"
		fi
		PAR=$(find "$PARITY_DIR" -maxdepth 2 -name "parity_pid${EPID}.bin" 2>/dev/null | head -1)
		if [ -n "$PAR" ]; then
			echo "parity live: $(basename "$PAR") in $(dirname "$PAR") ($(ls -l "$PAR" | awk '{print $5}') bytes)"
		else
			echo "parity not writing yet for pid $EPID (same rule)"
		fi
	else
		echo "engine not listening on $PORT - nothing to capture"
	fi
}

if [ "$MODE" = "status" ]; then
	report status
	exit 0
fi

# ---------------------------------------------------------------- watch ------
MINUTES=$MODE
END=$((SECONDS + MINUTES * 60))
set_baseline
echo "sentinel armed $(date '+%H:%M:%S') for ${MINUTES}m on $LOG (port $PORT, every ${INTERVAL}s)."
echo "baselines: rebuild=$base_rebuild edge=$base_edge discard=$base_discard"
echo "           prefill-fail=$base_fail divergence=$base_div"

# fire LABEL GREP-ARGS...  - prints the two most recent matching lines and exits 1.
fire() {
	label=$1; shift
	echo "ALERT: $label at $(date '+%H:%M:%S'):"
	grep "$@" "$LOG" 2>/dev/null | tail -2 | cut -c1-260
	exit 1
}

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
	[ "$r" -gt "$base_rebuild" ] && fire "NEW REBUILD" "generation rewind rebuild"
	e=$(cnt "target-ahead-of-window")
	[ "$e" -gt "$base_edge" ] && fire "EDGE REFUSAL RECURRED (ecef491 missed a case)" "target-ahead-of-window"
	d=$(cnt "kv cache discarded")
	[ "$d" -gt "$base_discard" ] && fire "KV DISCARDED (store integrity)" "kv cache discarded"
	f=$(cnt "prefill failed")
	[ "$f" -gt "$base_fail" ] && fire "PREFILL FAILED" "prefill failed"
	v=$(cnt_i "divergence")
	# -i plus the pattern, in that order: fire() shifts its label away and hands the
	# rest to grep, so passing only "-i" here would leave grep with no pattern and
	# it would block reading stdin - a watcher that hangs is a watcher that is deaf.
	[ "$v" -gt "$base_div" ] && fire "DIVERGENCE LINE" -i "divergence"
	sleep "$INTERVAL"
done

report clean
