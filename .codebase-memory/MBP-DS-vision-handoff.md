# HANDOFF — MBP DS vision / single-slot contention issue (investigate server-side)

## TL;DR
The DeepSeek "flash-exp" endpoint at **`http://192.168.1.178:8100/v1`** (model `deepseek-v4-flash`)
**does have working vision** — it correctly reads and describes real PNGs. The problem is **error
behaviour under load**: when a request arrives while the **single-slot** server is busy (an agent turn
or container teardown in flight), image requests fail with **misleading** errors — `HTTP 502 Bad
gateway` and/or `{"error":{"message":"invalid or unsupported PNG image"}}` — even though the *exact
same* request succeeds seconds later when the slot is idle. The "invalid PNG" string is a **contention
artifact, not a real format rejection.**

## Who is affected / why this matters
Our benchmark harness (Harbor + OpenCode2 agent, "oc2") sends an `image_url` content part to this
endpoint whenever the agent's `read` tool opens a PNG. So in a real run, the agent's *own* vision calls
will intermittently hit this if the endpoint misreports under contention. Today it has **not** corrupted
a result (the one vision cell we ran, code-from-image, was solved via a file-hash shortcut that never
exercised vision), but any genuine vision cell is at risk.

## Environment / access
- Endpoint: `http://192.168.1.178:8100/v1/chat/completions` (OpenAI-compatible), model `deepseek-v4-flash`.
- Auth: `Authorization: Bearer $NAZERIMS_MBP_KEY` (the operator has the key; it is an env var here).
- **The server is SINGLE-SLOT**: it serialises requests and does not cleanly handle a concurrent
  inbound request while mid-generation. **Never issue two benchmark jobs at once against it.**
- Repro script (client side, already written): `/mnt/scratch/naz/Projects/termbench/vision-repro.py`
  Usage: `bash -lc 'python3 vision-repro.py [path/to/image.png]'`  (defaults to a real task PNG).

## Evidence (timeline, same host)
1. During a busy period (an agent trial running on the slot): image request → **`HTTP 502 Bad
   gateway`** ("Remote end closed connection without response"); a **text-only** request at the same
   instant also returned **empty** (slot was serving the agent turn).
2. A grounding probe (real red PNG, `max_tokens=400`) → **`{"error":"invalid or unsupported PNG image"}`**.
3. Minutes later, slot confirmed **idle** (`docker ps` = 0 task containers), the SAME script:
   - `TEXT control` → **HTTP 200**, `content='TEXT-OK'`
   - `REAL PNG (code.png, 1160x804 RGBA)` → **HTTP 200**, content = *"The image shows a snippet of
     Python code that defines a main function to read an…"*  → **vision grounds correctly**
   - `SYNTHETIC 64x64 solid-red PNG` → **HTTP 200**, content=`'Red'`  → the PNG that was "invalid" in
     step 2 is now **accepted and correctly described**.

So the red PNG is **not** invalid; the error in step 2 was emitted because the slot was not free.

## Ruled out
- ❌ "MBP DS has no vision" — false; it describes real images correctly on an idle slot.
- ❌ "the synthetic PNG is malformed / server can't decode it" — false; it returned `Red` when idle.
- ❌ "a general OpenAI-schema `image_url` plumbing defect" — false; it works idle.
- ✅ Remaining: **server misreports inbound requests (esp. image payloads) while the single slot is
  busy / tearing down.**

## Leading hypothesis (confirm server-side)
The front-proxy (llama-swap / splashd / whichever fronts :8100) returns a transport-level error
(502) or a generic `invalid_request_error` **`invalid or unsupported PNG image`** when an image request
lands while the engine is occupied or between sessions — i.e. an **error-handling / queueing gap**, not
a vision defect. Note the specific string "invalid or unsupported PNG image" is *wrong* for the actual
cause (contention) — that mislabel is itself the likely bug: the image-validation step may be hitting a
null/partial buffer (engine busy) and falling through to a "can't decode" branch.

## Decisive experiment to run (controlled concurrency)
Reproduce the success/failure **pair** deterministically:
1. **Baseline (idle):** run `vision-repro.py` → expect all `HTTP 200` incl. `Red` + the code.png
   description. (Already shown.)
2. **Occupy the slot:** start a long generation that keeps the slot busy for ~20–30s, e.g.
   ```bash
   curl -sS -m 60 -N \
     -H "Authorization: Bearer $NAZERIMS_MBP_KEY" -H 'Content-Type: application/json' \
     -d '{"model":"deepseek-v4-flash","temperature":0.7,"max_tokens":600,"stream":true,
          "messages":[{"role":"user","content":"Write a very long essay about operating systems."}]}' \
     http://192.168.1.178:8100/v1/chat/completions > /tmp/occupy.out 2>&1 &
   ```
