# Verified reproduction (curl only — no scripts from this repo)

Ran 2026-11-18 on a **pristine `b3f5018` checkout** built with `make` (`git status` clean, 0 modified
files, binary newer than sources) — i.e. exactly what `git clone && make` gives a maintainer.
Model `Qwen3.8-Flash-Next-Q4.gguf`, macOS/Metal, one NVIDIA-external scratch dir. Both servers were
started from a shell with `DS4_API_KEY` unset.

The two servers differ in **one flag**. Nothing is read from disk on the `B` side — verified in its own
log: `kv cache hit` 0 lines, `kv cache stored` 0 lines (the `A` run logs 2 hits and 4 stores).

## 1. Deterministic prompt, no corpus and no tokeniser needed

```sh
python3 - <<'PY'
import json
SENT = ("The catchment's baseflow is sustained by fractured bedrock rather than soil moisture, "
        "which decouples stream response from rainfall intensity. ")
doc = "Archive 0. " + (SENT * (76000 // len(SENT) + 1))[:76000]
body = json.dumps({
    "model": "default",
    "messages": [
        {"role": "system", "content": doc},
        {"role": "user", "content": "In one paragraph, what single factor governs the "
                                    "effect described above, and why?"}],
    "temperature": 0, "max_tokens": 256, "stream": False,
    "chat_template_kwargs": {"enable_thinking": False}})
open("prompt.json", "w").write(body)
print(len(doc), "chars ->", __import__("hashlib").sha256(doc.encode()).hexdigest()[:16])
PY
```

Byte-exact and commit-independent: 76,011 chars, `sha256` prefix `9dc9f22c81921e73`,
`prompt_tokens = 13858` in both configs.

## 2. Server A — disk KV enabled

```sh
mkdir -p /tmp/kva
./ds4-server --model MODEL.gguf --ctx 65536 --port 8079 --kv-disk-dir /tmp/kva &
curl -s -H 'Content-Type: application/json' -d @prompt.json \
     http://127.0.0.1:8079/v1/chat/completions | python3 -c 'import json,sys; print(json.load(sys.stdin)["choices"][0]["message"]["content"])'
```

Server log signature (three chunks; the middle boundary is the cold-checkpoint sync):

```
chat ctx=0..13858:13858 prompt start
prefill chunk 8192/13858 (59.1%)
prefill chunk 12288/13858 (88.7%)
prefill chunk 13858/13858 (100.0%)
kv cache stored tokens=12288 trimmed=1570 reason=cold key=token-text size=482.82 MiB
```

## 3. Server B — identical, minus `--kv-disk-dir`

```sh
./ds4-server --model MODEL.gguf --ctx 65536 --port 8079 &
curl -s ... (same request)
```

```
chat ctx=0..13858:13858 prompt start
prefill chunk 8192/13858 (59.1%)
prefill chunk 13858/13858 (100.0%)
```

Same prompt, same binary, same `prompt_tokens = 13858`; **the `12288` boundary exists only in A**, because
writing the cold checkpoint requires syncing the session there mid-prefill.

## 4. The outputs differ

```
A (disk ON)  609 chars  sha256 9ecec8d5303ae277d66e1707231ae51e813748c8bfba3d3b70fac3bf9c8397a4
  "The single governing factor is the **geological structure of the bedrock**, specifically its
   fractured nature. This is because the fractured bedrock acts as the primary reservoir and
   conduit for groundwater, sustaining baseflow th..."

B (disk OFF) 754 chars  sha256 417a4130d5e43f1536abcc9a1201e4452ae637c7e4881450f4986b7d430e8faa
  "The single governing factor is the **geological structure of the catchment**, specifically the
   presence of **fractured bedrock**. This factor dictates the hydrological pathway because it
   shifts the primary source of baseflow from..."
```

A second prompt (`"Archive 1. "`, same body) behaves the same way: `1be64c1daffd…` vs `225c29c7f71b…`,
615 vs 707 chars.

**Determinism check, included because it is what makes this a bug report rather than a flake:** each
config was requested twice. Within a config the two replies were byte-identical (asserted in the harness,
not eyeballed), so the difference is a function of the flag, not of run order or noise.

**Bonus data point from the repeats.** In server A, request 2 did *not* re-run the full prefill: the log
shows `live kv cache miss … reason=token-mismatch`, then `kv cache hit text tokens=12288` and
`chat ctx=12288..13858:1570 prompt start` — a genuine disk resume — and its completion was **identical to
the cold one**. So resuming at the cold checkpoint's own boundary is faithful; what is not faithful is a
*different chunk schedule*, whichever route gets you there.

## Notes on reproducing this

* Any prompt long enough for the cold boundary to land strictly inside it reproduces this. Here
  `cold_store_len = 12288` with `P = 13858`; the default `cold_max_tokens` (30000) and `min_tokens` (512)
  bracket it, so no cache tuning flags are involved.
* The chunk schedule is visible without comparing any prose — `grep 'prefill chunk'` on the two logs is a
  10-second check, and it is the part worth confirming first.
* Larger-sample measurement of the same comparison (12 prompts, real documentation text, both builds):
  fork 6/6 differ, upstream `b3f5018` 4/6 differ. Two upstream agreements included, which is the expected
  shape for floating-point amplification rather than corruption.
* Driver used for the larger samples: `repro_curl_only.sh` in this folder (it runs the commands above, twice,
  with assertions). The above is the minimal manual version.
