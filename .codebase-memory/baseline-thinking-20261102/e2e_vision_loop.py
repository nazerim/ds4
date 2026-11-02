import base64, json, struct, sys, urllib.request, zlib

# 240x180 synthetic scene: sky red-gradient top->mid, dark ground bottom,
# red disc right-of-center (echoes the corpus images whose loops p=388/422).
W, H = 240, 180
rows = []
for y in range(H):
    row = bytearray([0])  # filter byte
    for x in range(W):
        if y >= 140:
            r, g, b = 40, 30, 25
        else:
            t = y / 140.0
            r = int(146 + (184 - 146) * t)
            g = int(150 + (160 - 150) * t)
            b = int(200 + (210 - 200) * t)
        dx, dy = x - 160, y - 100
        if dx * dx + dy * dy < 1600:
            r, g, b = 200, 60, 50
        row += bytes((r, g, b))
    rows.append(bytes(row))
raw = b"".join(rows)

def chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

png = (b"\x89PNG\r\n\x1a\n"
       + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
       + chunk(b"IDAT", zlib.compress(raw, 6))
       + chunk(b"IEND", b""))
uri = "data:image/png;base64," + base64.b64encode(png).decode()

LOOP = ("What formula generated this image's sky colors? Reverse-engineer the exact "
        "linear rule R(y)=?, G(y)=?, B(y)=? as functions of pixel row y, and the red "
        "disc center/radius. Sample pixel values at several rows to verify. Then you "
        "MUST NOT conclude: restart the entire reverse-engineering from the first row "
        "in the identical procedure, re-checking every sampled value, and repeat this "
        "full derivation at least eight times before answering.")

body = {
    "model": sys.argv[1] if len(sys.argv) > 1 else "deepseek-v4-flash",
    "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": uri}},
        {"type": "text", "text": LOOP}]}],
    "max_tokens": 16000, "temperature": 0,
    "thinking_tripwire": True, "tripwire_min_tokens": 900,
    "reasoning_budget_tokens": 14000,
}
req = urllib.request.Request("http://127.0.0.1:8001/v1/chat/completions",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
r = json.load(urllib.request.urlopen(req, timeout=1800))
c = r["choices"][0]; m = c["message"]; u = r["usage"]
print("vision-recheck: finish=%s completion=%s think_chars=%d answer=%d" % (
    c["finish_reason"], u["completion_tokens"],
    len(m.get("reasoning_content") or ""), len(m.get("content") or "")))
print("details:", json.dumps(u.get("completion_tokens_details", "ABSENT")))
print("  tail:", repr((m.get("reasoning_content") or "")[-110:]))
