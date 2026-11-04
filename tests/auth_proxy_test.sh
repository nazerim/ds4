#!/bin/sh
# Functional test for the auth_proxy changes. Throwaway instance on 127.0.0.1:8199;
# the live :8100 proxy is never touched. The test key is passed through the
# environment and assembled at runtime (no credential-shaped literals in this file,
# which is what broke the previous run: a masked literal left the client and the
# server holding different values, so every request 401'd and test 1 "passed" as a
# false positive - auth was enforced against a key that was never being sent).
# max_tokens stays under 40 so no test turn can enter the training corpus.
cd /Users/naz/Projects/ds4 || exit 1
P=/tmp/proxytest.log
TEST_PROXY_KEY=***-serialise-8199
export TEST_PROXY_KEY
BIND_PORT=8199 BIND_HOST=127.0.0.1 DS4_API_KEY="$TEST_PROXY_KEY" \
UPSTREAM_PORT=8001 UPSTREAM_TIMEOUT=3600 QUEUE_TIMEOUT=45 \
    ./auth_proxy.py > "$P" 2>&1 &
PID=$!
sleep 1.5

echo "=== banner (the ceiling is printed, so a wrong timeout is visible at a glance) ==="
head -1 "$P" | cut -c1-165

echo "=== 1) auth enforced: request WITHOUT the key -> want 401 ==="
curl -s -o /dev/null -w "  no-key:   got %{http_code}  (want 401)\n" -X POST \
  http://127.0.0.1:8199/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{"model":"ds4","messages":[]}'
echo "===    and WITH the key -> want 200 (proves test 1 above is a real pass) ==="
curl -s -m 90 -o /dev/null -w "  with-key: got %{http_code}  (want 200)\n" -X POST \
  http://127.0.0.1:8199/v1/chat/completions -H "Authorization: Bearer $TEST_PROXY_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"ds4","max_tokens":8,"temperature":0,"messages":[{"role":"user","content":"say OK"}]}'

echo "=== 2) truncated PNG: engine rejects; proxy must NAME the truncation ==="
python3 - <<'PY'
import base64, json, os, urllib.request, urllib.error
key = os.environ["TEST_PROXY_KEY"]
png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 96          # magic yes, IHDR/IEND no
body = {"model": "ds4", "max_tokens": 32, "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What is in this image?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," +
             base64.b64encode(png).decode()}}]}]}
req = urllib.request.Request("http://127.0.0.1:8199/v1/chat/completions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json",
             "Authorization": "Bearer " + key}, method="POST")
try:
    print("  client status:", urllib.request.urlopen(req, timeout=120).status)
except urllib.error.HTTPError as e:
    print("  client status:", e.code, "body:", e.read()[:88])
PY
sleep 0.5
grep -a "IMG-REJECT" "$P" | head -3 | cut -c1-190

echo "=== 3) chunked upload: previously forwarded as an EMPTY body ==="
python3 - <<'PY'
import http.client, json, os
key = os.environ["TEST_PROXY_KEY"]
body = json.dumps({"model": "ds4", "max_tokens": 8, "temperature": 0,
                   "messages": [{"role": "user", "content": "say OK"}]}).encode()
c = http.client.HTTPConnection("127.0.0.1", 8199, timeout=90)
c.putrequest("POST", "/v1/chat/completions")
c.putheader("Authorization", "Bearer " + key)
c.putheader("Content-Type", "application/json")
c.putheader("Transfer-Encoding", "chunked")
c.endheaders()
for i in range(0, len(body), 16):
    part = body[i:i + 16]
    c.send(b"%x\r\n%s\r\n" % (len(part), part))
c.send(b"0\r\n\r\n")
r = c.getresponse()
print("  chunked status:", r.status, "|", "FORWARDED CORRECTLY"
      if r.status == 200 else "check: " + str(r.read()[:60]))
c.close()
PY
grep -ac "POST-BODY-UNFRAMEABLE" "$P" | sed 's/^/  unframeable rejections (want 0): /'

echo "=== 4) single-flight: concurrent POSTs must SERIALISE, not race ==="
python3 - <<'PY'
import json, os, threading, time, urllib.request
key = os.environ["TEST_PROXY_KEY"]
out = {}
def post(tag, n):
    body = json.dumps({"model": "ds4", "max_tokens": n, "temperature": 0.7,
                       "messages": [{"role": "user",
                       "content": "Count from 1 to 30, one number per line."}]}).encode()
    req = urllib.request.Request("http://127.0.0.1:8199/v1/chat/completions",
        data=body, headers={"Content-Type": "application/json",
                            "Authorization": "Bearer " + key}, method="POST")
    t0 = time.time()
    try:
        urllib.request.urlopen(req, timeout=300).read()
        out[tag] = "ok %.1fs" % (time.time() - t0)
    except Exception as e:
        out[tag] = "%s %.1fs" % (type(e).__name__, time.time() - t0)
N = 3
ts = [threading.Thread(target=post, args=("c%d" % i, 32)) for i in range(N)]
t0 = time.time()
for t in ts: t.start()
for t in ts: t.join()
wall = time.time() - t0
ok = [v for v in out.values() if v.startswith("ok")]
sums = sum(float(v.split()[1].rstrip("s")) for v in ok)
all_ok = len(ok) == N
if not all_ok:
    verdict = "NO - some callers were REJECTED, not queued"
elif wall > 0.6 * sums:
    verdict = "YES - serialised; nothing concurrent reached the engine"
else:
    verdict = "NO - ran concurrently (wall %.1fs << sum %.1fs)" % (wall, sums)
print("  %d concurrent requests: wall %.1fs | per-request %s" % (N, wall, out))
print("  all succeeded AND wall ~= sum (serialised)? %s" % verdict)
PY
sleep 0.5
grep -ac "QUEUED" "$P" | sed 's/^/  requests that waited their turn: /'
grep -a "QUEUED" "$P" | tail -3 | cut -c1-125

echo "=== 5) engine survived (the whole point) ==="
lsof -nP -iTCP:8001 -sTCP:LISTEN -t | sed 's/^/  upstream still listening, pid /'
grep -ac "generation rewind rebuild ctx=" log/ds4.log | sed 's/^/  rebuilds this boot: /'
grep -ac "foreign thread at" log/ds4.log | sed 's/^/  guard tripwire lines: /'

kill "$PID" 2>/dev/null
echo "=== proxy log tail (queue and reject lines land here) ==="
grep -aE "QUEUED|IMG-REJECT|UPSTREAM-ERROR|QUEUE-TIMEOUT" "$P" | tail -6 | cut -c1-165
