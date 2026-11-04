#!/usr/bin/env python3
"""Auth-gated reverse proxy for ds4-server.

Listens on BIND_HOST:BIND_PORT, requires `Authorization: Bearer $DS4_API_KEY`,
and forwards to UPSTREAM_HOST:UPSTREAM_PORT (the loopback ds4-server). Relays
streaming (SSE/chunked) responses without buffering. Uses only the stdlib.

Two things this proxy is responsible for, learned the hard way:

1. SINGLE-FLIGHT. The engine behind us is a single-slot inference server whose
   GPU submission path is NOT thread-safe: two concurrent requests put two
   threads in Metal encoder code at once, which aborted the engine with a driver
   assertion (see .codebase-memory/PLAN-VISION-BUFFER-RACE.md - the 00:23:14
   crash was a parse-time image encode overlapping a deep sampled decode, and the
   proxy had been forwarding both). ThreadingHTTPServer will happily hand every
   client its own thread, so the queueing obligation is ours: POSTs acquire a
   semaphore and WAIT their turn instead of racing into the engine. A client that
   cannot be queued gets a 503 it can retry, never a corrupted or dead upstream.
2. TRUTHFUL ERROR REPORTING. A 600 s upstream timeout turned legitimate deep
   turns into 502s that were indistinguishable from a stall (measured: the proxy
   gave up exactly 600 s after re-prefills that took 601.4 s and 612.1 s). The
   timeout is now generous by default, because a real prefill can exceed 30 min.
   Image rejections are logged with payload diagnostics: the engine's
   "invalid or unsupported PNG image" is emitted only when PNG magic IS present
   and libpng then fails, i.e. a truncated body reached the decoder - which the
   proxy can prove or disprove from the bytes it received.

Env:
  DS4_API_KEY     required - the bearer token clients must present
  UPSTREAM_HOST   default 127.0.0.1 (ds4-server stays on loopback)
  UPSTREAM_PORT   default 8001
  BIND_HOST       default 0.0.0.0 (the LAN address clients hit)
  BIND_PORT       default 8002
  UPSTREAM_TIMEOUT  default 3600 (seconds; deep prefills legitimately exceed 30 min)
  MAX_CONCURRENT_UPSTREAM default 1 (engine is single-slot; >1 reintroduces the race)
  QUEUE_TIMEOUT     default = UPSTREAM_TIMEOUT (how long a caller may wait its turn)
"""
import base64
import json
import os
import re
import sys
import threading
import time
import hmac
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.client import HTTPConnection

