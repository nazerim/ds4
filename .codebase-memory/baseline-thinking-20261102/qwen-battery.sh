#!/bin/bash
# W6 Qwen3.8 battery (server booted with DS4_THINKING_BUDGET_TOKENS=256).
# Cases: env-default enforcement, request-over-env, explicit 0 = off,
# alias value, forced-boundary tools, Anthropic native object, streaming usage.
set -u
BASE="http://127.0.0.1:8002"
M="qwen3.8-flash-next"
OUT="$(dirname "$0")"
P="Prove rigorously, with explicit state-space argument and invariant analysis, that the wolf-goat-cabbage river crossing needs at least seven boat trips. Enumerate every minimal strategy. Reason exhaustively before answering."

sum() { python3 - "$1" "${2:-}" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
c=r['choices'][0]; m=c['message']; u=r.get('usage',{})
det=u.get('completion_tokens_details',{})
print("%-22s finish=%-10s completion=%-5s reasoning_tokens=%-6s think_chars=%-6d answer_chars=%d" % (
    sys.argv[2] if len(sys.argv)>2 and sys.argv[2] else sys.argv[1].split('/')[-1],
    c['finish_reason'], u.get('completion_tokens'),
    det.get('reasoning_tokens','ABSENT'),
    len(m.get('reasoning_content') or ''), len(m.get('content') or '')))
tail=(m.get('reasoning_content') or '')[-56:]
print("   reasoning tail:",repr(tail))
if m.get('tool_calls'):
    for t in m['tool_calls']:
        try: json.loads(t['function']['arguments']); print("   tool ok:",t['function']['name'])
        except Exception as e: print("   TOOL-BAD:",repr(t['function']['arguments'][:120]),e)
PY
}

req() { # name, extra
  curl -s --max-time 900 -H 'Content-Type: application/json' \
    -d "{\"model\":\"${M}\",\"messages\":[{\"role\":\"user\",\"content\":\"${P}\"}],\"max_tokens\":1200,\"temperature\":0,\"seed\":7${2}}" \
    "${BASE}/v1/chat/completions" -o "${OUT}/${1}.json"
  sum "${OUT}/${1}.json" "${1}"
}

req qwen-env-default   ''
req qwen-req-128       ',"reasoning_budget_tokens":128'
req qwen-off-0         ',"reasoning_budget_tokens":0'
req qwen-alias-64      ',"thinking_budget_tokens":64'
# streaming case (SSE, inspect tail directly)
curl -sN --max-time 900 -H 'Content-Type: application/json' \
  -d "{\"model\":\"${M}\",\"messages\":[{\"role\":\"user\",\"content\":\"${P}\"}],\"max_tokens\":1200,\"temperature\":0,\"seed\":7,\"reasoning_budget_tokens\":128,\"stream\":true,\"stream_options\":{\"include_usage\":true}}" \
  "${BASE}/v1/chat/completions" > "${OUT}/qwen-stream-128.sse"
echo "== qwen-stream-128: $(grep -c 'data:' "${OUT}/qwen-stream-128.sse") SSE lines; usage tail:"
tail -2 "${OUT}/qwen-stream-128.sse" | head -1 | cut -c1-260
grep -c '"delta":{"reasoning_content"' "${OUT}/qwen-stream-128.sse" | xargs -I{} echo "reasoning deltas: {}"

# forced boundary + tools
curl -s --max-time 900 -H 'Content-Type: application/json' -d '{
  "model":"qwen3.8-flash-next",
  "messages":[{"role":"user","content":"Before answering the wolf-goat-cabbage puzzle via the submit_answer tool, reason exhaustively about the full state space and lower bound. You MUST call submit_answer."}],
  "max_tokens":1024,"temperature":0,"reasoning_budget_tokens":96,
  "tools":[{"type":"function","function":{"name":"submit_answer","description":"Submit final solution","parameters":{"type":"object","properties":{"steps":{"type":"array","items":{"type":"string"}},"num_minimal_strategies":{"type":"integer"}},"required":["steps","num_minimal_strategies"]}}}]
}' "${BASE}/v1/chat/completions" -o "${OUT}/qwen-tools-forced96.json"
sum "${OUT}/qwen-tools-forced96.json" "qwen-tools-forced96"

# Anthropic native thinking object
curl -s --max-time 900 -H 'Content-Type: application/json' -H 'anthropic-version: 2023-06-01' -d '{
  "model":"qwen3.8-flash-next",
  "messages":[{"role":"user","content":"Prove the wolf-goat-cabbage crossing needs at least seven trips. Reason exhaustively first."}],
  "max_tokens":1024,"thinking":{"type":"enabled","budget_tokens":128}
}' "${BASE}/v1/messages" -o "${OUT}/qwen-anthropic-128.json"
python3 - "${OUT}/qwen-anthropic-128.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
b=r.get('content',[])
th=[x for x in b if x.get('type')=='thinking']; tx=[x for x in b if x.get('type')=='text']
print("qwen-anthropic: stop=%s thinking_chars=%d text_chars=%d tail=%s" % (
    r.get('stop_reason'), sum(len(t.get('thinking','')) for t in th),
    sum(len(t.get('text','')) for t in tx),
    repr((th[0]['thinking'][-56:]) if th else '')))
PY
