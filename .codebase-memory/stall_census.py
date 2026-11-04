#!/usr/bin/env python3
"""Census the DSML rewind rebuild stalls out of ordinary engine logs.

A rewind that the verify window cannot serve is rebuilt by re-prefilling the
whole kept context with the progress emitter unwired, so the engine prints
nothing while it happens.  It still leaves a fingerprint: the decode progress
line covers a fixed 50-token window, so the rebuild shows up as that chunk's
measured rate collapsing (0.05-0.6 t/s where 30 t/s is normal).  Dividing the
context size by the excess time gives the effective prefill rate, which is the
independent check that these events are full re-prefills rather than scheduler
or memory hiccups.

Usage:
    python3 stall_census.py log/ds4.log*
    python3 stall_census.py --threshold 1.0 --normal 30 log/ds4.log*

Exit code is the number of stall chunks found (capped at 255), so it composes
in a shell test.
"""
import argparse
import glob
import re
import statistics
import sys

DEC = re.compile(
    r'^(?P<day>\d+) (?P<time>\d\d:\d\d:\d\d) ds4-server: (?P<kind>\w+) '
    r'ctx=(?P<start>\d+)\.\.(?P<end>\d+):(?P<span>\d+)'
    r' gen=(?P<gen>\d+)(?P<flags>.*?) decoding chunk=(?P<rate>[\d.]+) t/s '
    r'avg=(?P<avg>[\d.]+) t/s (?P<elapsed>[\d.]+)s')


def census(paths, threshold, normal):
    events = []
    for path in paths:
        with open(path, errors='replace') as fp:
            for line in fp:
                m = DEC.search(line)
                if not m:
                    continue
                rate = float(m.group('rate'))
                if rate >= threshold:
                    continue
                ctx = int(m.group('end'))
                # The chunk covered 50 tokens; the excess over normal decode is
                # the time the rebuild stole.
                stall = max(0.0, 50.0 / rate - 50.0 / normal)
                events.append({
                    'file': path, 'day': m.group('day'), 'time': m.group('time'),
                    'ctx': ctx, 'gen': int(m.group('gen')),
                    'flags': (m.group('flags') or '').strip(),
                    'rate': rate, 'stall_s': stall,
                    'implied_tps': ctx / stall if stall > 0 else 0.0,
                })
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='*', default=['log/ds4.log*'])
    ap.add_argument('--threshold', type=float, default=1.0,
                    help='t/s below which a decode chunk counts as stalled')
    ap.add_argument('--normal', type=float, default=30.0,
                    help='undisturbed decode rate, used to subtract real decode time')
    ap.add_argument('--show', type=int, default=0, help='print N worst events')
    args = ap.parse_args()

    paths = []
    for pattern in args.paths:
        paths.extend(sorted(glob.glob(pattern))) or [pattern]
    events = census(paths, args.threshold, args.normal)
    if not events:
        print(f"no stall chunks in {len(paths)} logs")
        return 0

    total = sum(e['stall_s'] for e in events) / 60.0
    rates = sorted(e['implied_tps'] for e in events if e['stall_s'] > 20)
    print(f"logs scanned: {len(paths)}   stall chunks: {len(events)}   "
          f"GPU time lost: {total:.1f} min")
    print(f"median stall {statistics.median(e['stall_s'] for e in events):.0f}s   "
          f"max {max(e['stall_s'] for e in events):.0f}s")
    if rates:
        print("implied rebuild rate (ctx/stall_s) - compare with measured prefill "
              "t/s on the same box:")
        print(f"  median {statistics.median(rates):.0f} t/s   "
              f"p10 {rates[len(rates)//10]:.0f}   "
              f"p90 {rates[len(rates)*9//10]:.0f}   n={len(rates)}")
    print(f"stall ctx {min(e['ctx'] for e in events)}-"
          f"{max(e['ctx'] for e in events)}, gen "
          f"{min(e['gen'] for e in events)}-{max(e['gen'] for e in events)}")

    per_file = {}
    for e in events:
        per_file[e['file']] = per_file.get(e['file'], 0.0) + e['stall_s']
    print("\nper-boot minutes lost:")
    for path, sec in sorted(per_file.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {sec/60:6.1f} min  {path}")

    if args.show:
        print(f"\nworst {args.show}:")
        for e in sorted(events, key=lambda e: -e['stall_s'])[:args.show]:
            print(f"  {e['day']} {e['time']} ctx={e['ctx']} gen={e['gen']} "
                  f"chunk={e['rate']} t/s stall={e['stall_s']:.0f}s "
                  f"implied={e['implied_tps']:.0f} t/s [{e['flags']}] {e['file']}")
    return min(len(events), 255)


if __name__ == '__main__':
    sys.exit(main())
