#!/usr/bin/env python3
"""Per-request DSpark speculation collector (server lane, DS4_DSPARK_STATS=1).

Usage: python3 ds41_collect_spec.py log/ds4-v41.log [more.log ...]

Reads leg logs (boot-restart rotation semantics: a leg may span several
files - pass them oldest..newest) and reports:
  - leg span wall (first prompt start -> last finish) and total-generation
    overall t/s (includes prefill; the cross-leg wall comparable)
  - every 'dspark req:' delta parsed: per-request rows, aggregate cycles /
    proposed / accepted, weighted accept rate, accepted per cycle
  - decode-rate medians from the artifact-proof avg= field, split by
    speculating vs zero-cycles request (see README normalization rules)
"""
import re
import statistics
import sys

TS = r'^\d+ (\d{2}:\d{2}:\d{2}) '
SPEC_RE = re.compile(
    TS + r'ds4-server: (chat|completion) ctx=(\S+) dspark req: '
    r'cycles=(\d+) proposed=(\d+) accepted_draft=(\d+) '
    r'accept_rate=([\d.]+)% avg_accept=([\d.]+).*spec_ms=([\d.]+)')
PROMPT_RE = re.compile(
    TS + r'ds4-server: (?:chat|completion) ctx=\S+ .*prompt start')
FIN_RE = re.compile(
    TS + r'ds4-server: (chat|completion) ctx=(\S+) .*gen=(\d+).*finish=(\w+)'
    r'.*([0-9.]+)s\s*$')
DEC_RE = re.compile(
    TS + r'ds4-server: (?:chat|completion).*gen=\d+ \S+ .*(?:TOOLS )?'
    r'(?:THINKING )?decoding chunk=[\d.]+ t/s avg=([\d.]+)')


def hms(t):
    h, m, s = map(int, t.split(':'))
    return h * 3600 + m * 60 + s


def collect(paths, since=None):
    rows, dec, fin = [], [], []
    t_start = t_end = None
    ctx = None
    for p in paths:
        try:
            fh = open(p, errors='ignore')
        except OSError:
            continue
        with fh:
            for line in fh:
                m = SPEC_RE.search(line)
                if m:
                    if since is not None and hms(m.group(1)) < since:
                        continue
                    rows.append({'t': m.group(1), 'ctx': m.group(3),
                                 'cycles': int(m.group(4)),
                                 'proposed': int(m.group(5)),
                                 'accepted': int(m.group(6)),
                                 'rate': float(m.group(7)),
                                 'avg_acc': float(m.group(8)),
                                 'ms': float(m.group(9))})
                    continue
                m = PROMPT_RE.search(line)
                if m:
                    if since is not None and hms(m.group(1)) < since:
                        continue
                    t = hms(m.group(1))
                    t_start = t if t_start is None else min(t_start, t)
                    t_end = t if t_end is None else max(t_end, t)
                    # ctx span lives in the prompt line: capture it for
                    # decode segmentation (same field order as spec rows)
                    mc = re.search(r'ctx=(\S+)', line)
                    if mc:
                        ctx = mc.group(1)
                    continue
                m = FIN_RE.search(line)
                if m:
                    if since is not None and hms(m.group(1)) < since:
                        continue
                    t = hms(m.group(1))
                    t_end = t if t_end is None else max(t_end, t)
                    fin.append((m.group(3), int(m.group(4))))
                    continue
                m = DEC_RE.search(line)
                if m and ctx:
                    if since is not None and hms(m.group(1)) < since:
                        continue
                    dec.append((ctx, float(m.group(2))))
    return rows, dec, fin, (t_start, t_end)


def main(paths):
    since = None
    if paths and paths[0].startswith('--since'):
        if paths[0] == '--since':
            since = paths[1] if len(paths) > 1 else None
            paths = paths[2:]
        else:  # --since=HH:MM:SS
            since = paths[0].split('=', 1)[1]
            paths = paths[1:]
        if since is not None and not re.match(r'^\d{2}:\d{2}:\d{2}$', since):
            sys.exit("--since needs HH:MM:SS")
    since_s = hms(since) if since else None
    rows, dec, fin, (t0, t1) = collect(paths, since_s)
    spec = [r for r in rows if r['cycles'] > 0]
    zero = [r for r in rows if r['cycles'] == 0]
    gen_total = sum(g for _, g in fin)
    if t0 is not None and t1 is not None and t1 > t0:
        wall = t1 - t0
        print(f'leg span: {wall/60:.1f} min, requests-finished={len(fin)}, '
              f'gen={gen_total} -> {gen_total/wall:.2f} t/s overall wall')
    print(f'requests w/ telemetry: {len(rows)}  speculating: {len(spec)}  '
          f'zero-cycles: {len(zero)}')
    if spec:
        cyc = sum(r['cycles'] for r in spec)
        prop = sum(r['proposed'] for r in spec)
        acc = sum(r['accepted'] for r in spec)
        # cycles>0/proposed=0 rows are confidence-gated attempts (speculate
        # entered but the drafter withheld): keep them counted, avoid /0.
        wacc = f"{100.0*acc/prop:.1f}%" if prop else 'n/a'
        apc = f'{acc/cyc:.3f}' if cyc else 'n/a'
        tv = f'{1+acc/cyc:.2f}' if cyc else 'n/a'
        print(f'aggregate: cycles={cyc} proposed={prop} accepted={acc}  '
              f'weighted accept={wacc}  '
              f'accepted/cycle={apc}  '
              f'~tokens/verify={tv}')
        print('top spec rows:')
        for r in sorted(spec, key=lambda r: -r['accepted'])[:10]:
            print(f"  {r['t']} ctx={r['ctx'][:18]:18s} cycles={r['cycles']:5d} "
                  f"prop={r['proposed']:6d} acc={r['accepted']:6d} "
                  f"rate={r['rate']:5.1f}% spec_ms={r['ms']:8.0f}")
    print('per-request detail (last 24):')
    for r in rows[-24:]:
        print(f"  {r['t']} ctx={r['ctx'][:18]:18s} cycles={r['cycles']:5d} "
              f"prop={r['proposed']:6d} acc={r['accepted']:6d} rate={r['rate']:5.1f}%")
    spec_ctx = {r['ctx'] for r in spec}
    zero_ctx = {r['ctx'] for r in zero}
    svals = [v for c, v in dec if c in spec_ctx]
    zvals = [v for c, v in dec if c in zero_ctx]
    for label, vals in (('speculating', svals), ('zero-cycles', zvals)):
        if vals:
            vals.sort()
            print(f'decode avg= {label}: median {statistics.median(vals):.2f} '
                  f'p90 {vals[int(len(vals)*.9)]:.2f} max {vals[-1]:.2f} '
                  f'n={len(vals)}')
    if svals and zvals:
        print(f'within-leg ratio (spec/zero medians): '
              f'{statistics.median(svals)/statistics.median(zvals):.3f}')


if __name__ == '__main__':
    main(sys.argv[1:] or ['log/ds4-v41.log'])
