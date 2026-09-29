#!/usr/bin/env python3
"""Print the full ancestry of the 08:41-08:51 rung family and of every 49152
sibling, so we can see which conversations exist on disk and where they diverge."""
import glob, os, struct, time

D = "/Volumes/FireCuda520/ds4-kv-qwen"
FIXED, V2, V3 = 48, 24, 44
rows = {}
for p in glob.glob(os.path.join(D, "*.kv")):
    st = os.stat(p)
    with open(p, "rb") as f:
        h = f.read(FIXED)
        if len(h) < FIXED or h[0:3] != b"KVC":
            continue
        ver = h[3]; tokens = struct.unpack_from("<I", h, 8)[0]
        parent, dfrom = "", 0
        if ver == 3:
            f.seek(FIXED + V2); v3 = f.read(V3)
            parent = v3[0:40].decode("ascii", "replace").rstrip("\x00")
            dfrom = struct.unpack_from("<I", v3, 40)[0]
        rows[os.path.basename(p)[:-3]] = dict(tokens=tokens, parent=parent, dfrom=dfrom,
                                              birth=st.st_birthtime, size=st.st_size)

def ancestry(name):
    out, cur, guard = [], name, 0
    while cur and cur in rows and guard < 40:
        r = rows[cur]
        out.append((r["tokens"], cur, r["dfrom"], r["birth"]))
        cur = r["parent"]; guard += 1
    return out

def fmt(name, label=""):
    a = ancestry(name)
    print("%s  name=%.12s depth=%d" % (label, name, len(a)))
    print("    chain (deepest first): " + " <- ".join(
          "%d@%s" % (t, time.strftime("%m-%d/%H:%M", time.localtime(b))) for t, n, d, b in a))

for nm in sorted(rows):
    if nm.startswith("d77ce384"):
        fmt(nm, "=== THE OBJECT RESTORED AT 13:06")

print()
print("=== all objects at 49152, with who sits above them")
kids = {}
for n, r in rows.items():
    kids.setdefault(r["parent"], []).append(n)
for n, r in sorted(rows.items(), key=lambda x: x[1]["birth"]):
    if r["tokens"] != 49152:
        continue
    above = sorted((rows[c]["tokens"], c) for c in kids.get(n, []))
    a = ancestry(n)
    head = a[1][0] if len(a) > 1 else 0
    print("  %.12s birth=%s anc@=%d  children=%s" %
          (n, time.strftime("%m-%d %H:%M", time.localtime(r["birth"])), head,
           ", ".join("%d(%.6s)" % (t, c) for t, c in above) or "NONE"))

print()
print("=== ancestry of the 08:41-08:51 family")
for pref in ("7bb043cf", "c0bf99c6"):
    for nm in rows:
        if nm.startswith(pref):
            fmt(nm, "---")
print()
print("=== which 49152 object is an ancestor of the deepest pre-restart chain?")
best = max((r["tokens"], n) for n, r in rows.items() if r["tokens"] <= 140000)
for n in [best[1]]:
    a = ancestry(n)
    print("  deepest<=140k: tokens=%d %.12s" % (best[0], n))
    for t, nm, d, b in a:
        if t == 49152:
            print("    passes through 49152 object %.12s (birth %s)" %
                  (nm, time.strftime("%m-%d %H:%M", time.localtime(b))))
