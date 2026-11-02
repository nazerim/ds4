import json, sys
# usage: e2e_tripwire.py BASE MODEL MODE
# MODE: tw-on | tw-off | parity | tools
base, model, mode = sys.argv[1], sys.argv[2], sys.argv[3]
LOOP = ("Before giving the final answer, verify the total of 13+27+8+41+19+6+33+24 "
        "by writing out the running-sum enumeration, then REPEAT THE EXACT SAME "
        "verification twenty times, each time re-deriving every step identically "
        "from 13 onward, to make sure no arithmetic slip occurred. Only after the "
        "repetitions, state the total.")
body = {
    "model": model,
    "messages": [{"role": "user", "content": LOOP}],
    "max_tokens": 6000,
    "temperature": 0,
    "seed": 11,
}
if mode == "tw-on":
    body["thinking_tripwire"] = True
    body["reasoning_budget_tokens"] = 0
elif mode == "tw-off":
    body["thinking_tripwire"] = False
    body["reasoning_budget_tokens"] = 0
elif mode == "tools":
    body["thinking_tripwire"] = True
    body["messages"] = [{"role": "user", "content":
        ("Plan a three-day Kyoto itinerary. Before calling the save_plan tool, "
         "verify day-by-day that no attraction is repeated or missing by writing "
         "the full schedule check, then REPEAT THE EXACT SAME verification "
         "twenty times identically before calling the tool.")}]
    body["tools"] = [{"type": "function", "function": {
        "name": "save_plan", "description": "Save the itinerary",
        "parameters": {"type": "object", "properties": {
            "days": {"type": "array", "items": {"type": "string"}},
            "verified_repeats": {"type": "integer"}},
            "required": ["days"]}}}]
elif mode == "parity":
    body = json.load(open("/Users/naz/Projects/ds4/.codebase-memory/"
                          "baseline-thinking-20261102/req-baseline.json"))
import urllib.request
req = urllib.request.Request(base + "/v1/chat/completions",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
r = json.load(urllib.request.urlopen(req, timeout=900))
c = r["choices"][0]; m = c["message"]; u = r.get("usage", {})
det = u.get("completion_tokens_details", {})
tag = {"parity": "PARITY"}
print("%-8s finish=%-10s completion=%-5s think_chars=%-7d answer_chars=%-6d details=%s" % (
    mode, c["finish_reason"], u.get("completion_tokens"),
    len(m.get("reasoning_content") or ""), len(m.get("content") or ""),
    json.dumps(det) if det else "ABSENT"))
if mode == "parity":
    ref = json.load(open("/Users/naz/Projects/ds4/.codebase-memory/"
                         "baseline-thinking-20261102/resp-baseline.json"))
    print("  byte-identical-to-baseline:", m == ref["choices"][0]["message"])
if mode == "tools":
    tcs = m.get("tool_calls") or []
    for t in tcs:
        try:
            json.loads(t["function"]["arguments"])
            print("  tool-OK:", t["function"]["name"], t["function"]["arguments"][:90])
        except Exception as e:
            print("  TOOL-BAD:", repr(t["function"]["arguments"][:120]), e)
    print("  tools: n=%d ok=%s" % (len(tcs), all(isinstance(t, dict) for t in tcs)))
print("  reasoning tail:", repr((m.get("reasoning_content") or "")[-70:]))
