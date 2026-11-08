#!/usr/bin/env python3
"""Per-request DSpark speculation collector (server lane, DS4_DSPARK_STATS=1).

Usage: python3 ds41_collect_spec.py log/ds4-v41.log [more.log ...]

Reads rotated leg logs (boot-restart rotation semantics: leg data may span
several files) and reports:
  - every 'dspark req:' delta as a machine-parsed row (ctx span, cycles,
    proposed, accepted, accept_rate, spec_ms)
  - aggregate: requests seen, speculating share, weighted accept rate,
    avg accepted/cycle
  - decode-rate comparison medians (artifact-proof 'avg=' field) for
    speculating vs non-speculating requests, segmented by prompt start ->
    finish spans (see README normalization rules)
Correlating a request with its spec rows: both lines carry 'ctx=<span>' and
'req:' lines follow their request's final 'finish=' line in log order.
"""
import re
import statistics
import sys

SPEC_RE = re.compile(
    r'^\d+ (\d{2}:\d{2}:\d{2}) ds4-server: (chat|completion) ctx=(\S+) '
    r'dspark req: cycles=(\d+) proposed=(\d+) accepted_draft=(\d+) '
    r'accept_rate=([\d.]+)% avg_accept=([\d.]+).*spec_ms=([\d.]+)')
FIN_RE = re.compile(
    r'^\d+ \d{2}:\d{2}:\d{2} ds4-server: (chat|completion) ctx=(\S+) .*gen=(\d+).*finish=(\w+).*([0-9.]+)s\s*$')
DEC_RE = re.compile(
    r'^\d+ \d{2}:\d{2}:\d{2} .*gen=\d+ \S+ .*(?:TOOLS )?(?:THINKING )?'
    r'decoding chunk=[\d.]+ t/s avg=([\d.]+)')


def collect(paths):
    rows = []          # spec rows
    fin = []           # (ctx, gen, toolish?) from finish lines
    dec = []           # (ctx, avg) decode samples
    ctx = None         # current request's ctx span for decode segmentation
    for p in paths:
        try:
            fh = open(p, errors='ignore')
        except OSError:
            continue
        with fh:
            for line in fh:
                m = SPEC_RE.search(line)
                if m:
                    rows.append({'t': m.group(1), 'kind': m.group(2),
                                 'ctx': m.group(3), 'cycles': int(m.group(4)),
                                 'proposed': int(m.group(5)),
                                 'accepted': int(m.group(6)),
                                 'rate': float(m.group(7)),
                                 'avg_acc': float(m.group(8)),
                                 'ms': float(m.group(9))})
                    continue
                m = re.match(r'^\d+ \d{2}:\d{2}:\d{2} ds4-server: chat ctx=(\S+) .*(?:prompt start)', line)
                if m:
                    ctx = m.group(1)
                    continue
                m = FIN_RE.search(line)
                if m:
                    fin.append((m.group(2), int(m.group(3))))
                    continue
                m = DEC_RE.search(line)
                if m and ctx:
                    dec.append((ctx, float(m.group(1))))
    return rows, dec


def main(paths):
    rows, dec = collect(paths)
    spec = [r for r in rows if r['cycles'] > 0]
    zero = [r for r in rows if r['cycles'] == 0]
    print(f'requests w/ telemetry: {len(rows)}  speculating: {len(spec)}  '
          f'zero-cycles: {len(zero)}')
    if spec:
        cyc = sum(r['cycles'] for r in spec)
        prop = sum(r['proposed'] for r in spec)
        acc = sum(r['accepted'] for r in spec)
        print(f'aggregate: cycles={cyc} proposed={prop} accepted={acc}  '
              f'weighted accept={100.0*acc/prop:.1f}%  '
              f'accepted/cycle={acc/cyc:.3f}')
        print('top spec rows:')
        for r in sorted(spec, key=lambda r: -r['accepted'])[:10]:
            print(f"  {r['t']} ctx={r['ctx'][:18]:18s} cycles={r['cycles']:5d} "
                  f"prop={r['proposed']:6d} acc={r['accepted']:6d} "
                  f"rate={r['rate']:5.1f}% spec_ms={r['ms']:8.0f}")
    print('per-request detail (last 20):')
    for r in rows[-20:]:
        print(f"  {r['t']} ctx={r['ctx'][:18]:18s} cycles={r['cycles']:5d} "
              f"prop={r['proposed']:6d} acc={r['accepted']:6d} rate={r['rate']:5.1f}%")
    # decode-rate comparison, artifact-proof avg= field only
    spec_ctx = {r['ctx'] for r in spec}
    zero_ctx = {r['ctx'] for r in zero}
    svals = [v for c, v in dec if c in spec_ctx]
    zvals = [v for c, v in dec if c in zero_ctx]
    if svals and zvals:
        print(f'decode avg= medians: speculating {statistics.median(svals):.2f} '
              f'(n={len(svals)})  zero-cycles {statistics.median(zvals):.2f} '
              f'(n={len(zvals)})  ratio {statistics.median(svals)/statistics.median(zvals):.3f}')
    elif svals or zvals:
        vals = svals or zvals
        print(f'decode avg= median ({len(svals) and "spec" or "zero"} only): '
              f'{statistics.median(vals):.2f} (n={len(vals)})')


if __name__ == '__main__':
    main(sys.argv[1:] or ['log/ds4-v41.log'])
