#!/usr/bin/env python3
"""ds41_watch_leg.py — long-run leg monitor for the v41 server lane.

Usage: python3 ds41_watch_leg.py [--log log/ds4-v41.log] [--out /tmp/leg.snap]
       [--interval 60] [--quiet 1200] [--max-hours 4.5]

One snapshot line per interval to --out (human-tailable) plus a final verdict
block. Tracks, WITHOUT touching the server:
  - engine liveness + RSS (pgrep/ps) and swap (sysctl)
  - finished requests / total gen / finish=error count (log deltas)
  - dspark req deltas: cycles/proposed/accepted -> weighted accept rate
  - max context position reached (ctx=lo..pos depth field)
  - decode avg= samples: overall + last-15-min median (regression tripwire:
    the fixed substrate is flat-in-age; a decaying recent-median means the
    transient-reap guard should be checked FIRST, per HANDOFF §7)
  - boot changes: a new 'listening on' with a different ctx means someone
    restarted the lane (e.g. losing V41_CTX=524288) -> flagged in snapshots
Exits: --max-hours cap, or after --quiet seconds with no finish line once
at least 20 requests completed.
"""
import argparse
import re
import subprocess
import sys
import time

SPEC = re.compile(r'dspark req: cycles=(\d+) proposed=(\d+) accepted_draft=(\d+)')
FIN = re.compile(r'gen=(\d+).*finish=(\w+)')
CTX = re.compile(r'ctx=\d+\.\.(\d+)')
DEC = re.compile(r'decoding chunk=[\d.]+ t/s avg=([\d.]+)')
LISTEN = re.compile(r'listening on')
CTXBUF = re.compile(r'\(ctx=(\d+)\)')


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=15).stdout
    except Exception:
        return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--log', default='log/ds4-v41.log')
    ap.add_argument('--out', default='/tmp/ds41_leg.snap')
    ap.add_argument('--interval', type=int, default=60)
    ap.add_argument('--quiet', type=int, default=1200)
    ap.add_argument('--max-hours', type=float, default=4.5)
    ap.add_argument('--stall', type=int, default=180)
    a = ap.parse_args()
    deadline = time.time() + a.max_hours * 3600
    pos = 0
    reqs = errs = gen = 0
    cyc = prop = acc = 0
    maxctx = 0
    dec_all = []
    dec_recent = []
    t_last_fin = time.time()
    boots = 0
    nstall = 0
    inflight = 0
    t_last_log = time.time()
    ctx_line = 'n/a'
    f = open(a.log, errors='ignore')
    f.seek(0, 2)
    with open(a.out, 'w') as snap:
        snap.write(f'# leg monitor started {time.strftime("%H:%M:%S")} '
                   f'log={a.log} cap={a.max_hours}h quiet={a.quiet}s\n')
        snap.flush()
        while time.time() < deadline:
            time.sleep(a.interval)
            new = f.readlines()
            if new:
                t_last_log = time.time()
            for line in new:
                m = SPEC.search(line)
                if m:
                    cyc += int(m.group(1)); prop += int(m.group(2)); acc += int(m.group(3))
                m = FIN.search(line)
                if m:
                    reqs += 1; gen += int(m.group(1))
                    if m.group(2) == 'error':
                        errs += 1
                    t_last_fin = time.time()
                    inflight = max(0, inflight - 1)
                if 'client disconnected' in line:
                    # disconnects end a request WITHOUT a finish= line; count
                    # them in inflight/quiet bookkeeping or the STALL tripwire
                    # fires ghosts at a healthy idle engine (2026-11-10 21:27,
                    # exact round: alert while main thread parked in accept()).
                    inflight = max(0, inflight - 1)
                    t_last_fin = time.time()
                if 'prompt start' in line:
                    inflight += 1
                m = CTX.search(line)
                if m:
                    maxctx = max(maxctx, int(m.group(1)))
                m = DEC.search(line)
                if m:
                    v = float(m.group(1))
                    dec_all.append(v); dec_recent.append((time.time(), v))
                if LISTEN.search(line):
                    boots += 1
                m = CTXBUF.search(line)
                if m:
                    ctx_line = m.group(1)
            dec_recent = [(t, v) for t, v in dec_recent if t > time.time() - 900]
            pid = sh('pgrep -x ds4-server | tail -1').strip()
            rss = swap = 'DEAD'
            if pid:
                r = sh(f'ps -o rss= -p {pid}').strip()
                rss = f'{int(r) / 1048576:.1f}G' if r else '?'
                sw = re.search(r'used = ([\d.]+)M', sh('sysctl -n vm.swapusage'))
                swap = f'{float(sw.group(1)) / 1024:.1f}G' if sw else '?'
            import statistics as st
            dall = f'{st.median(dec_all):.1f}' if dec_all else '-'
            drec = f'{st.median([v for _, v in dec_recent]):.1f}' if dec_recent else '-'
            wacc = f'{100.0 * acc / prop:.1f}%' if prop else '-'
            # stall tripwire: request in flight + log silent + engine burning no
            # CPU = wedged kernel wait (2026-11-09 pread-hang signature: stuck
            # pread in expert-stream pool froze the whole lane at 0.0% cpu).
            silence = time.time() - t_last_log
            alert = ''
            if pid and inflight > 0 and silence > a.stall:
                cpu = float(sh(f'ps -o %cpu= -p {pid}').strip() or 99)
                if cpu < 1.0:
                    nstall += 1
                    leaf = sh(f'sample {pid} 1 2>/dev/null | grep -m1 -E '
                              '"pread|waitUntilCompleted|psynch" | cut -c1-72')
                    alert = f' !! STALL sil={int(silence)}s inflight={inflight} ' \
                            f'cpu={cpu:.1f} leaf=[{leaf.strip()}]'
                    try:
                        subprocess.run(['osascript', '-e',
                                        f'display notification "engine silent '
                                        f'{int(silence)}s, {inflight} inflight" '
                                        f'with title "ds4 leg STALL"'], timeout=10)
                    except Exception:
                        pass
                    t_last_log = time.time()  # re-arm; alert once per window
            snap.write(f'{time.strftime("%H:%M:%S")} req={reqs} err={errs} gen={gen} '
                       f'accept={wacc} cyc={cyc} maxctx={maxctx} '
                       f'dec_med all={dall} 15m={drec} rss={rss} swap={swap} '
                       f'boots={boots} ctx={ctx_line}{alert}\n')
            snap.flush()
            if reqs >= 20 and inflight == 0 and time.time() - t_last_fin > a.quiet:
                snap.write(f'# QUIET {a.quiet}s after {reqs} reqs -> leg done '
                           f'{time.strftime("%H:%M:%S")}\n')
                break
        snap.write(f'# FINAL: reqs={reqs} errs={errs} gen={gen} cycles={cyc} '
                   f'proposed={prop} accepted={acc} '
                   f'weighted_accept={100.0 * acc / prop if prop else 0:.1f}% '
                   f'acc/cycle={acc / cyc if cyc else 0:.2f} '
                   f'tok/verify={1 + (acc / cyc if cyc else 0):.2f} '
                   f'maxctx={maxctx} boots={boots} stalls={nstall} '
                   f'dec_median_all={dall} end={time.strftime("%H:%M:%S")}\n')
    print('watcher complete; see', a.out)


if __name__ == '__main__':
    main()
