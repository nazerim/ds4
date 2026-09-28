#!/usr/bin/env python3
"""Verify the trailer's stored span is byte-identical to the span the renderer
put in the key text of the same file -- the property that makes a restart
render-stable once the bootstrap restores it."""
import struct
import sys

path = sys.argv[1]
FIXED, V2, V3 = 48, 24, 44

with open(path, "rb") as f:
    h = f.read(FIXED)
    ver = h[3]
    payload = struct.unpack_from("<Q", h, 40)[0]
    off = FIXED + (V2 if ver in (2, 3) else 0) + (V3 if ver == 3 else 0)
    f.seek(off)
    text_bytes = struct.unpack("<I", f.read(4))[0]
    text = f.read(text_bytes)
    f.seek(off + 4 + text_bytes + payload)
    tk = f.read(16)
    assert tk[0:3] == b"TKF", "expected the fingerprint section first"
    th = f.read(8)
    assert th[0:3] == b"KTM", "no tool map section"
    count = struct.unpack_from("<I", th, 4)[0]
    recs = []
    for _ in range(count):
        il, dl = struct.unpack("<II", f.read(8))
        cid = f.read(il).decode()
        span = f.read(dl)
        recs.append((cid, span))

print("file=%s text_bytes=%d tool_map_count=%d" % (path.split("/")[-1][:12], text_bytes, count))
for cid, span in recs:
    at = text.find(span)
    print("  id=%s span=%dB occurrences_in_key_text=%d first_at=%d" %
          (cid[:26], len(span), text.count(span), at))
    print("    span shape: starts_with_2_newlines=%s ends_with_ws=%s" %
          (span[0:2] == b"\n\n", span[-1:] in (b"\n", b" ", b"\t")))
uniq = {s for _, s in recs}
print("distinct spans=%d  all present verbatim in key text=%s" %
      (len(uniq), all(text.count(s) >= 1 for s in uniq)))
