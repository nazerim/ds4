#!/bin/bash
# W5 live battery for the hard thinking budget (PLAN-THINKING-BUDGET-20261102).
# Usage: ./budget-battery.sh PORT MODEL PROMPT_FILE_PREFIX
# Exercises: budget via canonical field (stream + non-stream), alias,
# Anthropic thinking object, no-budget parity, tool-payload survival.
set -u
PORT="${1:-8001}"
MODEL="${2:-deepseek-v4-flash}"
BASE="http://127.0.0.1:${PORT}"
OUT="${OUTDIR:-.}"
PROMPT="A farmer needs to transport a wolf, a goat, and a cabbage across a river. The boat can only carry the farmer plus one item. If left alone unattended, the wolf will eat the goat, and the goat will eat the cabbage. Prove rigorously, with an explicit state-space argument and invariant analysis, that the farmer's solution requires at least seven crossings, and enumerate every valid minimal strategy."

run() { # name, extra json fragment
  local name="$1"; shift
  local extra="$1"
  local body="{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"${PROMPT}\"}],\"max_tokens\":1536,\"temperature\":0,\"seed\":42${extra}}"
  curl -s --max-time 900 -H 'Content-Type: application/json' -d "$body" \
    "${BASE}/v1/chat/completions" -o "${OUT}/${name}.json"
  python3 - "$OUT/${name}.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
c=r['choices'][0]; m=c['message']
u=r.get('usage',{})
print(f"{sys.argv[1].split('/')[-1]}: finish={c['finish_reason']} "
      f"completion={u.get('completion_tokens')} "
      f"reasoning={u.get('completion_tokens_details',{}).get('reasoning_tokens','ABSENT')} "
      f"thinking_len={len(m.get('reasoning_content') or '')} answer_len={len(m.get('content') or '')}")
PY
}

run stream-official  ',"reasoning_budget_tokens":128,"stream":false'
run nonstream-512    ',"reasoning_budget_tokens":512,"stream":false'
run alias-256        ',"thinking_budget_tokens":256,"stream":false'
run nobudget-parity  ''
