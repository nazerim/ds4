import json, urllib.request

d = json.load(open("/Users/naz/Projects/ds4/.codebase-memory/baseline-thinking-20261102/dbwal-val-conversation.json"))
msgs = [{"role": "user", "content": d["user_prompt"]["text"]}]
cid = 0
for m in d["messages"]:
    reasoning = "".join(p.get("text", "") for p in m["parts"] if p.get("kind") == "reasoning")
    text = "".join(p.get("text", "") for p in m["parts"] if p.get("kind") == "text")
    tools = [p for p in m["parts"] if p.get("kind") == "tool"]
    a = {"role": "assistant"}
    if reasoning:
        a["reasoning_content"] = reasoning
    if text:
        a["content"] = text
    if tools:
        tcs = []
        for t in tools:
            cid += 1
            tcs.append({"id": "call_r%d" % cid, "type": "function", "function": {
                "name": t.get("tool", "shell"),
                "arguments": json.dumps(t.get("input", {}))[:600]}})
            msgs.append({"role": "tool", "tool_call_id": "call_r%d" % cid,
                         "content": (t.get("output") or "")[:400]})
        a["tool_calls"] = tcs
    if not reasoning and not text and not tools:
        continue
    # place assistant before its tool results: pop trailing tool msgs and re-append
    tail = []
    while msgs and msgs[-1]["role"] == "tool":
        tail.insert(0, msgs.pop())
    msgs.append(a)
    msgs.extend(tail)

msgs.append({"role": "user", "content": "Continue working the task from where you left off. Finish it now."})
body = {
    "model": "deepseek-v4-flash",
    "messages": msgs,
    "max_tokens": 6000,
    "temperature": 1.0,
    "top_p": 0.95,
    "thinking_tripwire": True,
    "tripwire_min_tokens": 600,
    "reasoning_budget_tokens": 0,
    "stream": False,
}
print("replay messages:", len(msgs), "approx chars:", sum(len(json.dumps(m)) for m in msgs))
req = urllib.request.Request("http://127.0.0.1:8001/v1/chat/completions",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
r = json.load(urllib.request.urlopen(req, timeout=1800))
c = r["choices"][0]; m = c["message"]; u = r["usage"]
print("replay: finish=%s completion=%s think=%d answer=%d details=%s" % (
    c["finish_reason"], u["completion_tokens"],
    len(m.get("reasoning_content") or ""), len(m.get("content") or ""),
    json.dumps(u.get("completion_tokens_details", "ABSENT"))))
print(" tail:", repr((m.get("reasoning_content") or "")[-90:]))
