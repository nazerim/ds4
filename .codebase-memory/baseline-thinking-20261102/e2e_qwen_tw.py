import json, urllib.request

LOOP = ('In your reasoning, before answering, write out the following sentence ONE HUNDRED TWENTY times, '
        'one sentence per line, EXACTLY identical every time, with no numbering, no comments, no variation, '
        'nothing else at all in between:\n'
        'The quick brown fox jumps over the lazy dog while the careful farmer counts seven red sheep.\n'
        'After the hundred twenty repetitions, state how many lines you wrote.')
TOOLS = [{'type': 'function', 'function': {
    'name': 'save_note', 'description': 'Save a note',
    'parameters': {'type': 'object', 'properties': {
        'lines_written': {'type': 'integer'}}, 'required': ['lines_written']}}}]

def run(tag, body):
    req = urllib.request.Request('http://127.0.0.1:8002/v1/chat/completions',
                                 data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    r = json.load(urllib.request.urlopen(req, timeout=900))
    c = r['choices'][0]; m = c['message']; u = r['usage']
    tcs = m.get('tool_calls') or []
    print('%-18s finish=%-10s completion=%-5s think=%-6d answer=%-6d details=%s' % (
        tag, c['finish_reason'], u['completion_tokens'],
        len(m.get('reasoning_content') or ''), len(m.get('content') or ''),
        json.dumps(u.get('completion_tokens_details', 'ABSENT'))))
    print('   tail:', repr((m.get('reasoning_content') or '')[-70:]))
    for t in tcs:
        try:
            json.loads(t['function']['arguments'])
            print('   tool-OK:', t['function']['name'], t['function']['arguments'])
        except Exception as e:
            print('   tool-BAD:', repr(t['function']['arguments'][:120]), e)

base = {'model': 'qwen3.8-flash-next',
        'messages': [{'role': 'user', 'content': LOOP}],
        'max_tokens': 4000, 'temperature': 0}
run('qwen-tw-on', dict(base, thinking_tripwire=True, tripwire_min_tokens=600))
run('qwen-tw-off', dict(base, thinking_tripwire=False))
twe = dict(base, tools=TOOLS, thinking_tripwire=True, tripwire_min_tokens=600,
           messages=[{'role': 'user', 'content': LOOP +
                      ' Then call save_note with lines_written.'}]
           )
twe.pop('max_tokens'); twe['max_tokens'] = 4000
run('qwen-tools-tw', twe)
