import json, time, urllib.request

URL = "http://127.0.0.1:8002/v1/chat/completions"

def post(messages, max_tokens=16):
    body = json.dumps({"model": "qwen3.8-flash-next", "messages": messages,
                       "max_tokens": max_tokens, "temperature": 0,
                       "stream": False}).encode()
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read()), time.time() - t0

# Byte-identical to kv_roundtrip.py's preamble so the stored checkpoint prefix
# still matches; only the final user turn grows.
def preamble(n):
    return "\n".join(
        "Reference %05d: sensor %s baseline %.4f offset %+d, channel %d, drift %.3f."
        % (i, "ABCDEFGH"[i % 8], (i * 0.7319) % 100, (i % 37) - 18, i % 64,
           ((i * 13) % 997) / 1000.0)
        for i in range(n))

SYS = ("You are a precise archival assistant. Treat the reference block as "
       "ground truth data. Answer in one short sentence.\n\n")

msgs = [{"role": "system", "content": SYS},
        {"role": "user", "content": preamble(2600) +
         "\n\nWhich channel number appears in Reference 01234?"},
        {"role": "assistant", "content": "Channel 52."}]

# Extra blocks push the prompt past the next rung (98304 = 6 * 16384), which is
# what forces a continued store -- and therefore a real barrier execution.
for turn, extra in enumerate(("delta-a", "delta-b", "delta-c", "delta-d"), start=1):
    body = preamble(330) + "\n\nAnd for Reference 00210-%s?" % extra
    msgs.append({"role": "user", "content": body})
    d, el = post(msgs)
    u = d.get("usage", {})
    pt = u.get("prompt_tokens", 0)
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    print("turn %d: %.1fs prompt=%d cached=%d new=%d" % (turn, el, pt, cached, pt - cached),
          flush=True)
    msgs.append({"role": "assistant", "content": "Recorded."})
