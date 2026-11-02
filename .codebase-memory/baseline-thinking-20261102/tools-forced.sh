#!/bin/bash
# Forced-budget + tool-call survival: long exhaustive prompt that wants a
# tool call, budget 96 fires mid-think, tool payload must still be valid.
set -u
curl -s --max-time 900 -H 'Content-Type: application/json' -d '{
  "model":"deepseek-v4-flash",
  "messages":[{"role":"user","content":"Before answering the wolf-goat-cabbage puzzle via the submit_answer tool, reason exhaustively: enumerate the full state space (27 states), all transitions, prove the lower bound by invariant analysis, and list every minimal path. You MUST call submit_answer with the seven-step solution and the count of minimal strategies."}],
  "max_tokens":1024,"temperature":0,"reasoning_budget_tokens":96,
  "tools":[{"type":"function","function":{"name":"submit_answer","description":"Submit the final solution","parameters":{"type":"object","properties":{"steps":{"type":"array","items":{"type":"string"},"description":"The seven crossings in order"},"num_minimal_strategies":{"type":"integer"}},"required":["steps","num_minimal_strategies"]}}}
]
}' http://127.0.0.1:8001/v1/chat/completions -o "$(dirname "$0")/tools-forced96.json"
python3 - "$(dirname "$0")/tools-forced96.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
c=r['choices'][0]; m=c['message']; u=r.get('usage',{})
tcs=m.get('tool_calls') or []
print("finish=%s n_tool_calls=%d reasoning_len=%d usage_details=%s" % (
    c['finish_reason'], len(tcs), len(m.get('reasoning_content') or ''),
    u.get('completion_tokens_details')))
for t in tcs:
    try:
        args=json.loads(t['function']['arguments'])
        print("tool:",t['function']['name'],"keys=",sorted(args.keys()),
              "steps=",len(args.get('steps',[])),"n=",args.get('num_minimal_strategies'))
    except Exception as e:
        print("BAD-ARGS:",repr(t['function']['arguments'][:200]),e)
print("reasoning tail:",repr((m.get('reasoning_content') or '')[-70:]))
PY
