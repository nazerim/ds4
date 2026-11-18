#!/bin/zsh
# Does the pre-fix binary produce the same cache-ON answers as the patched one?
# If yes, deploying the parity fix cannot change production output -- production always
# runs with --kv-disk-dir, so it is the enabled path, which this compares directly.
set -u
REPO=/Users/naz/Projects/ds4
OUT=$REPO/.codebase-memory/20261118-kvstorm/runs/parity-prefix
PORT=8079
MODEL=gguf/Qwen3.8-Flash-Next-Q4.gguf
OLD=/tmp/ds4-prefix/ds4-server
say() { echo "$(date '+%H:%M:%S') $*"; }

restore_prod() {
  pkill -f "ds4-server --model $MODEL --ctx 65536 --port $PORT" 2>/dev/null
  sleep 5
  cd $REPO
  ./ds4-server.sh start-qwen >> $OUT/runner.log 2>&1
  for i in {1..60}; do
    curl -s -m 5 -o /dev/null http://127.0.0.1:8002/v1/models && { say "prod restored"; return; }
    sleep 5
  done
  say "WARNING: prod did not answer within 5 min"
}
mkdir -p $OUT
trap restore_prod EXIT

cd $REPO || exit 2
say "stopping production (idle-verified before launch)"
./ds4-server.sh stop >> $OUT/runner.log 2>&1
sleep 4
unset DS4_API_KEY QWEN_API_KEY QWEN_SUBSCRIPTION_KEY

rm -rf /tmp/prefix-kv; mkdir -p /tmp/prefix-kv
cd /tmp/ds4-prefix || exit 2
./ds4-server --model $MODEL --ctx 65536 --port $PORT --kv-disk-dir /tmp/prefix-kv >> $OUT/server.log 2>&1 &
PID=$!
for i in {1..120}; do
  curl -s -m 5 -o /dev/null http://127.0.0.1:$PORT/v1/models && break
  kill -0 $PID 2>/dev/null || { say "pre-fix engine died"; exit 3; }
  sleep 5
done
say "pre-fix engine up (cache ON, fresh empty dir)"

for p in 0 1; do
  curl -s -m 600 -H 'Content-Type: application/json' -d @$REPO/.codebase-memory/20261118-kvstorm/runs/parity/prompt$p.json \
    http://127.0.0.1:$PORT/v1/chat/completions > $OUT/resp-p$p.json
  python3 -c "
import json
d=json.load(open('$OUT/resp-p$p.json'))
t=(d['choices'][0]['message'].get('content') or '').strip()
open('$OUT/text-p$p.txt','w').write(t)
print('    pre-fix p$p P=%-6s comp=%-4s chars=%d' % (d['usage']['prompt_tokens'], d['usage']['completion_tokens'], len(t)))
"
done
grep -a 'prompt start\|prefill chunk' $OUT/server.log > $OUT/chunks.txt
kill $PID 2>/dev/null; wait $PID 2>/dev/null
rm -rf /tmp/prefix-kv

python3 - "$REPO" <<'PY'
import hashlib
import re
import sys

repo = sys.argv[1]
new = repo + "/.codebase-memory/20261118-kvstorm/runs/parity"
old = repo + "/.codebase-memory/20261118-kvstorm/runs/parity-prefix"
print("\n=== cache ON: pre-fix binary vs patched binary (production configuration) ===")
allsame = True
for p in (0, 1):
    a = open("%s/text-A-cache-on-p%d.txt" % (new, p), encoding="utf-8").read()
    b = open("%s/text-p%d.txt" % (old, p), encoding="utf-8").read()
    ha = hashlib.sha256(a.encode()).hexdigest()[:16]
    hb = hashlib.sha256(b.encode()).hexdigest()[:16]
    allsame &= (a == b)
    print("  prompt %d: patched=%s  pre-fix=%s  ->  %s" % (p, ha, hb, "IDENTICAL" if a == b else "DIFFERENT"))

def sched(path):
    txt = open(path, errors="replace").read()
    starts = [m.start() for m in re.finditer(r"chat ctx=(\d+)\.\.(\d+):\d+ prompt start", txt)]
    out = []
    for k, st in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(txt)
        cum = [int(m.group(1)) for m in re.finditer(r"prefill chunk (\d+)/(\d+)", txt[st:end])]
        if cum and max(cum) > 5000:
            out.append(tuple(cum))
    return out

s_old = sched("%s/chunks.txt" % old)
s_new = sched("%s/chunks-A-cache-on.txt" % new)
print("  schedules  pre-fix: %s" % (s_old,))
print("               patched: %s" % (s_new,))
print("  identical plans:", s_old == s_new)
print("\n  => %s" % ("Cache-ON behaviour is byte-for-byte unchanged by the fix: production output cannot move."
                    if allsame and s_old == s_new else
                    "Cache-ON behaviour CHANGED -- investigate before deploying; the enabled path was "
                    "meant to be untouched."))
PY
say "done"
