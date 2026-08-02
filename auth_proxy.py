#!/usr/bin/env python3
"""Auth-gated reverse proxy for ds4-server.

Listens on BIND_HOST:BIND_PORT, requires `Authorization: Bearer $DS4_API_KEY`,
and forwards to UPSTREAM_HOST:UPSTREAM_PORT (the loopback ds4-server). Relays
streaming (SSE/chunked) responses without buffering. Uses only the stdlib.

Env:
  DS4_API_KEY   required  - the bearer token clients must present
  UPSTREAM_HOST default 127.0.0.1 (ds4-server stays on loopback)
  UPSTREAM_PORT default 8001
  BIND_HOST     default 0.0.0.0   (the LAN address clients hit)
  BIND_PORT     default 8002
"""
import os
import sys
import hmac
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.client import HTTPConnection

AUTH_KEY = os.environ.get("DS4_API_KEY", "")
UPSTREAM_HOST = os.environ.get("UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("UPSTREAM_PORT", "8001"))
BIND_HOST = os.environ.get("BIND_HOST", "0.0.0.0")
BIND_PORT = int(os.environ.get("BIND_PORT", "8002"))

if not AUTH_KEY:
    print("auth_proxy: DS4_API_KEY is required (refusing to start unauthenticated)", file=sys.stderr)
    sys.exit(1)

# Headers we do not forward to the upstream (hop-by-hop / framing).
_HOP = {"connection", "keep-alive", "proxy-connection", "transfer-encoding",
        "content-length", "upgrade", "te", "host"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        return hmac.compare_digest(auth[7:], AUTH_KEY)

    def _deny(self):
        self.send_error(401, "Unauthorized")

    def _relay(self, resp):
        # Status line + headers (drop framing/hop-by-hop, re-add our own).
        status = f"{resp.status} {resp.reason or ''}"
        clen = resp.getheader("Content-Length")
        self.wfile.write(f"HTTP/1.1 {status}\r\n".encode("ascii"))
        for k, v in resp.getheaders():
            if k.lower() in _HOP:
                continue
            self.wfile.write(f"{k}: {v}\r\n".encode("ascii"))
        if clen is not None:
            self.wfile.write(f"Content-Length: {clen}\r\n".encode("ascii"))
        else:
            self.wfile.write(b"Transfer-Encoding: chunked\r\n")
        self.wfile.write(b"\r\n")
        self.wfile.flush()

        if clen is not None:
            remaining = int(clen)
            while remaining > 0:
                chunk = resp.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                self.wfile.write(chunk)
            self.wfile.flush()
        else:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                self.wfile.write(chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    def do_GET(self):
        if not self._authorized():
            self._deny()
            return
        conn = HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=600)
        try:
            conn.request("GET", self.path, headers=self._upstream_headers())
            resp = conn.getresponse()
            self._relay(resp)
            resp.close()
        except Exception as e:
            self.send_error(502, f"Bad gateway: {e}")
        finally:
            conn.close()

    def do_POST(self):
        if not self._authorized():
            self._deny()
            return
        body = self._read_body()
        conn = HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=600)
        try:
            conn.request("POST", self.path, body=body, headers=self._upstream_headers())
            resp = conn.getresponse()
            self._relay(resp)
            resp.close()
        except Exception as e:
            self.send_error(502, f"Bad gateway: {e}")
        finally:
            conn.close()

    def do_OPTIONS(self):
        # CORS preflight: forward without auth so clients can discover the endpoint.
        conn = HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=600)
        try:
            conn.request("OPTIONS", self.path, headers=self._upstream_headers())
            resp = conn.getresponse()
            self._relay(resp)
            resp.close()
        except Exception as e:
            self.send_error(502, f"Bad gateway: {e}")
        finally:
            conn.close()

    def _read_body(self):
        cl = self.headers.get("Content-Length")
        if cl:
            return self.rfile.read(int(cl))
        return b""

    def _upstream_headers(self):
        out = {}
        for k, v in self.headers.items():
            if k.lower() not in _HOP:
                out[k] = v
        return out


def main():
    server = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    print(f"auth_proxy: listening on {BIND_HOST}:{BIND_PORT} -> {UPSTREAM_HOST}:{UPSTREAM_PORT} "
          f"(auth required, key configured)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
