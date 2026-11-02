#!/usr/bin/env python3
"""GGUF v2/v3 metadata + tensor-info dumper (handles nested arrays)."""
import struct, sys, collections

TYPES = {0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'F',7:'B',8:'s',9:'A',10:'Q',11:'q',12:'d'}
SCALAR = {'B':(1,'<B'),'b':(1,'<b'),'H':(2,'<H'),'h':(2,'<h'),'I':(4,'<I'),
          'i':(4,'<i'),'F':(4,'<f'),'Q':(8,'<Q'),'q':(8,'<q'),'d':(8,'<d')}

class R:
    def __init__(self, f): self.f = f
    def raw(self, n):
        d = self.f.read(n)
        if len(d) != n: raise EOFError
        return d
    def u32(self): return struct.unpack('<I', self.raw(4))[0]
    def u64(self): return struct.unpack('<Q', self.raw(8))[0]
    def str(self):
        n = self.u64(); return self.raw(n).decode('utf8', 'replace')
    def scalar(self, code):
        sz, fmt = SCALAR[code]
        return struct.unpack(fmt, self.raw(sz))[0]
    def value(self, t):
        if t == 8: return self.str()
        if t == 9:
            et = self.u32(); n = self.u64()
            return [self.value(et) for _ in range(min(n, 2_000_000))]
        code = TYPES[t]
        return self.scalar(code)

def main(path, filter_kw=None, dump_tensors=False):
    with open(path, 'rb') as f:
        r = R(f)
        if r.raw(4) != b'GGUF': sys.exit('not GGUF')
        ver = r.u32()
        n_tensors = r.u64(); n_kv = r.u64()
        print(f'version={ver} n_tensors={n_tensors} n_kv={n_kv}')
        kv = {}
        for _ in range(n_kv):
            k = r.str(); t = r.u32(); v = r.value(t)
            if isinstance(v, list) and v and isinstance(v[0], list):
                v = [v[0][:8], '...'] if v[0] else v
            kv[k] = v
            if filter_kw is None or any(w in k.lower() for w in filter_kw):
                s = str(v)
                print(f'  {k} = {s[:160]}')
        if dump_tensors:
            dtypes = collections.Counter(); groups = collections.Counter()
            named = []
            for _ in range(n_tensors):
                name = r.str(); nd = r.u32()
                dims = [r.u64() for _ in range(nd)]
                dt = r.u32(); off = r.u64()
                dtypes[dt] += 1
                named.append((name, dt, dims))
                parts = name.split('.')
                g = '.'.join(p for p in parts if not p.isdigit())
                groups[g] += 1
            print('\ntensor dtypes (code:count):', dict(dtypes))
            print(f'\n{len(groups)} tensor-name groups; top 40 by count:')
            for g, c in groups.most_common(40):
                print(f'  {c:6d}  {g}')
            # --dims: full name x shape listing
            if '--show-names' in sys.argv:
                print('\nfull tensor list (name | dtype | dims):')
                for name, dt, dims in named:
                    print(f'  {name:<48} dt={dt:<3} {dims}')
            return kv, dtypes, groups
        return kv

if __name__ == '__main__':
    path = sys.argv[1]
    filt = sys.argv[2].split(',') if len(sys.argv) > 2 and sys.argv[2] != '-' else None
    dump_tensors = '-t' in sys.argv
    main(path, filt, dump_tensors)
