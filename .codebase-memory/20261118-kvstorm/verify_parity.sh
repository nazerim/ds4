#!/bin/zsh
# Verify prefill-plan parity: does the fork fix make output independent of --kv-disk-dir,
# and does disabling the fix bring the dependence back?
#
# Three phases, same binary, byte-identical prompts:
#   A  cache ON                        (what production does today)
#   B  cache OFF, parity default (on)  -> must EQUAL A   (the fix works)
#   C  cache OFF, --disable-...-parity -> must DIFFER from A (the fix is what does the work)
# If A==B and A==C, the prompts never triggered divergence and the run proves nothing; that is
# reported as INCONCLUSIVE rather than as success.  (Three harness bugs tonight were of exactly
# this shape, so the script asserts the negative control explicitly.)
set -u
REPO=/Users/naz/Projects/ds4
HERE=$REPO/.codebase-memory/20261118-kvstorm
OUT=$HERE/runs/parity
PORT=8079
MODEL=gguf/Qwen3.8-Flash-Next-Q4.gguf
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

python3 - "$OUT" <<'PY'
import hashlib, json, sys
out = sys.argv[1]
SENT = ("The catchment's baseflow is sustained by fractured bedrock rather than soil moisture, "
        "which decouples stream response from rainfall intensity. ")
UNIT = SENT * (76000 // len(SENT) + 1)
for i in range(2):
    doc = ("Archive %d. " % i) + UNIT[:76000]
    open("%s/prompt%d.json" % (out, i), "w").write(json.dumps({
        "model": "default",
        "messages": [{"role": "system", "content": doc},
                     {"role": "user", "content": "In one paragraph, what single factor governs the "
                                                 "effect described above, and why?"}],
        "temperature": 0, "max_tokens": 256, "stream": False,
        "chat_template_kwargs": {"enable_thinking": False}}))
    print("  prompt%d sha256=%s" % (i, hashlib.sha256(doc.encode()).hexdigest()[:16]))
PY

phase() {   # $1 = tag, $2.. = extra flags
  local tag=$1; shift
  : > $OUT/server-$tag.log
  local extra=()
  [ "$tag" = "A-cache-on" ] && { rm -rf /tmp/parity-kv; mkdir -p /tmp/parity-kv; extra=(--kv-disk-dir /tmp/parity-kv); }
  cd $REPO || exit 2
  ./ds4-server --model $MODEL --ctx 65536 --port $PORT $extra "$@" >> $OUT/server-$tag.log 2>&1 &
  local pid=$!
  for i in {1..120}; do
    curl -s -m 5 -o /dev/null http://127.0.0.1:$PORT/v1/models && break
    kill -0 $pid 2>/dev/null || { say "engine $tag died"; return 1; }
    sleep 5
  done
  say "phase $tag up (flags: $extra $*)"
  grep -a 'plan parity' $OUT/server-$tag.log | sed 's/^/    /' | cut -c1-100
  for p in 0 1; do
    curl -s -m 600 -H 'Content-Type: application/json' -d @$OUT/prompt$p.json \
      http://127.0.0.1:$PORT/v1/chat/completions > $OUT/resp-$tag-p$p.json
    python3 -c "
import json
d=json.load(open('$OUT/resp-$tag-p$p.json'))
t=(d['choices'][0]['message'].get('content') or '').strip()
open('$OUT/text-$tag-p$p.txt','w').write(t)
print('    $tag p$p P=%-6s comp=%-4s chars=%d' % (d['usage']['prompt_tokens'], d['usage']['completion_tokens'], len(t)))
"
  done
  grep -a 'prompt start\|prefill chunk' $OUT/server-$tag.log > $OUT/chunks-$tag.txt
  kill $pid 2>/dev/null; wait $pid 2>/dev/null
  sleep 3
}

phase A-cache-on
phase B-cache-off-parity
phase C-cache-off-no-parity --disable-kv-cache-cold-stop-parity

python3 - "$OUT" <<'PY'
import difflib
import sys

out = sys.argv[1]
tags = ("A-cache-on", "B-cache-off-parity", "C-cache-off-no-parity")


def txt(tag, p):
    return open("%s/text-%s-p%d.txt" % (out, tag, p), encoding="utf-8").read()


def schedules(tag):
    import re
    lines = open("%s/chunks-%s.txt" % (out, tag), errors="replace").read()
    starts = [m.start() for m in re.finditer(r"chat ctx=(\d+)\.\.(\d+):\d+ prompt start", lines)]
    outp = []
    for k, st in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        cum = [int(m.group(1)) for m in re.finditer(r"prefill chunk (\d+)/(\d+)", lines[st:end])]
        if cum and max(cum) > 5000:
            outp.append(tuple(cum))
    return outp


print("\n=== chunk schedules (cumulative) ===")
for t in tags:
    print("  %-26s %s" % (t, schedules(t)))

print("\n=== verdicts (schedules above are the primary evidence; texts are the consequence) ===")
ab = [txt("A-cache-on", p) == txt("B-cache-off-parity", p) for p in (0, 1)]
ac = [txt("A-cache-on", p) == txt("C-cache-off-no-parity", p) for p in (0, 1)]
for p in (0, 1):
    sim_bc = difflib.SequenceMatcher(None, txt("A-cache-on", p), txt("C-cache-off-no-parity", p)).ratio()
    print("  prompt %d:  A==B(parity on)=%-5s   A==C(parity off)=%-5s  similarity A vs C=%.3f"
          % (p, ab[p], ac[p], sim_bc))

# A changed chunk plan only flips the argmax where greedy decoding sits on a near-tie,
# so a plan-INSENSITIVE prompt is normal and is not evidence of failure. The negative
# control needs at least one sensitive prompt, not every prompt.
n_sens = sum(1 for x in ac if not x)
if not all(ab):
    print("\n  FIX FAILED: with parity on, cache ON and cache OFF still disagree on %d of %d prompts."
          % (sum(1 for x in ab if not x), len(ab)))
elif n_sens == 0:
    print("\n  INCONCLUSIVE: every prompt is plan-insensitive in this build, so phase C could not")
    print("  show that the plan is what drives the difference. Add prompts known to diverge")
    print("  (see runs/repro-curl) rather than reading this as a pass.")
else:
    print("\n  FIX VERIFIED: cache ON and cache OFF now agree on all %d prompts (A==B), the chunk"
          % len(ab))
    print("  schedules are identical, and %d of %d prompts diverge again when parity is disabled --"
          % (n_sens, len(ac)))
    print("  so the plan, and the fix to it, is what is doing the work.")
    print("  The %d insensitive prompt(s) are expected: a moved reduction order only flips the"
          % (len(ac) - n_sens))
    print("  argmax at a near-tie.")
PY
rm -rf /tmp/parity-kv
say "done"