AUTH_KEY = os.environ.get("DS4_API_KEY", "")
UPSTREAM_HOST = os.environ.get("UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("UPSTREAM_PORT", "8001"))
BIND_HOST = os.environ.get("BIND_HOST", "0.0.0.0")
BIND_PORT = int(os.environ.get("BIND_PORT", "8002"))
UPSTREAM_TIMEOUT = int(os.environ.get("UPSTREAM_TIMEOUT", "3600"))
MAX_CONCURRENT = max(1, int(os.environ.get("MAX_CONCURRENT_UPSTREAM", "1")))
QUEUE_TIMEOUT = int(os.environ.get("QUEUE_TIMEOUT", str(UPSTREAM_TIMEOUT)))

if not AUTH_KEY:
    print("auth_proxy: DS4_API_KEY is required (refusing to start unauthenticated)", file=sys.stderr)
    sys.exit(1)

# One permit by default: the engine serialises generation, and its GPU submit path
# is not safe under concurrency. BoundedSemaphore so a stray release() is a loud
# error rather than a silently widening gate.
_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
_stats_lock = threading.Lock()
_stats = {"queued": 0, "queue_timeouts": 0, "max_wait_ms": 0, "img_rejects": 0}

# Headers we do not forward to the upstream (hop-by-hop / framing).
_HOP = {"connection", "keep-alive", "proxy-connection", "transfer-encoding",
        "content-length", "upgrade", "te", "host"}

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PNG_IEND = b"IEND"
_IMAGE_ERROR_MARKS = ("invalid or unsupported png", "invalid or unsupported jpeg",
                      "image is empty or exceeds", "decoded image limits",
                      "image token grid")


def _note(key, wait_ms=0):
    """Counters under a lock - the lesson of the engine-side bug is that a shared
    mutable object mutated from handler threads is a bug, even for telemetry."""
    with _stats_lock:
        if key == "max_wait_ms":
            _stats["max_wait_ms"] = max(_stats["max_wait_ms"], wait_ms)
        else:
            _stats[key] += 1
            if wait_ms:
                _stats["max_wait_ms"] = max(_stats["max_wait_ms"], wait_ms)


def _png_chunks(raw):
    """Walk the chunk stream: (type, declared_length) per chunk.

    A payload can carry correct magic AND a correct IEND trailer while its IDAT
    length fields are nonsense - which is what happened on the first run of this
    diagnostic: a hand-copied base64 string lost 16 characters mid-stream, so the
    old "complete" flag was true while libpng was right to reject the file. Parsing
    the chunk structure is what separates 'arrived corrupt' from 'valid PNG the
    engine should have decoded'.
    """
    if not raw.startswith(_PNG_MAGIC):
        return None
    i, chunks, end_ok = 8, [], False
    while i + 8 <= len(raw):
        ln = int.from_bytes(raw[i:i + 4], "big")
        typ = raw[i + 4:i + 8].decode("latin-1", "replace")
        chunks.append(typ)
        if ln > len(raw):                       # declared size larger than the body
            return {"chunks": chunks, "trailer": typ == "IEND", "aligned": False}
        i += 8 + ln + 4                          # header + data + CRC
        if typ == "IEND":
            end_ok = True
            break
    return {"chunks": chunks, "trailer": end_ok, "aligned": i == len(raw)}


def _image_payload_diagnostics(body):
    """Describe every image the client actually sent, as bytes we hold.

    The `verdict` field answers the question the engine cannot: TRUNCATED (no
    IEND), BROKEN (chunk stream does not add up - the corrupt-in-transit case),
    or SOUND (a well-formed PNG that the engine rejected for its own reasons,
    which IS a real engine bug).
    """
    out = []
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return out
    parts = []
    for msg in data.get("messages") or []:
        content = msg.get("content")
        if isinstance(content, list):
            parts.extend(p for p in content if isinstance(p, dict))
    for p in parts:
        if p.get("type") != "image_url":
            continue
        url = ((p.get("image_url") or {}).get("url")) or ""
        m = re.match(r"data:([^;,]+)[^,]*;base64,(.*)$", url, re.S)
        if not m:
            out.append({"kind": "url-or-unparsed", "chars": len(url), "verdict": "UNPARSED"})
            continue
        mime, b64 = m.group(1), m.group(2)
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception as e:
            out.append({"mime": mime, "verdict": "BASE64-DECODE-FAILED",
                        "detail": str(e)[:60]})
            continue
        rec = {"mime": mime, "b64_chars": len(b64), "bytes": len(raw),
               "head": raw[:8].hex(), "tail": raw[-12:].hex()}
        if raw.startswith(_PNG_MAGIC):
            rec["magic"] = "PNG"
            info = _png_chunks(raw)
            rec["chunk_types"] = info["chunks"] if info else None
            if info is None or not info["trailer"]:
                rec["verdict"] = "TRUNCATED (no IEND)"
            elif not info["aligned"]:
                rec["verdict"] = "BROKEN (chunk lengths do not add up - corrupt in transit)"
            elif "IHDR" not in rec["chunk_types"] or "IDAT" not in rec["chunk_types"]:
                rec["verdict"] = "BROKEN (missing IHDR/IDAT)"
            else:
                rec["verdict"] = "SOUND (well-formed PNG; the rejection is an engine problem)"
        elif raw.startswith(b"\xff\xd8\xff"):
            rec["magic"] = "JPEG"
            rec["verdict"] = ("SOUND-JPEG (EOI present)" if raw.endswith(b"\xff\xd9")
                              else "TRUNCATED (no EOI)")
        else:
            rec["magic"] = "other"
            rec["verdict"] = "UNSUPPORTED-TYPE (no PNG/JPEG signature)"
        out.append(rec)
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # SSE latency: never let Nagle hold a small event packet back.
    disable_nagle_algorithm = True

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        return hmac.compare_digest(auth[7:], AUTH_KEY)

    def _deny(self):
        self.send_error(401, "Unauthorized")

    def log_message(self, fmt, *args):
        sys.stderr.write("auth_proxy: %s - %s\n" % (self.address_string(), fmt % args))

    def _relay(self, resp, req_body=None):
        status = f"{resp.status} {resp.reason or ''}"
        clen = resp.getheader("Content-Length")
        # Buffer and inspect only error responses; 2xx streams stay untouched so
        # SSE keeps arriving event by event.
        if clen is not None and resp.status >= 400:
            err_body = resp.read(int(clen))
            self._log_upstream_error(resp, err_body, req_body)
            self._write_head(status, len(err_body))
            self.wfile.write(err_body)
            self.wfile.flush()
            return
        self._write_head(status, int(clen) if clen is not None else None)
        if clen is not None:
            self.wfile.write(resp.read(int(clen)))
            self.wfile.flush()
            return
        # Chunked/SSE: read1() returns as soon as any bytes are available, so
        # events are forwarded one at a time. read(n) would block until n bytes
        # accumulate (or EOF), which made live streams arrive in ~64 KiB blocks.
        read_stream = getattr(resp, "read1", None) or resp.read
        while True:
            chunk = read_stream(65536)
            if not chunk:
                break
            self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
            self.wfile.write(chunk + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _write_head(self, status, clen):
        self.wfile.write(f"HTTP/1.1 {status}\r\n".encode("ascii"))
        for k, v in self._resp_headers():
            self.wfile.write(f"{k}: {v}\r\n".encode("ascii"))
        if clen is not None:
            self.wfile.write(f"Content-Length: {clen}\r\n".encode("ascii"))
        else:
            self.wfile.write(b"Transfer-Encoding: chunked\r\n")
        self.wfile.write(b"\r\n")

    def _resp_headers(self):
        # Set by _relay from the upstream response; kept separate so the framing
        # header we emit always matches the body we actually write.
        return getattr(self, "_resp_hdrs", [])

    def _log_upstream_error(self, resp, body, req_body):
        """Name the real failure instead of letting it look like a format rejection."""
        try:
            text = body.decode("utf-8", "replace")
        except Exception:
            text = repr(body[:200])
        low = text.lower()
        marks = [m for m in _IMAGE_ERROR_MARKS if m in low]
        tag = "IMG-REJECT" if marks else "UPSTREAM-ERROR"
        self.log_message("%s status=%s msg=%.180s", tag, resp.status,
                         text.replace("\n", " ")[:180])
        if marks and req_body:
            diag = _image_payload_diagnostics(req_body)
            n = len(diag)
            with _stats_lock:
                _stats["img_rejects"] += 1
            for i, d in enumerate(diag):
                self.log_message("IMG-REJECT payload[%d/%d] %s", i + 1, n,
                                 json.dumps(d, sort_keys=True))
            # The engine cannot see any of this; this line is the whole point.
            self.log_message("IMG-REJECT request_bytes=%d verdicts=%s", len(req_body),
                             "; ".join(d.get("verdict", "?") for d in diag) or "no image parts")

    def do_GET(self):
        if not self._authorized():
            self._deny()
            return
        # Health/model listing does not touch the engine's single slot.
        self._forward("GET", b"")

    def do_POST(self):
        if not self._authorized():
            self._deny()
            return
        body, err = self._read_body()
        if err:
            # Previously a chunked upload was silently forwarded as an EMPTY body,
            # which surfaces upstream as a bogus content error. Refuse honestly.
            self.log_message("POST-BODY-UNFRAMEABLE %s", err)
            self.send_error(400, f"Cannot frame request body: {err}")
            return
        self._forward("POST", body, want_body=body)

    def do_OPTIONS(self):
        # CORS preflight: forward without auth so clients can discover the endpoint.
        self._forward("OPTIONS", b"")

    def _forward(self, method, body, want_body=None):
        acquired = False
        waited_ms = 0
        try:
            if method == "POST" and MAX_CONCURRENT:
                t0 = time.monotonic()
                # QUEUE_TIMEOUT is in SECONDS - do not divide by 1000 here. (The
                # first test run proved it: a "45 s" limit rejected two queued
                # callers after 45 ms, which is the old fail-fast behaviour this
                # semaphore exists to replace.)
                acquired = _slots.acquire(timeout=float(QUEUE_TIMEOUT))
                waited_ms = int((time.monotonic() - t0) * 1000)
                if not acquired:
                    with _stats_lock:
                        _stats["queue_timeouts"] += 1
                    self.log_message("QUEUE-TIMEOUT waited_ms=%d limit_s=%d - engine "
                                     "still busy; retry", waited_ms, QUEUE_TIMEOUT)
                    self.send_error(503, f"Engine busy: queued for {waited_ms} ms "
                                         f"(limit {QUEUE_TIMEOUT} s). Retry.")
                    return
                if waited_ms > 250:
                    _note("queued", wait_ms=waited_ms)
                    self.log_message("QUEUED waited_ms=%d (serialised behind a "
                                     "generation)", waited_ms)
            self._resp_hdrs = []
            conn = HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=UPSTREAM_TIMEOUT)
            try:
                hdrs = self._upstream_headers()
                if want_body is not None and want_body:
                    hdrs["Content-Length"] = str(len(want_body))
                conn.request(method, self.path,
                             body=want_body if method == "POST" else None,
                             headers=hdrs)
                resp = conn.getresponse()
                self._resp_hdrs = [(k, v) for k, v in resp.getheaders()
                                   if k.lower() not in _HOP]
                self._relay(resp, req_body=want_body)
                resp.close()
            finally:
                conn.close()
        except Exception as e:
            try:
                self.send_error(502, f"Bad gateway: {e}")
            except Exception:
                pass   # client already gone; the 502 write itself cannot be fatal
        finally:
            if acquired:
                _slots.release()

    def _read_body(self):
        """Return (bytes, error_string). Handles both CL and chunked framing."""
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        cl = self.headers.get("Content-Length")
        if cl:
            n = int(cl)
            return self.rfile.read(n), None
        if "chunked" in te:
            buf = bytearray()
            while True:
                line = self.rfile.readline()
                if not line:                      # client closed mid-body: never spin
                    return b"", "connection closed during chunked body"
                line = line.strip()
                if not line:
                    continue
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError:
                    return b"", f"bad chunk size {line[:24]!r}"
                if size == 0:
                    self.rfile.readline()      # trailing CRLF after the terminator
                    return bytes(buf), None
                buf.extend(self.rfile.read(size))
                self.rfile.readline()          # CRLF after each chunk
        return b"", "no Content-Length and not chunked"

    def _upstream_headers(self):
        out = {}
        for k, v in self.headers.items():
            if k.lower() not in _HOP:
                out[k] = v
        return out


def main():
    server = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    print(f"auth_proxy: listening on {BIND_HOST}:{BIND_PORT} -> {UPSTREAM_HOST}:{UPSTREAM_PORT} "
          f"(auth required, key configured; single-flight={MAX_CONCURRENT}, "
          f"upstream_timeout={UPSTREAM_TIMEOUT}s, queue_timeout={QUEUE_TIMEOUT}s)",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with _stats_lock:
            print(f"auth_proxy: final stats {json.dumps(_stats)}", flush=True)
        server.server_close()


if __name__ == "__main__":
    main()
