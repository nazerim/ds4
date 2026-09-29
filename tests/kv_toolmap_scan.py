#!/usr/bin/env python3
"""Scan ds4 .kv files for the tool-map trailer (verdict fix #2 evidence).

Layout: fixed 48B header (+24 v2, +44 v3), u32 text_bytes, text, payload,
then the trailer.  Trailer header is b"KTM" + version byte + le32 count,
followed by count records of (le32 id_len, le32 dsml_len, id, dsml).
"""
import glob
import os
import struct
import sys

D = sys.argv[1] if len(sys.argv) > 1 else "/Volumes/FireCuda520/ds4-kv-qwen"
FIXED, V2, V3 = 48, 24, 44
# Authoritative map: see tests/kv_reason.py (mirrors ds4_kvstore.h).
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kv_reason import REASON


def read_entry(path, want_ids=3):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        h = f.read(FIXED)
        if len(h) < FIXED or h[0:3] != b"KVC":
            return None
        ver = h[3]
        reason = h[5]
        ext = h[6]
        tokens = struct.unpack_from("<I", h, 8)[0]
        payload = struct.unpack_from("<Q", h, 40)[0]
        off = FIXED + (V2 if ver in (2, 3) else 0) + (V3 if ver == 3 else 0)
        f.seek(off)
        tb = f.read(4)
        if len(tb) < 4:
            return None
        text_bytes = struct.unpack("<I", tb)[0]
        f.seek(off + 4 + text_bytes + payload)
        # Trailer: TKF section (8B header + 8B fingerprint), then the optional
        # tool map, then the optional vision section.
        tk = f.read(16)
        tokfp_ok = len(tk) == 16 and tk[0:3] == b"TKF"
        th = f.read(8)
        count = -1
        ids = []
        next_magic = th[0:3] if len(th) >= 3 else b""
        magic_ok = len(th) == 8 and th[0:3] == b"KTM"
        if magic_ok:
            count = struct.unpack_from("<I", th, 4)[0]
            for _ in range(min(count, want_ids)):
                ln = f.read(8)
                if len(ln) < 8:
                    break
                il, dl = struct.unpack("<II", ln)
                cid = f.read(il).decode("utf8", "replace")
                f.seek(dl, 1)
                ids.append("%s/%dB" % (cid[:22], dl))
    return dict(name=os.path.basename(path)[:12], tokens=tokens, ver=ver,
                reason=REASON.get(reason, str(reason)), ext=ext,
                flag=bool(ext & 1), tokfp=tokfp_ok, nextm=next_magic,
                magic=magic_ok, count=count, ids=ids, mb=size / 1e6)


files = sorted(glob.glob(os.path.join(D, "*.kv")), key=os.path.getmtime)
rows = [r for r in (read_entry(p) for p in files) if r]
flagged = [r for r in rows if r["flag"]]
withmap = [r for r in rows if r["count"] > 0]
print("files=%d  ext_tool_map_flag=%d  tokfp_ok=%d  nonempty_tool_map=%d" %
      (len(rows), len(flagged), sum(1 for r in rows if r["tokfp"]), len(withmap)))
print("--- newest 8 by mtime")
print("name          tokens   ver reason  ext flag tkfp next  count  ids")
for r in rows[-8:]:
    print("%-12s %8d  %d  %-6s %3d %-5s %-4s %-5s %5d  %s" %
          (r["name"], r["tokens"], r["ver"], r["reason"], r["ext"],
           r["flag"], r["tokfp"], r["nextm"], r["count"], ",".join(r["ids"])))
if withmap:
    print("--- all files with a non-empty tool map")
    for r in withmap:
        print("%-12s tokens=%d reason=%s count=%d ids=%s" %
              (r["name"], r["tokens"], r["reason"], r["count"], ",".join(r["ids"])))
