#!/usr/bin/env python3
"""Rank the four competing open upstream evictor PRs against one recorded workload.

Why this exists: `antirez/ds4` has four open PRs changing the same eviction score, none
reviewed, and adjudicating them normally means build + ~50 min of GPU + a stopped production
engine each. This ranks all of them in about a second per policy on a recorded store/lookup
stream, with no model and no GPU.

Policies, each read off the PR's own diff (`gh api repos/antirez/ds4/pulls/<n>/files`):

  upstream   b3f5018 baseline: score = ANCHOR * (eff_hits + 1.0) * tokens/size
  pr986      #986 -- the permanent +1 becomes `freshness`, decaying on the same clock as hits:
             score = ANCHOR * (eff_hits + freshness) * tokens/size
  pr711      #711 -- score on marginal bytes: tokens / (size - fixed_overhead). `fixed` is not
             guessed: it is the intercept fitted from the arm's own log by fit_size().
  pr814      #814 -- kv_cache_reason_is_anchor() becomes COLD-only, so EVICT/SHUTDOWN lose x2.0.
  pr1170     #1170 -- victim selection skips the entry a load of the incoming prompt would pick;
             if nothing else remains, eviction STOPS, deliberately overflowing the budget.
             The only PR that changes evict()'s return type.
  boost      this session's experimental patch (magnitude 8.0, half-life 300 s).

Stack by name: "pr986+pr711", "pr986+pr711+pr814", ...

Approximations, stated rather than hidden:
  * #1170 threads `protect_text` from the server; the replay has no server, so the protected
    entry is the longest-prefix entry of the conversation currently being stored.
  * Entry identity is (conversation, token count), not prefix SHA, so ~0.5 pt of drift remains
    for arms whose replies diverge (see evict_replay.py's limits block).

usage: python3 evict_prs.py [runs/u16a/requests.jsonl] [N] [budgetMiB]
"""
import gzip
import importlib.util
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_spec = importlib.util.spec_from_file_location("evict_replay", os.path.join(HERE, "evict_replay.py"))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

HALF_LIFE = R.HALF_LIFE
ANCHOR = 2.0
PEAK = [0.0]          # mutable channel: replay() constructs the Store internally
STRETCH = [None]        # seconds per request; None = realistic burst, 1800 = hours-long dwell


def fit_size(log_path):
    """Re-fit size(MiB) = a + b*tokens from an arm's own log -> (a, b, max_residual).

    The affine fit is load-bearing (zeroing `a` moves upstream 63.4% -> 52.3%), so it is
    measured per model rather than hardcoded. Callers should warn if residual is not at
    log precision.
    """
    opener = gzip.open if log_path.endswith(".gz") else open
    rows = []
    with opener(log_path, "rt", errors="replace") as fh:
        for line in fh:
            m = re.search(r"kv cache (?:stored|evicted).*?tokens=(\d+).*?size=([0-9.]+) MiB", line)
            if m:
                rows.append((int(m.group(1)), float(m.group(2))))
    if len(rows) < 20:
        raise SystemExit("not enough store/evict lines in %s to fit a size model" % log_path)
    n = len(rows)
    sx = sum(t for t, _ in rows)
    sy = sum(s for _, s in rows)
    sxx = sum(t * t for t, _ in rows)
    sxy = sum(t * s for t, s in rows)
    b = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    a = (sy - b * sx) / n
    resid = max(abs(s - (a + b * t)) for t, s in rows)
    return a, b, resid


def make_score(flags, fixed_mib):
    def score(self, x, now):
        eh = float(x["hits"])
        used = x["last_used"]
        freshness = 1.0
        if used == 0:
            eh, freshness = 0.0, 0.0
        elif now > used:
            freshness = math.exp2(-(now - used) / HALF_LIFE)
            eh *= freshness
            if eh < R.FLOOR:
                eh = 0.0

        hit_term = eh + (freshness if "pr986" in flags else 1.0)

        denom = x["size"]
        if "pr711" in flags and 0.0 < fixed_mib < denom:
            denom -= fixed_mib

        anchor = 1.0 if ("pr814" in flags and x["reason"] != "cold") else ANCHOR
        s = anchor * hit_term * x["tok"] / denom
        if "boost" in flags and eh == 0.0:
            s *= 1.0 + R.BOOST * math.exp2(-max(0.0, now - x["created"]) / R.BOOST_HL)
        return s
    return score


