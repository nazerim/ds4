#!/usr/bin/env python3
# Live vision identity + budget + disk round-trip test against a ds4-server.
# Usage: python3 live_vision_identity_test.py <base_url> <img_dir> [pre|post]
#   pre  (before a server restart): phase 1 duplicate-image sandwich,
#        phase 2 over-budget reduction
#   post (after restart): phase 3 disk-restore replay, phase 4 shorter replay
# Server-side assertions (grep the log): encoder reuse with runs=0 for the
# duplicate turn, kept-16/omitted-4 warning, zero fingerprint rejects,
# "multimodal disk kv hit" with the duplicated identities adopted.
import base64, json, sys, urllib.request

BASE, IMG = sys.argv[1].rstrip('/'), sys.argv[2].rstrip('/')
MODE = sys.argv[3] if len(sys.argv) > 3 else 'pre'

def data_uri(name):
    with open(f"{IMG}/{name}", 'rb') as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()

def chat(messages, max_tokens=24):
    body = json.dumps({"model": "t", "max_tokens": max_tokens,
                       "thinking": {"type": "disabled"},
                       "messages": messages}).encode()
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]

def img_msg(text, uri):
    return {"role": "user", "content": [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": uri}}]}

A = data_uri("drill-wp-1-home.png")
B = data_uri("drill-wp-2-home.png")

CONV_FILE = f"/tmp/dupconv-{BASE.split('//')[1].replace(':','_')}.json"

if MODE == 'pre':
    print("== phase 1: A,B,A sandwich (identical bytes at two positions) ==")
    h = [img_msg("Describe this page.", A)]
    m1 = chat(h)
    h += [{"role": "assistant", "content": m1.get("content") or "ok"},
          img_msg("And this one?", B)]
    m2 = chat(h)
    h += [{"role": "assistant", "content": m2.get("content") or "ok"},
          img_msg("Same as the first, right?", A)]
    m3 = chat(h)
    print("PASS phase1" if m3 else "FAIL phase1")
    with open(CONV_FILE, 'w') as f:   # byte-exact replay fuel for post mode
        json.dump(h, f)

    print("== phase 2: 20 images under keep=16 ==")
    files = ["drill-wp-1-home.png", "drill-wp-2-home.png", "drill-wp-3-home.png",
             "drill-wp2-home.png", "drill-wp2-login.png", "repro_hsbc-ai.png",
             "wp-2_login_.png", "wp-3_hsbc-ai_.png", "wp3-REPRO-dashboard.png",
             "live-survey-132596.png"]
    uris = [data_uri(f) for f in files]
    big = [{"role": "user", "content":
            [{"type": "text", "text": "Twenty pages:"}] +
            [{"type": "image_url", "image_url": {"url": u}} for u in uris * 2] +
            [{"type": "text", "text": "Count them."}]}]
    try:
        chat(big, max_tokens=16)
        print("PASS phase2 (200 OK; reduction is log-asserted)")
    except Exception as e:
        print(f"FAIL phase2: {e}")
else:
    print("== phase 3: dupconv extended post-restart (disk restore) ==")
    with open(CONV_FILE) as f:
        h = json.load(f)
    try:
        chat(h + [{"role": "assistant", "content": h[-1]["content"]
                   if h[-1]["role"] == "assistant" else "ok"},
                  img_msg("One more look at the second.", B)], max_tokens=16)
        print("PASS phase3 (disk-hit is log-asserted)")
    except Exception as e:
        print(f"FAIL phase3: {e}")

    print("== phase 4: shorter replay (rewind path) ==")
    try:
        chat(h[:3], max_tokens=16)
        print("PASS phase4 (no crash; behavior log-asserted)")
    except Exception as e:
        print(f"FAIL phase4: {e}")
