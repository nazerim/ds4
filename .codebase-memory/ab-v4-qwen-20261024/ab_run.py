#!/usr/bin/env python3
"""Cheap A/B runner: posts fixed prompt set to one OpenAI-compat server, saves answers.
Usage: ab_run.py <base> <model> <out.jsonl> [vision]"""
import json, sys, base64, time, urllib.request

base, model, out = sys.argv[1], sys.argv[2], sys.argv[3]
prompts = json.load(open("/var/folders/gp/hjkf5cnx3vg4pxxv0zbd1c8h0000gn/T/opencode/ab_prompts.json"))
if "vision" in sys.argv:
    img = base64.b64encode(open("/Users/naz/Projects/Scratch/muse-glimmer/smoke-tests/test_image.png","rb").read()).decode()
    for p in prompts:
        for m in p["messages"]:
            if isinstance(m.get("content"), list):
                for part in m["content"]:
                    if part.get("type")=="image_url":
                        part["image_url"]["url"] = f"data:image/png;base64,{img}"

with open(out,"w") as f:
    for p in prompts:
        body = {"model":model,"temperature":0,"max_tokens":p["max_tokens"],"messages":p["messages"]}
        if p.get("tools"): body["tools"]=p["tools"]; body["tool_choice"]="auto"
        t0=time.time()
        try:
            r = urllib.request.urlopen(urllib.request.Request(base+"/v1/chat/completions",
                data=json.dumps(body).encode(), headers={"Content-Type":"application/json"}), timeout=600)
            d = json.load(r)
            m = d["choices"][0]["message"]
            rec = {"id":p["id"],"t":round(time.time()-t0,1),
                   "content":(m.get("content") or "").strip(),
                   "tool_calls":[[tc["function"]["name"], tc["function"]["arguments"]] for tc in (m.get("tool_calls") or [])],
                   "usage":d.get("usage",{}).get("completion_tokens")}
        except Exception as e:
            rec = {"id":p["id"],"error":str(e)[:200]}
        f.write(json.dumps(rec)+"\n"); f.flush()
        print(f"{p['id']}: {rec.get('error') or str(rec['content'])[:90]} | tools={rec.get('tool_calls')}")
print("RUN DONE")