def run(reqs, budget, name, fixed_mib, protect_next=False):
    """One policy end to end. `protect_next` implements #1170's exemption + overflow."""
    flags = set(name.split("+"))
    base = R.Store
    orig_score, orig_store, orig_evict = base.score, base.store, base.evict_for
    PEAK[0] = float(budget)

    class Tracker(base):
        """Records peak residency so #1170's deliberate overflow is measurable."""
        def __init__(self, *a, **kw):
            base.__init__(self, *a, **kw)
            PEAK[0] = max(PEAK[0], self.total)

        def store(self, *a, **kw):
            base.store(self, *a, **kw)
            PEAK[0] = max(PEAK[0], self.total)

    base.score = make_score(flags, fixed_mib)

    if protect_next:
        def protect_key_for(self, conv):
            best = None
            for (c, n) in self.e:
                if c == conv and (best is None or n > best[1]):
                    best = (c, n)
            return best

        def store(self, conv, tok, now, reason):
            self._protect = protect_key_for(self, conv)
            try:
                orig_store(self, conv, tok, now, reason)
            finally:
                self._protect = None

        def evict_for(self, extra, now):
            target = self.budget - extra
            prot = getattr(self, "_protect", None)
            while self.total > target and self.e:
                cands = [k for k in self.e if k != prot]
                if not cands:
                    break            # PR #1170: returns false, leaving the budget over
                k = min(cands, key=lambda key: (self.score(self.e[key], now),
                                                 self.e[key]["last_used"]))
                x = self.e.pop(k)
                self.total -= x["size"]
                self.evicted.append((x["tok"], x["hits"], now - x["created"], x["reason"]))

        base.store, base.evict_for = store, evict_for

    try:
        R.Store = Tracker
        out = R.replay(reqs, budget, "upstream", stretch_s=STRETCH[0])
    finally:
        R.Store = base
        base.score, base.store, base.evict_for = orig_score, orig_store, orig_evict

    out["policy"] = name
    out["overflow_mib"] = max(0.0, PEAK[0] - budget)
    return out


NAMES = ["upstream", "pr986", "pr711", "pr814", "pr1170", "boost",
         "pr986+pr711", "pr986+pr814", "pr711+pr814", "pr986+pr711+pr814"]

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "runs/u16a/requests.jsonl"
    N = int(sys.argv[2]) if len(sys.argv) > 2 else 430
    budget = int(sys.argv[3]) if len(sys.argv) > 3 else 16384

    log = src.replace("requests.jsonl", "server.log.gz")
    if not os.path.exists(log):
        log = src.replace("requests.jsonl", "server.log")
    a, b, resid = fit_size(log)
    print("size model fitted from %s" % os.path.basename(log))
    print("  size(MiB) = %.3f + %.7f * tokens;  max residual %.4f MiB" % (a, b, resid))
    if resid > 0.05:
        print("  WARNING: residual > 0.05 MiB, the affine assumption is not holding")
    print("  fixed payload cost %.1f MiB = %.0f%% of a 2048-token checkpoint"
          % (a, 100.0 * a / R.size_mib(2048)))
    print()

    reqs = [json.loads(l) for l in open(src)][:N]
    if len(sys.argv) > 4:
        STRETCH[0] = float(sys.argv[4])
        print("dwell time: %.0f s between consecutive requests (a long-lived cache)\n" % STRETCH[0])
    print("%-20s %6s %9s %6s %6s %6s %11s" % ("policy", "cold%", "rebuilt", "evict",
                                               "zh%", "files", "overflowMiB"))
    for name in NAMES:
        r = run(reqs, budget, name, a, protect_next=("pr1170" in name))
        print("%-20s %6.1f %9d %6d %6.1f %6d %11.0f"
              % (r["policy"], r["coldpct"], r["rebuilt"], r["ev"], r["zhpct"],
                 r["files"], r["overflow_mib"]))
