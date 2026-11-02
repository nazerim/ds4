import json, sys
b = {
    'model': 'qwen3.8-flash-next',
    'messages': [{'role': 'user', 'content':
        'Before answering the wolf-goat-cabbage puzzle via the submit_answer '
        'tool, reason about the full state space and the lower bound. You MUST '
        'call submit_answer with steps and num_minimal_strategies.'}],
    'max_tokens': 1024,
    'temperature': 0,
    'tools': [{
        'type': 'function',
        'function': {
            'name': 'submit_answer',
            'description': 'Submit final solution',
            'parameters': {
                'type': 'object',
                'properties': {
                    'steps': {'type': 'array', 'items': {'type': 'string'}},
                    'num_minimal_strategies': {'type': 'integer'},
                },
                'required': ['steps', 'num_minimal_strategies'],
            },
        },
    }],
}
if sys.argv[1] != 'none':
    b['reasoning_budget_tokens'] = int(sys.argv[1])
print(json.dumps(b))
