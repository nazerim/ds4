#!/bin/bash
# Qwen tool diagnostics: control (explicit 0 = off, beats env 256),
# forced at 96 and 160.
set -u
BASE="http://127.0.0.1:8002"
OUT="$(dirname "$0")"
report() { python3 - "$1" "$2" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
c=r['choices'][0]; m=c['message']; u=r.get('usage',{})
tcs=m.get('tool_calls') or []
det=u.get('completion_tokens_details',{})
print("%-20s finish=%-10s reasoning_tokens=%-7s think_chars=%-6d answer_chars=%-6d tools=%d" % (
    sys.argv[2], c['finish_reason'], det.get('reasoning_tokens','ABSENT'),
    len(m.get('reasoning_content') or ''), len(m.get('content') or ''), len(tcs)))
for t in tcs:
    try: json.loads(t['function']['arguments']); print("   tool-OK:",t['function']['name'],t['function']['arguments'][:100])
    except Exception: print("   tool-BAD:",repr(t['function']['arguments'][:140]))
if not tcs:
    print("   content tail:",repr((m.get('content') or '')[-100:]))
    print("   reasoning tail:",repr((m.get('reasoning_content') or '')[-100:]))
PY
}
for spec in "0:qtools-control" "96:qtools-b96" "160:qtools-b160"; do
  bud="${spec%%:*}"; name="${spec##*:}"
  curl -s --max-time 900 -H 'Content-Type: application/json' \
    -d "$(python3 "${OUT}/mkqtools.py" "$bud")" \
    "${BASE}/v1/chat/completions" -o "${OUT}/${name}.json"
  report "${OUT}/${name}.json" "${name}"
done
