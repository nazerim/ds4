#!/usr/bin/env python3
# GGUF tokenizer reader (no external deps) + token-id window decoder.
# Primary tool for KV-cache drift forensics: decode `first_mismatch_token`
# windows from log/ds4.trace and compare against re-tokenized text.
#
# usage:
#   gguf_tokenizer.py <model.gguf> tokenize "<text>"      # ids, decimal
#   gguf_tokenizer.py <model.gguf> dump START END         # id -> token string
#
# Session tooling 2026-08..09 (replaces /tmp/kvtok.py; /tmp is volatile).
import struct, sys

def read_gguf_metadata(path):
    f = open(path, 'rb'); magic = f.read(4)
    assert magic == b'GGUF', magic
    ver = struct.unpack('<I', f.read(4))[0]
    cnt = '<Q' if ver >= 3 else '<I'
    cn = 8 if ver >= 3 else 4
    n_tensors = struct.unpack(cnt, f.read(cn))[0]
    n_kv = struct.unpack(cnt, f.read(cn))[0]
    T = {0:'u8',1:'i8',2:'u16',3:'i16',4:'u32',5:'i32',6:'f32',7:'bool',8:'str',9:'arr',10:'u64',11:'i64',12:'f64'}
    def rstr():
        n = struct.unpack('<Q', f.read(8))[0]; b = f.read(n)
        try: return b.decode('utf-8')
        except UnicodeDecodeError: return b.decode('latin1')
    def rval(tt):
        c = T[tt]
        if c == 'str': return rstr()
        if c == 'bool': return struct.unpack('<B', f.read(1))[0]
        if c == 'u8': return struct.unpack('<B', f.read(1))[0]
        if c == 'i8': return struct.unpack('<b', f.read(1))[0]
        if c == 'u16': return struct.unpack('<H', f.read(2))[0]
        if c == 'i16': return struct.unpack('<h', f.read(2))[0]
        if c == 'u32': return struct.unpack('<I', f.read(4))[0]
        if c == 'i32': return struct.unpack('<i', f.read(4))[0]
        if c == 'f32': return struct.unpack('<f', f.read(4))[0]
        if c == 'u64': return struct.unpack('<Q', f.read(8))[0]
        if c == 'i64': return struct.unpack('<q', f.read(8))[0]
        if c == 'f64': return struct.unpack('<d', f.read(8))[0]
        if c == 'arr':
            elem = struct.unpack('<I', f.read(4))[0]; n = struct.unpack('<Q', f.read(8))[0]
            return [rval(elem) for _ in range(n)]
        raise ValueError(c)
    md = {}
    for _ in range(n_kv):
        k = rstr(); tt = struct.unpack('<I', f.read(4))[0]; md[k] = rval(tt)
    f.close(); return md

md = read_gguf_metadata(sys.argv[1])
toks = md['tokenizer.ggml.tokens']; pre = md.get('tokenizer.ggml.pre','?')
if sys.argv[2] == 'tokenize':
    import bisect
    text = sys.argv[3].encode('utf-8')
    merges = [tuple(m.split(' ')) if isinstance(m, str) else m
              for m in md.get('tokenizer.ggml.merges', [])]
    special = set(md.get('tokenizer.ggml.tokens.special', []))
    piece2id = {t: i for i, t in enumerate(toks)}
    words = []; i = 0
    while i < len(text):
        if text[i:i+1] == b' ' and i+1 < len(text) and text[i+1:i+2] != b' ':
            j = i+1
            while j < len(text) and text[j:j+1] != b' ': j += 1
            words.append('\u0120' + text[i+1:j].decode('latin1')); i = j
        else:
            j = i
            while j < len(text) and text[j:j+1] != b' ': j += 1
            if j > i: words.append(text[i:j].decode('latin1'))
            i = max(j, i+1)
    out = []
    for ws in words:
        if ws in special:
            out.append(piece2id.get(ws, 0)); continue
        pieces = list(ws)
        while len(pieces) > 1:
            best = None; bestr = None
            for k in range(len(pieces)-1):
                cand = (pieces[k], pieces[k+1])
                try: r = merges.index(cand)
                except ValueError: continue
                if bestr is None or r < bestr: bestr = r; best = k
            if best is None: break
            pieces = pieces[:best] + [pieces[best]+pieces[best+1]] + pieces[best+2:]
        out.extend(piece2id.get(p, 0) for p in pieces)
    print(f'# pre={pre} words={len(words)} ids={len(out)}')
    print(' '.join(map(str, out)))
    sys.exit(0)
lo, hi = int(sys.argv[3]), int(sys.argv[4])
for i in range(lo, hi):
    if i < len(toks):
        t = toks[i]
        print(i, t.replace('\u0120', ' '))