3. **Mid-turn, fire the image request** (within ~2s of step 2 starting):
   ```bash
   B64=$(base64 -w0 /mnt/scratch/naz/Projects/termbench/datasets/terminal-bench/code-from-image/environment/code.png)
   curl -sS -i -m 60 \
     -H "Authorization: Bearer $NAZERIMS_MBP_KEY" -H 'Content-Type: application/json' \
     -d "{\"model\":\"deepseek-v4-flash\",\"temperature\":0.0,\"max_tokens\":400,
          \"messages\":[{\"role\":\"user\",\"content\":[
            {\"type\":\"text\",\"text\":\"What is in this image?\"},
            {\"type\":\"image_url\",\"image_url\":{\"url\":\"data:image/png;base64,$B64\"}}]}]}" \
     http://192.168.1.178:8100/v1/chat/completions
   ```
   **Expected (if hypothesis holds):** a `502`, a connection reset, or `invalid or unsupported PNG
   image` — despite a valid PNG. Repeat a few times to see intermittency, and vary: (a) non-stream vs
   stream occupy, (b) text request mid-turn vs image request mid-turn (does text also fail, or only
   images?), (c) two concurrent image requests.

## Data to collect server-side (correlate with the client timestamps)
- Front-proxy + engine logs around the failing request: the **actual status code** the proxy emits vs
  what the client sees, and the stack that produced `invalid or unsupported PNG image`.
- Does the proxy **queue** concurrent requests or reject them? If reject, which layer turns it into
  "invalid PNG"?
- Whether **text** requests mid-turn also error (we saw an empty response once) — is it image-only?
- The image-validation path: does it read the full `image_url` data URI before the engine is acquired?
  A busy engine returning an empty/partial buffer would explain the false "unsupported PNG".

## One-shot repro commands (copy-paste)
```bash
# needs: $NAZERIMS_MBP_KEY set; endpoint IDLE for the success path
cd /mnt/scratch/naz/Projects/termbench

# A) full 3-way (text / real PNG / synthetic PNG) with status + content:
bash -lc 'python3 vision-repro.py'

# B) single image request against the REAL task image:
B64=$(base64 -w0 datasets/terminal-bench/code-from-image/environment/code.png)
curl -sS -i -m 60 -H "Authorization: Bearer $NAZERIMS_MBP_KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"deepseek-v4-flash\",\"temperature\":0.0,\"max_tokens\":400,\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"What is in this image?\"},{\"type\":\"image_url\",\"image_url\":{\"url\":\"data:image/png;base64,$B64\"}}]}]}" \
  http://192.168.1.178:8100/v1/chat/completions

# C) tiny solid-red synthetic PNG (base64) — accepted as 'Red' when idle, "invalid PNG" when busy:
echo 'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAb0lEQVR4nO3PAQkAAAyEwO9feoshgnABdLep8QUNyPEFDcjxBQ3I8QUNyPEFDcjxBQ3I8QUNyPEFDcjxBQ3I8QUNyPEFDcjxBQ3I8QUNyPEFDcjxBQ3I8QUNyPEFDcjxBQ3IPanc8OLDQitxAAAAAElFTkSuQmCC'

# D) text-only control (should ALWAYS be 200 when idle):
curl -sS -m 20 -H "Authorization: Bearer $NAZERIMS_MBP_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","temperature":0.0,"max_tokens":12,"messages":[{"role":"user","content":"Reply with exactly: TEXT-OK"}]}' \
  http://192.168.1.178:8100/v1/chat/completions
```

## Gotchas (avoid mis-reading the results)
- **Test when the slot is idle** (`docker ps` shows 0 task containers) for the baseline; a "busy" run
  is not a valid format test.
- `curl` without `-i` hides the HTTP status; use `-i` (or a parser) — an error body is still valid JSON.
- Our own status checks once falsely reported "busy" because a `pgrep` pattern self-matched the grep
  line — ignore self-matches; trust `docker ps` container count for idle detection.
- The engine also serves a non-vision model id `deepseek-v4-pro`; test on `deepseek-v4-flash`.

## What we (benchmark side) need to know to proceed
1. Is vision **safe** for real runs, i.e. will the harness's per-turn image calls succeed given the
   server is effectively single-slot and the agent only ever sends one request at a time (no external
   concurrency)? If the failures need *external* concurrent load, normal single-agent runs are fine.
2. If the proxy **should** queue image requests mid-turn, that's the fix; the wrong error string is a
   secondary cosmetic bug worth correcting.
3. Confirm whether **text** requests also misreport mid-turn (affects all cells, not just vision).


---

# SERVER-SIDE VERDICT (2026-11-05 07:00, ds4 lane) — hypothesis partly refuted

First: `192.168.1.178` **is this machine**, and the `:8100` front is **our own**
`auth_proxy.py` (ThreadingHTTPServer, logging to `log/auth_proxy.log`) → upstream
`127.0.0.1:8001` = `ds4-server`. "Investigate server-side" therefore lands in this lane,
and the proxy is ours to fix.

## The 502 family is heterogeneous — attributing every entry
`Connection refused` x17, `timed out` x6, `{e}`-traceback lines x5 (= *source-line echoes
in Python tracebacks, not an f-string bug - checked all three `send_error` sites are
f-prefixed), `Broken pipe` x5, `Remote end closed connection without response` x1.

