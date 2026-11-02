#!/bin/bash
# W5b: streaming + Anthropic-route + tool-survival budget tests.
set -u
PORT="${1:-8001}"
MODEL="${2:-deepseek-v4-flash}"
BASE="http://127.0.0.1:${PORT}"
OUT="${OUTDIR:-.}"
PROMPT="Prove rigorously, with explicit state-space argument and invariant analysis: the wolf-goat-cabbage river crossing needs at least seven boat trips. Enumerate every minimal strategy."

# 1) Streaming, canonical field, usage in final chunk.
curl -sN --max-time 900 -H 'Content-Type: application/json' \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"${PROMPT}\"}],\"max_tokens\":1536,\"temperature\":0,\"seed\":42,\"reasoning_budget_tokens\":128,\"stream\":true,\"stream_options\":{\"include_usage\":true}}" \
  "${BASE}/v1/chat/completions" > "${OUT}/stream-budget128.sse"
echo "== stream-budget128: $(grep -c 'data:' "${OUT}/stream-budget128.sse") SSE lines"
grep -o '"reasoning_tokens":[0-9]*' "${OUT}/stream-budget128.sse" | tail -1
grep -c 'think' "${OUT}/stream-budget128.sse" | xargs -I{} echo "delta-lines-mentioning-think-tag: {}"

# 2) Anthropic /v1/messages with native thinking object.
curl -s --max-time 900 -H 'Content-Type: application/json' -H 'anthropic-version: 2023-06-01' \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"${PROMPT}\"}],\"max_tokens\":1536,\"thinking\":{\"type\":\"enabled\",\"budget_tokens\":128}}" \
  "${BASE}/v1/messages" > "${OUT}/anthropic-budget128.json"
python3 - "${OUT}/anthropic-budget128.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
blocks=r.get('content',[])
kinds=[b.get('type') for b in blocks]
thinking=[b for b in blocks if b.get('type')=='thinking']
text=[b for b in blocks if b.get('type')=='text']
print("anthropic: stop_reason=%s blocks=%s thinking_chars=%d text_chars=%d usage=%s" % (
    r.get('stop_reason'), kinds,
    sum(len(t.get('thinking','')) for t in thinking),
    sum(len(t.get('text','')) for t in text),
    r.get('usage')))
if thinking:
    print("  thinking tail:", repr(thinking[0]['thinking'][-80:]))
PY

# 3) Tool-heavy survival: budget mid-think must not corrupt tool payloads.
curl -s --max-time 900 -H 'Content-Type: application/json' \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"What is the weather in Paris and Tokyo right now? You must use the tools.\"}],\"max_tokens\":1536,\"temperature\":0,\"reasoning_budget_tokens\":64,\"tools\":[{\"type\":\"function\",\"function\":{\"name\":\"get_weather\",\"description\":\"Current weather for a city\",\"parameters\":{\"type\":\"object\",\"properties\":{\"city\":{\"type\":\"string\"}},\"required\":[\"city\"]}}},{\"type\":\"function\",\"function\":{\"name\":\"get_forecast\",\"description\":\"Multi-day forecast\",\"parameters\":{\"type\":\"object\",\"properties\":{\"city\":{\"type\":\"string\"},\"days\":{\"type\":\"integer\"}},\"required\":[\"city\"]}}}]}" \
  "${BASE}/v1/chat/completions" > "${OUT}/tools-budget64.json"
python3 - "${OUT}/tools-budget64.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
c=r['choices'][0]; m=c['message']
tcs=m.get('tool_calls') or []
ok=True
for t in tcs:
    try: json.loads(t['function']['arguments']); print("tool:",t['function']['name'],t['function']['arguments'])
    except Exception as e: ok=False; print("BAD-ARGS:",t['function']['arguments'][:120],e)
print("tools: finish=%s n_tool_calls=%d args_json_ok=%s thinking_len=%d usage=%s" % (
    c['finish_reason'], len(tcs), ok, len(m.get('reasoning_content') or ''),
    r.get('usage',{}).get('completion_tokens_details')))
PY
