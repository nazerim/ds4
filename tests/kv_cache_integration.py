#!/usr/bin/env python3
"""End-to-end KV disk-cache integration test against a real ds4-server.

Strategy: a "cold" checkpoint of the prompt is written right after prefill. So:
  * Phase 1: start the server, send a single-turn prompt P -> a checkpoint of
    render(P) is stored to the disk cache ("kv cache stored").
  * Stop the server (clears the in-memory live KV, but the disk cache persists).
  * Phase 2: start the server again on the SAME KV dir, send the identical
    prompt P -> live KV is empty, so the prompt must be rebuilt from the disk
    checkpoint ("kv cache hit text").

This exercises the real store + find_text_prefix + load path (v2 headers,
model_fp, conv_id) without depending on multi-turn assistant re-rendering.

The server is spawned on a private port with a fresh temp KV dir and a separate
instance-lock file, so it never collides with a production server. It is opt-in:
it skips (exit 0) if the model or binary is absent. Configure via env:
  DS4_IT_MODEL    path to the GGUF (default: the 0731 mixed-quant build)
  DS4_IT_PORT     server port (default 8011)
  DS4_IT_CTX      context size (default 8192)
  DS4_IT_MAXTOK   max tokens to generate per turn (default 16)

Usage: python3 tests/kv_cache_integration.py
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MODEL = (
    "/Users/naz/.omlx/models/jmilnz/DeepSeek-V4-Flash-0731-antirez-ds4-GGUF/"
    "DeepSeek-V4-Flash-0731-Layers37-42Q4K-mixed-realimatrix-v2.gguf"
)
MODEL = os.environ.get("DS4_IT_MODEL", DEFAULT_MODEL)
PORT = int(os.environ.get("DS4_IT_PORT", "8011"))
CTX = int(os.environ.get("DS4_IT_CTX", "8192"))
MAXTOK = int(os.environ.get("DS4_IT_MAXTOK", "16"))
BASE = f"http://127.0.0.1:{PORT}"

# A single-turn prompt long enough to comfortably exceed min_tokens.
PROMPT = (
    "You are a helpful assistant. Please write a detailed, multi-paragraph "
    "overview of the history of lighthouse engineering, covering the earliest "
    "stone towers, the development of Fresnel lenses, the transition to "
    "automated electric lights, and the preservation of historic lighthouses "
    "as cultural landmarks in the modern era. Include notable examples."
)


def log(msg):
    print(f"[kv-it] {msg}", flush=True)


def post_chat(messages):
    body = json.dumps({
        "model": "deepseek-v4-flash",
        "messages": messages,
        "max_tokens": MAXTOK,
        "temperature": 0.0,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode())


def wait_ready(deadline_s=240):
    t0 = time.time()
    while time.time() - t0 < deadline_s:
        try:
            with urllib.request.urlopen(f"{BASE}/v1/models", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(1.0)
    return False


class Server:
    def __init__(self, kv_dir, lock_file, log_path):
        self.log_path = log_path
        self._log_f = open(log_path, "wb")
        env = dict(os.environ, DS4_LOCK_FILE=lock_file)
        cmd = [
            os.path.join(REPO_ROOT, "ds4-server"),
            "--model", MODEL,
            "--ctx", str(CTX),
            "--tokens", str(CTX),
            "--host", "127.0.0.1",
            "--port", str(PORT),
            "--kv-disk-dir", kv_dir,
            "--kv-disk-space-mb", "2048",
            "--kv-cache-min-tokens", "32",
        ]
        self._proc = subprocess.Popen(cmd, stdout=self._log_f,
                                      stderr=subprocess.STDOUT, env=env,
                                      cwd=REPO_ROOT)

    def ready(self):
        return wait_ready()

    def stop(self):
        self._proc.send_signal(signal.SIGTERM)
        try:
            self._proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._log_f.close()

    def log_text(self):
        with open(self.log_path, "r", errors="replace") as f:
            return f.read()


def main():
    if not os.path.exists(MODEL):
        log(f"SKIP: model not found: {MODEL}")
        return 0
    if not os.path.exists(os.path.join(REPO_ROOT, "ds4-server")):
        log("SKIP: ./ds4-server not built (run `make ds4-server`)")
        return 0

    kv_dir = tempfile.mkdtemp(prefix="ds4-kv-it-")
    lock_file = os.path.join(kv_dir, "ds4.lock")
    log(f"model={MODEL}")
    log(f"port={PORT} ctx={CTX} kv_dir={kv_dir}")

    try:
        # Phase 1: store a checkpoint of the prompt.
        log("phase 1: starting server, sending prompt (expect store)")
        s1 = Server(kv_dir, lock_file, os.path.join(kv_dir, "phase1.log"))
        try:
            if not s1.ready():
                log("FAIL: phase 1 server did not become ready")
                return 1
            r1 = post_chat([{"role": "user", "content": PROMPT}])
            log(f"phase 1 response: {r1['choices'][0]['message']['content'][:50]!r}...")
            time.sleep(1.0)
            stored = "kv cache stored" in s1.log_text()
            log(f"phase 1 kv cache stored: {stored}")
            if not stored:
                log("FAIL: phase 1 did not store a checkpoint")
                for line in s1.log_text().splitlines():
                    if "kv cache" in line:
                        log("  " + line)
                return 1
        finally:
            s1.stop()

        # Phase 2: fresh server (empty live KV) must rebuild from disk.
        log("phase 2: restarting server, re-sending prompt (expect disk hit)")
        s2 = Server(kv_dir, lock_file, os.path.join(kv_dir, "phase2.log"))
        try:
            if not s2.ready():
                log("FAIL: phase 2 server did not become ready")
                return 1
            r2 = post_chat([{"role": "user", "content": PROMPT}])
            log(f"phase 2 response: {r2['choices'][0]['message']['content'][:50]!r}...")
            time.sleep(1.0)
            txt = s2.log_text()
            hit = "kv cache hit" in txt
            log(f"phase 2 kv cache hit: {hit}")
            if hit:
                log("PASS: prompt checkpoint persisted to disk and reloaded")
                return 0
            log("FAIL: phase 2 did not hit the disk cache")
            for line in txt.splitlines():
                if "kv cache" in line or "cache miss" in line:
                    log("  " + line)
            return 1
        finally:
            s2.stop()
    finally:
        subprocess.run(["rm", "-rf", kv_dir], check=False)


if __name__ == "__main__":
    sys.exit(main())