**Two of the six `timed out` are the DSML rewind stall, proven by arithmetic:** the proxy
timeout is `timeout=600`.

| rebuild start (engine log) | proxy 502 `timed out` | delta | engine's own report |
|---|---|---|---|
| 19:28:26 (212,546 tok, est 607 s) | **19:38:26** | **exactly 600 s** | `rewind rebuild done 19:38:28 elapsed_s=601.4` |
| 19:39:31 (212,525 tok, est 607 s) | **19:49:31** | **exactly 600 s** | `rewind rebuild done 19:49:43 elapsed_s=612.1` |

So the harness was not seeing "misreported contention" - the proxy gave up at 600 s on a
request the engine was silently re-prefilling for 601-612 s, and the engine finished 2-12 s
later. Those rebuilds are the bug this lane fixed and deployed (`18fa4f8`, `ecef491`; field
result: 115 rewind events / 0 rebuilds over the 4-hour deep watch, 0.7-2.8 t/s turns gone).
The four earlier `timed out` entries (12:59, 13:23, 13:45, 14:02) predate our logging
instrument but match the same signature in the historical census (24 rebuilds / 150 GPU-min).

**`Remote end closed connection without response` at 00:23:13 is the Metal abort**, one
second before `ds4-server-2026-11-05-002314.ips` (driver assertion 1094). The image request
did not get rejected for being concurrent - it *was* the concurrency that killed the engine
(`PLAN-VISION-BUFFER-RACE.md`, reproduced in 3 s by `./tests/test_metal_command_memory race`).

**`Broken pipe` x5** at 03:30:58 = client-side disconnect; the engine logged
`client disconnected` in the same second. Not a server fault.

**`Connection refused` x17** = engine down. The 00:24:03-00:26:39 cluster and the
06:22-06:41 gap are **ours** (crash recovery, then banking + deploy). Your harness retried
into dead windows; those lost trials are not a vision problem.

## Answers to your three open questions
1. **Is vision safe?** Not under concurrency, and client-side serialisation isn't enough:
   the proxy has **no single-flight**, so two requests *can* reach the single-slot engine -
   at 00:23:14 exactly that happened (deep sampled decode + parse-time image encode, both
   submitting GPU work). Since 06:41:43 the engine carries a guard that turns that abort into
   a diverted submit + a log line; the engine-side race still needs the proper fix (mutex on
   the shared retain lists, then the section design).
2. **Should the proxy queue mid-turn? Yes - that is the cheap real fix.** A single-flight
   semaphore around the upstream POST in `auth_proxy.py` would have prevented the abort
   outright, independent of engine work. The wrong error string is genuinely a secondary
   cosmetic bug (see below, but it is not a contention artifact).
3. **Do text requests misreport mid-turn? Yes - proven, twice.** The `timed out` 502s above
   were ordinary long agent turns, not image requests; and any abort kills every in-flight
   connection, which is your observed empty text response.

## The "invalid or unsupported PNG image" string - mechanism is NOT contention
It is ours: `ds4_image.c:251`, reached only when the payload **starts with valid PNG magic**
(`\x89PNG\r\n\x1a\n`) **and libpng then fails**. A null/empty buffer cannot produce it - that
path says `"image is empty or exceeds the 64 MiB encoded limit"` (ds4_image.c:243). So the
failure means a **truncated PNG body actually reached the decoder**, not that the engine was
busy. Your inference "the mislabel is the bug" was right; the proposed cause was not.
Also worth knowing: `_read_body()` returns `b""` when the client omits `Content-Length`
(chunked upload) - a real gap in the proxy, though its symptom would be an empty POST rather
than this string. Note the engine never *logs* this rejection (grep: zero occurrences in any
ds4 log), so it is invisible server-side today - that's why your step-2 event had no trace.

## New risk created by fixing the stall (please read)
Legitimate deep turns are now the longest thing on the link: measured post-fix
**p99 630 s, max 751 s** (17,297 tokens at 23 t/s). Both exceed the proxy's `timeout=600`,
so a *successful* turn will start producing `timed out` 502s exactly like a stall did. We
have not seen one since 19:49 yet, so treat this as next-up, not current: raise the proxy
timeout (and/or make it configurable) to >= 1800 s, or the harness will lose good turns for
a new reason that looks identical to the old one.

## Proposed next steps (this lane, awaiting operator go)
1. `auth_proxy.py`: single-flight semaphore upstream + configurable timeout (small, no engine
   contact) - prevents both the abort class and the new timeout class.
2. Log PNG decode rejections with `encoded_len` + first/last 8 bytes so a truncated body is
   identifiable next time, instead of a bare mislabel.
3. Run your "decisive experiment" - but **only with capture redirected to a scratch dir**,
   because step 2-3 (occupy the slot, fire an image mid-turn) is now known to be a crash
   recipe against an unguarded engine, and it would also write synthetic rows into the corpus.
   Expected result on the current binary: **no abort**, engine stays up, and a
   `GPU batch section touched by a foreign thread at ...` line appears (the guard's signal).
