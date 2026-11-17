#!/usr/bin/env python3
"""Census KV checkpoints for files whose header token count contradicts their payload.

Why this exists: the FIX 1 analysis quoted "~89 GiB of doomed resident files" from
the audit's own dir walk, which the fix author explicitly did not reproduce. This
script measures it from the files themselves, so the figure is ours.

The rule it tests is the loader's own. A store writes a payload holding the whole
live session (ds4.c: `const uint32_t rows = s->checkpoint.len;`) while the KV header
records whatever length the caller asked for. On load, `ds4_kvstore.c` accepts the
file only if `loaded_tokens->len == hdr.tokens`. So a header that claims fewer
tokens than the payload carries can never load: the file is write-only.

Offsets are derived from the writer, not guessed, and the payload magic is checked
at the computed position. Files whose magic does not verify are reported as
`unknown` rather than silently classified -- a wrong offset model shows up as a
high unknown count instead of a plausible-looking wrong answer.

Layout (ds4_kvstore.c store path, ds4.c payload header):
    [0,48)              fixed header   : version=byte 3, tokens=u32@8,
                                         payload_bytes=u64@40
    [48, 48+extra)      version extras : v1=0, v2=24, v3=68 bytes
    [+0,+4)             text-size word (u32)
    [+4, +4+text_len)   prompt text
    payload             13 u32 fields; magic=0x34565344 "DSV4" at [0],
                        rows (live session length) at field 7

Usage: python3 kv_doomed_census.py [DIR]   (default /Volumes/FireCuda520/ds4-kv-qwen)
"""
import os
import struct
import sys
from collections import defaultdict

DIR = sys.argv[1] if len(sys.argv) > 1 else "/Volumes/FireCuda520/ds4-kv-qwen"

PAYLOAD_MAGIC = 0x34565344          # "DSV4", ds4.h DS4_SESSION_PAYLOAD_MAGIC
PAYLOAD_FIELDS = 13                 # DS4_SESSION_PAYLOAD_U32_FIELDS
FIXED_HEADER = 48
EXTRA = {1: 0, 2: 24, 3: 68}        # kv_header_extra()


def classify(path):
    """Return (verdict, hdr_tokens, payload_rows, size) or (None, reason, ...)."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(FIXED_HEADER + 68 + 4)
        if len(head) < FIXED_HEADER + 4:
            return "truncated", None, None, size
        version = head[3]
        if version not in EXTRA:
            return f"bad-version-{version}", None, None, size
        hdr_tokens = struct.unpack_from("<I", head, 8)[0]
        base = FIXED_HEADER + EXTRA[version]
        text_len = struct.unpack_from("<I", head, base)[0]
        payload_off = base + 4 + text_len
        fh.seek(payload_off)
        ph = fh.read(PAYLOAD_FIELDS * 4)
        if len(ph) < PAYLOAD_FIELDS * 4:
            return "payload-short", hdr_tokens, None, size
        fields = struct.unpack("<13I", ph)
        if fields[0] != PAYLOAD_MAGIC:
            # offset model or file is not what we think: never guess a verdict
            return "unknown-magic", hdr_tokens, None, size
        rows = fields[7]
        if rows == hdr_tokens:
            return "loadable", hdr_tokens, rows, size
        if hdr_tokens < rows:
            return "doomed-short-header", hdr_tokens, rows, size
        return "header-ahead", hdr_tokens, rows, size


def main():
    if not os.path.isdir(DIR):
        print(f"not a directory: {DIR}", file=sys.stderr)
        return 1
    files = [os.path.join(DIR, n) for n in sorted(os.listdir(DIR))
             if n.endswith(".kv") and os.path.isfile(os.path.join(DIR, n))]
    tally = defaultdict(int)
    bytesum = defaultdict(int)
    gap_worst = (0, None)
    for f in files:
        verdict, hdr, rows, size = classify(f)
        tally[verdict] += 1
        bytesum[verdict] += size
        if verdict == "doomed-short-header" and rows - hdr > gap_worst[0]:
            gap_worst = (rows - hdr, os.path.basename(f))
    total = sum(bytesum.values())
    print(f"census of {DIR}")
    print(f"{len(files)} files, {total / 2**30:.2f} GiB resident\n")
    print(f"{'verdict':<22}{'files':>7}{'GiB':>9}{'% of bytes':>12}")
    for v in sorted(tally, key=lambda x: -bytesum[x]):
        print(f"{v:<22}{tally[v]:>7}{bytesum[v] / 2**30:>9.2f}"
              f"{100.0 * bytesum[v] / total:>11.1f}%")
    print(f"\nlargest header/payload gap: {gap_worst[0]} tokens ({gap_worst[1]})")
    unk = tally.get("unknown-magic", 0) + tally.get("payload-short", 0)
    if unk:
        print(f"WARNING: {unk} files unclassified -- the offset model did not hold for "
              f"them, so treat the totals as partial.")
    else:
        print("offset model verified by payload magic on every file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
