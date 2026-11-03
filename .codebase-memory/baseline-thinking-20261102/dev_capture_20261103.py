"""Milestone-2 dev capture: scripted fresh conversations across bands.
Cold-start cycles (draft-cache depth 1) + deterministic temp0 for mirror
ground-truth pairing: .recs h-taps + parity proposals/positions by (pid,pos).
"""
import json, time, urllib.request, os
KEY=open(os.path.expanduser("~/.config/ds4/desktop.key")).read().strip()
BASE="http://127.0.0.1:8001/v1/chat/completions"
TOOLS=[{"type":"function","function":{"name":"lookup_weather","description":
    "Report current weather for a city.","parameters":{"type":"object",
    "properties":{"city":{"type":"string"}},"required":["city"]}}}]
def ask(messages, maxtok=150):
    body={"model":"ds4","messages":messages,"tools":TOOLS,"temperature":0,
          "max_tokens":maxtok,"stream":False}
    req=urllib.request.Request(BASE,data=json.dumps(body).encode(),
        headers={"Content-Type":"application/json","Authorization":f"Bearer {KEY}"})
    t0=time.time()
    with urllib.request.urlopen(req,timeout=1800) as r:
        d=json.loads(r.read())
    u=d.get("usage",{})
    print(f"  done {time.time()-t0:.1f}s prompt={u.get('prompt_tokens')} "
          f"completion={u.get('completion_tokens')} finish="
          f"{d['choices'][0]['finish_reason']}")
pad=lambda n: "\n".join(f"Item {i}: The quarterly telemetry report line {i} notes steady sensor calibration drift in sector {i%17} requiring routine review per protocol." for i in range(n))
print("1) short (<2k ctx)...");  ask([{"role":"system","content":"You are a concise assistant."},{"role":"user","content":"Explain in two sentences what a KV cache is in LLM inference, then check weather for Oslo."}])
time.sleep(5)
print("2) short-2..."); ask([{"role":"user","content":"Write a haiku about speculative decoding, then report weather for Tokyo."}])
time.sleep(5)
print("3) medium ~20-40k..."); ask([{"role":"system","content":"You are an analyst."},{"role":"user","content":"Here is the telemetry dump:\n"+pad(250)+"\nSummarize the top 3 patterns in under 100 words, then look up weather for Bern."}])
time.sleep(5)
print("4) multi-turn growing...")
h=[{"role":"user","content":"Begin a numbering exercise: list 250 telemetry items numbered, one line each, terse."}]
ask(h); h+= [{"role":"assistant","content":"Done."},{"role":"user","content":"Now add analysis sentences to items 100-249 and restate them."}]
ask(h,maxtok=250)
print("dev capture complete")
