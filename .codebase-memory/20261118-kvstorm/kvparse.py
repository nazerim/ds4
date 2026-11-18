#!/usr/bin/env python3
"""Attribute ds4-server log lines to requests WITHOUT timestamps.

Caveat (verified by review): the 'kv cache stored reason=evict' line that saves request n-1's
own session is logged AFTER finish n-1, so it lands in segment n. Per-request eviction counts are
therefore shifted by one request; totals are preserved, and the tail after the last finish line is
dropped (one eviction in u1). Aggregate counts over a prefix are accurate to +/-1.

Requests are strictly sequential, and every request ends with exactly one
'chat ctx=A..B:N gen=G finish=... Ts' line. So the log splits into segments:
segment n = every line after finish line n-1 up to and including finish line n.
That is where the request's 'live kv cache miss', 'kv cache hit', eviction and
store lines live (they are logged before 'prompt start'). No time windows, no
clock resolution problems, no double attribution.

A mismatch between the number of finish lines and the number of requests is a
hard error: the attribution would be wrong, so we refuse to produce numbers.
"""
import gzip, os, re

FINISH = re.compile(r"chat ctx=\d+\.\.\d+:\d+ gen=\d+ finish=")
START = re.compile(r"chat ctx=(\d+)\.\.(\d+):\d+ prompt start")
DONE = re.compile(r"chat ctx=0\.\.\d+:\d+ prompt done ([0-9.]+)s")
LINE = re.compile(r"^\d{4} \d\d:\d\d:\d\d ds4-server: (.*)$")
num = lambda s, k: (lambda m: int(m.group(1)) if m else None)(re.search(rf"\b{k}=(\d+)", s))


def _open(path):
    return gzip.open(path + ".gz", "rt", errors="replace") if not os.path.exists(path) \
        else open(path, errors="replace")


def segments(log_path, n_requests=None):
    segs, cur = [], []
    for raw in _open(log_path):
        m = LINE.match(raw)
        if not m:
            continue
        s = m.group(1)
        cur.append(s)
        if FINISH.search(s):
            segs.append(cur)
            cur = []
    if n_requests is not None and len(segs) != n_requests:
        raise SystemExit(f"{log_path}: {len(segs)} finish lines != {n_requests} requests; "
                         f"attribution impossible")
    out = []
    for lines in segs:
        d = {"hit": None, "miss": None, "start_prompt": None, "ctx_from": None, "prefill_s": None,
             "evict": [], "stores": [], "lines": lines}
        for s in lines:
            if s.startswith("kv cache hit") and d["hit"] is None:
                d["hit"] = num(s, "tokens")
            elif s.startswith("live kv cache miss") and d["miss"] is None:
                d["miss"] = {"common": num(s, "common"), "prompt": num(s, "prompt")}
            elif s.startswith("kv cache evicted"):
                d["evict"].append(re.search(r"reason=(\S+)", s).group(1) if "reason=" in s else "?")
            elif s.startswith("kv cache stored"):
                d["stores"].append(re.search(r"reason=(\S+)", s).group(1) if "reason=" in s else "?")
            m = START.search(s)
            if m:
                d["ctx_from"], d["start_prompt"] = int(m.group(1)), int(m.group(2))
            m = DONE.search(s)
            if m:
                d["prefill_s"] = float(m.group(1))
        out.append(d)
    return out


def classify(seg, prompt_tokens):
    """Return (class, rebuilt_tokens). rebuilt = P - X from this request's own
    'prompt start ctx=X..P' line: the tokens actually prefilled, exact for cold (X=0), disk
    hit (X=hit tokens) and live reuse (X=live prefix). Falls back to the hit/miss arithmetic
    only if the start line is missing (reported by the caller as a warning)."""
    if seg["hit"] is None and seg["miss"] is None:
        cls = "live"
    elif seg["hit"] is not None:
        cls = "disk"
    else:
        cls = "cold"
    if seg["start_prompt"] is not None and seg["ctx_from"] is not None:
        return cls, seg["start_prompt"] - seg["ctx_from"]
    return cls + "?", -1
