import json, time, urllib.request

URL = "http://127.0.0.1:8002/v1/chat/completions"

def post(messages, max_tokens=40):
    body = json.dumps({"model": "qwen3.8-flash-next", "messages": messages,
                       "max_tokens": max_tokens, "temperature": 0,
                       "stream": False}).encode()
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        d = json.loads(r.read())
    return d, time.time() - t0

def block(n, salt):
    return "\n".join(
        "Ref %05d-%s: gauge %s reads %.4f at node %d, tilt %+d, band %d."
        % (i, salt, "KLMNOP"[i % 6], (i * 0.9173 + salt.__hash__() % 7) % 100,
           i % 512, (i % 41) - 20, (i * 7) % 23)
        for i in range(n))

SYS = ("You are an archival assistant. The reference block is ground truth. "
       "Answer in one short sentence.\n\n")

# Deterministic content and fixed assistant turns, so the rendered prompt prefix
# replays byte-exactly across requests and each turn leaves the live frontier at
# an arbitrary (off-grid) length. That is the case FIX 1 changes: pre-fix a rung
# crossing there stored the snapped-down boundary with trimmed != 0 (guaranteed
# unloadable); post-fix it stores the live length with trimmed == 0.
msgs = [{"role": "system", "content": SYS},
        {"role": "user", "content": block(1500, "alpha") +
         "\n\nWhat band is listed for Ref 00777-alpha?"}]

for turn in range(1, 5):
    d, el = post(msgs)
    u = d.get("usage", {})
    pt = u.get("prompt_tokens", 0)
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    print("turn %d: %.1fs prompt=%d cached=%d delta=%d finish=%s"
          % (turn, el, pt, cached, pt - cached,
             d["choices"][0].get("finish_reason")), flush=True)
    ans = (d["choices"][0]["message"].get("content") or "").strip()
    print("         reused %.1f%% of the prompt; answer=%r"
          % (100.0 * cached / pt if pt else 0.0, ans[:70]), flush=True)
    msgs.append({"role": "assistant", "content": "Band %d." % (777 * turn % 23)})
    msgs.append({"role": "user",
                 "content": block(311, "t%d" % turn) +
                            "\n\nAnd for Ref 00311-t%d?" % turn})
