#!/usr/bin/env python3
"""Report whether ds4's chat markers are registered CONTROL (special) tokens in a GGUF.

Uses tests/gguf_tokenizer.py's verified metadata reader. Read-only.
Builds marker strings from code points so this file stays pure ASCII.
"""
import sys

sys.argv = ["gt", sys.argv[1] if len(sys.argv) > 1 else "gguf/Qwen3.8-Flash-Next-Q4.gguf", "dump", "0", "0"]
g = {}
exec(compile(open("tests/gguf_tokenizer.py").read(), "gt", "exec"), g)
md = g["read_gguf_metadata"](sys.argv[1])

toks = md.get("tokenizer.ggml.tokens") or []
tt = md.get("tokenizer.ggml.token_type") or []
NAMES = {1: "NORMAL", 2: "UNKNOWN", 3: "CONTROL(special)", 4: "USER", 5: "DEFINED", 6: "PAD"}
FW, UL, SP, VN = chr(0xFF5C), chr(0x2581), " ", chr(0x7C)   # fullwidth bar, meta underscore, space, vertical bar

CAND = {
    "qwen spaced im_end":        "<" + SP + VN + "im_end" + VN + SP + ">",
    "qwen spaced im_start":      "<" + SP + VN + "im_start" + VN + SP + ">",
    "qwen plain im_end":         "<|" + "im_end" + "|>",
    "qwen plain im_start":       "<|" + "im_start" + "|>",
    "dsml end-of-sentence":      "<" + FW + "end" + UL + "of" + UL + "sentence" + FW + ">",
    "dsml User":                 "<" + FW + "User" + FW + ">",
    "dsml Assistant":            "<" + FW + "Assistant" + FW + ">",
    "dsml begin-of-sentence":    "<" + FW + "begin" + UL + "of" + UL + "sentence" + FW + ">",
    "deepseek think":            "<think>",
}

print("vocab=%d token_type entries=%d" % (len(toks), len(tt)))
if len(toks) != len(tt):
    print("MISMATCH: cannot map ids to types")
    sys.exit(1)

tally = {}
for t in tt:
    tally[t] = tally.get(t, 0) + 1
print("token_type histogram:", {NAMES.get(k, k): v for k, v in sorted(tally.items())})
print("'tokenizer.ggml.added_tokens' present:", "tokenizer.ggml.added_tokens" in md)
print()

index = {}
for i, t in enumerate(toks):
    index.setdefault(t, i)

for label, s in CAND.items():
    i = index.get(s)
    if i is None:
        print("  %-24s %-28r -> absent from vocab: tokenises as ordinary TEXT" % (label, s))
    else:
        print("  %-24s %-28r -> id=%d type=%s" % (label, s, i, NAMES.get(tt[i], tt[i])))
