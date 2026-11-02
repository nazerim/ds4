#!/usr/bin/env python3
"""Text-level audit of the `plain` bucket in DS4_TRAIN_DUMP harvests.

Usage: tools/td_textclass.py <corpus-dir> [<corpus-dir> ...]
       [--gguf PATH]  (default ds4flash.gguf; token table cached to a
                      sibling td_tokens.json next to this script's tmpdir)

Decodes each plain-class record's window (seed + resolved lookahead labels)
to surface text via the model's own vocab pieces, classifies the snippet,
and reports per-class share, mean cycle-gap (drafting engagement proxy:
gap 1 = seed-only = no drafts committed; gap >= 4 = healthy drafting) and
gap==1 fraction. Answers "where is the drafter actually weak?" at the
content level — the review's open todo (structural boundaries were found
STRONG; residual weak classes live inside the ~99% plain bucket).

Window = up to 6 tokens, so per-record classification is heuristic/noisy;
aggregate shares are the signal. Classes evaluated in rule order.
"""
import glob
import json
import os
import re
import struct
import sys
import tempfile

REC = struct.Struct("<IiBBBB5i")
SZ = REC.size + 12288

DSML = 128825
TOOL = set(range(128806, 128815)) | {128829}
THINK = {128821, 128822}
FILESYS = set(range(128815, 128821))
ROLE = {128803, 128804, 128805, 128826, 128827, 128828}
PLACEHOLDER = {128823, 128824}
ALLCTL = {DSML} | TOOL | THINK | FILESYS | ROLE | PLACEHOLDER

CACHE = os.path.join(tempfile.gettempdir(), "opencode", "td_tokens.json")


def load_tokens(gguf):
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    f = open(gguf, "rb")

    def rd_str():
        n = struct.unpack("<Q", f.read(8))[0]
        return f.read(n).decode("utf8", "replace")

    def read_val(t):
        # ds4 GGUF dialect: bool=7, STRING=8, ARRAY=9, u64=10, i64=11, f64=12
        if t in (0, 1, 7):
            return f.read(1)[0]
        if t in (2, 3):
            return struct.unpack("<H", f.read(2))[0]
        if t in (4, 5):
            return struct.unpack("<I", f.read(4))[0]
        if t == 6:
            return struct.unpack("<f", f.read(4))[0]
        if t == 8:
            return rd_str()
        if t in (10, 11):
            return struct.unpack("<Q", f.read(8))[0]
        if t == 12:
            return struct.unpack("<d", f.read(8))[0]
        if t == 9:
            at = struct.unpack("<I", f.read(4))[0]
            n = struct.unpack("<Q", f.read(8))[0]
            return [read_val(at) for _ in range(n)]
        raise ValueError(t)

    assert f.read(4) == b"GGUF"
    f.read(4)
    f.read(8)
    nkv = struct.unpack("<Q", f.read(8))[0]
    tokens = None
    for _ in range(nkv):
        k = rd_str()
        t = struct.unpack("<I", f.read(4))[0]
        v = read_val(t)
        if k == "tokenizer.ggml.tokens":
            tokens = v
            break
    f.close()
    assert tokens, "tokenizer.ggml.tokens not found"
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(tokens, open(CACHE, "w"))
    return tokens


BYTE_RE = re.compile(r"^<0x([0-9a-fA-F]{2})>$")


def decode(ids, tokens):
    out = []
    for t in ids:
        if 0 <= t < len(tokens):
            s = tokens[t]
            m = BYTE_RE.match(s)
            if m:
                out.append(chr(int(m.group(1), 16)))
            else:
                out.append(s.replace("\u0120", " ").replace("\u2581", " ")
                           .replace("\u010a\u010a", "\n\n")
                           .replace("\u010a", "\n"))
        else:
            out.append("\ufffd")
    return "".join(out)


