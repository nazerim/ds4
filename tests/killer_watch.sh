#!/bin/zsh
# Watch the running ds4-server PID; capture full process state the moment it
# exits. Used to identify the external "shutdown requested" killer (3 events
# Sep 4-6, source unknown). Run detached: `./killer_watch.sh &!`.
# Replaces /tmp/killer-watch.sh (/tmp got wiped once; that may be the killer's
# doing or just the macOS cleaner - either way, keep tools out of /tmp).
REPO=/Users/naz/Projects/ds4
PID=$(cat $REPO/ds4-server.pid 2>/dev/null)
[ -z "$PID" ] && { echo "no pidfile"; exit 1; }
LOG=/tmp/killer-watch.log
echo "watching PID $PID" >>$LOG
while kill -0 $PID 2>/dev/null; do sleep 1; done
{
  echo "=== $PID died at $(date '+%Y-%m-%d %H:%M:%S') ==="
  tail -5 $REPO/log/ds4.log
  echo "--- ds4 processes still alive:"
  ps -A -o pid,lstart,command | grep -i "ds4-server\|ds4 " | grep -v grep
  echo "--- who could have sent it (launchd/cron/sh):"
  ps -A -o pid,ppid,lstart,command | grep -iE "launchd|cron|osascript" | grep -v grep | head -10
} >>$LOG 2>&1
