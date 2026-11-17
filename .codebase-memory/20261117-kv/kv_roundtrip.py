import json, time, urllib.request

URL = "http://127.0.0.1:8002/v1/chat/completions"

def post(messages, max_tokens=24):
    body = json.dumps({
        "model": "qwen3.8-flash-next",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": False,
    }).encode()
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        d = json.loads(r.read())
    return d, time.time() - t0

# A long, deliberately non-repetitive preamble: unique numbered facts so the
# thinking tripwire and repetition loop guards have nothing to latch onto, and so
# the rendered prompt crosses many 8192-token rungs during prefill.
def preamble(n):
    return "\n".join(
        "Reference %05d: sensor %s baseline %.4f offset %+d, channel %d, drift %.3f."
        % (i, "ABCDEFGH"[i % 8], (i * 0.7319) % 100, (i % 37) - 18, i % 64,
           ((i * 13) % 997) / 1000.0)
        for i in range(n)
    )

sys_prompt = ("You are a precise archival assistant. Treat the reference block as "
              "ground truth data. Answer in one short sentence.\n\n")
block = preamble(2600)          # ~2600 lines, roughly 60-70k tokens

phase = 1
print("phase %d: long cold prefill (~%d chars of preamble)" % (phase, len(block)),
      flush=True)
msgs = [{"role": "system", "content": sys_prompt},
        {"role": "user", "content": block +
         "\n\nWhich channel number appears in Reference 01234?"}]
d, el = post(msgs)
u = d.get("usage", {})
print("  %.1fs  prompt_tokens=%s cached=%s completion=%s finish=%s"
      % (el, u.get("prompt_tokens"),
         (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
         u.get("completion_tokens"),
         d["choices"][0].get("finish_reason")), flush=True)
print("  answer: %r" % ((d["choices"][0]["message"].get("content") or "")[:90],),
      flush=True)

# Now extend the SAME conversation. The rendered prompt replays the whole
# prefix byte-exactly, so the disk cache should serve a rung checkpoint instead
# of re-prefilling from zero.
phase += 1
print("phase %d: extend the same conversation (should reuse a checkpoint)" % phase,
      flush=True)
msgs.append({"role": "assistant",
             "content": d["choices"][0]["message"].get("content") or "Channel 38."})
msgs.append({"role": "user",
             "content": "And in Reference 02101?"})
d2, el2 = post(msgs)
u2 = d2.get("usage", {})
print("  %.1fs  prompt_tokens=%s cached=%s completion=%s finish=%s"
      % (el2, u2.get("prompt_tokens"),
         (u2.get("prompt_tokens_details") or {}).get("cached_tokens"),
         u2.get("completion_tokens"),
         d2["choices"][0].get("finish_reason")), flush=True)