RULES = [
    ("c-family", re.compile(
        r"\b(int|char|void|struct|typedef|sizeof|unsigned|const)\b.*[;{(]"
        r"|->|#include|\bmalloc\(|\bfree\(|\bFILE\b|\bprintf\(|\bmemcpy\b")),
    ("code", re.compile(
        r"\b(def|class|import|return|function|const|let|var|static|async"
        r"|await|public|private|func|impl|pub|fn|SELECT|FROM|export)\b"
        r"|=>|::|\{\}|\(\)|\t|#!/|\bdef \w+\(|\bfi\b|end\b|\|\s*\|"
        r"|;|\{\}|[{}]\n|\n[{}]|:::|\bbash\b|\bgit\b|grep|\.py\b|\.c\b"
        r"|\.sh\b|\bmain\b|\bfunc\b")),
    ("json-args", re.compile(r'^\s*[{"]|\w+"\s*:\s|:\\?"')),
    ("markup-args", re.compile(r'="[^"]*"|\w+="|</\w+>|<\w+ [^>]*=|<｜|｜>|: //')),
    ("markdown", re.compile(r"^\s*#+\s|^\s*[-*]\s+\w|```|^\|.*\||\*\*\w")),
    ("math", re.compile(r"[∑∫≈≤≥±×÷]|\\frac|\\\(|\\\[|\bsum\b|\bmod\b"
                        r"|\bx\b\s*[=+\-*]|\b\d+\s*[\^]\s*\d")),
]


def classify_text(w):
    letters = sum(c.isalpha() for c in w)
    syms = sum(not (c.isalnum() or c.isspace()) for c in w)
    for name, rx in RULES:
        if rx.search(w):
            return name
    if w and letters / len(w) > 0.70 and syms / max(len(w), 1) < 0.06:
        return "prose"
    return "other"


def main():
    dirs = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--gguf" in sys.argv:
        gguf = sys.argv[sys.argv.index("--gguf") + 1]
    else:
        gguf = "ds4flash.gguf"
    tokens = load_tokens(gguf)
    tot = {}
    gap1 = {}
    gapsum = {}
    gapn = {}
    samples = {}
    for d in dirs:
        for rp in sorted(glob.glob(os.path.join(d, "train_pid*.recs"))):
            data = open(rp, "rb").read()
            n = len(data) // SZ
            for i in range(n):
                r = REC.unpack_from(data, i * SZ)
                pos, seed, streak, nlab = r[0], r[1], r[2], r[3]
                labels = r[6:11]
                if nlab == 0 or streak >= 250:
                    continue
                # window = labels[0..nlab-1] (labels[0]==seed by
                # construction: do NOT prepend seed, it doubles the text)
                win = list(labels[:nlab])
                if not win:
                    win = [seed]
                if seed in ALLCTL or any(t in ALLCTL for t in win):
                    continue          # structural classes live in td_classify
                c = classify_text(decode(win, tokens))
                tot[c] = tot.get(c, 0) + 1
                gap1[c] = gap1.get(c, 0) + (streak == 1)
                gapsum[c] = gapsum.get(c, 0.0) + streak
                gapn[c] = gapn.get(c, 0) + 1
                samples.setdefault(c, [])
                if len(samples[c]) < 2:
                    samples[c].append(decode(win, tokens))
    usable = sum(tot.values())
    print(f"text classes over plain-bucket windows  (dirs={dirs}, "
          f"scanned records={usable})")
    print(f"{'class':<12} {'recs':>8} {'share':>7} {'mean-gap':>9} "
          f"{'gap==1%':>8}")
    for c in sorted(tot, key=tot.get, reverse=True):
        print(f"{c:<12} {tot[c]:>8} {100*tot[c]/usable:>6.1f}% "
              f"{gapsum[c]/gapn[c]:>9.2f} {100*gap1[c]/gapn[c]:>7.1f}%")
    for c in ("c-family", "code", "prose", "other", "markup-args",
              "json-args", "markdown", "math"):
        if c in samples:
            print(f"\nsample[{c}]: " + " || ".join(repr(s) for s in samples[c]))


main()
