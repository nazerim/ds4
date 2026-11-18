#!/usr/bin/env python3
"""Offline discrete-event replay of upstream b3f5018's disk-KV evictor.

NO model, NO GPU. Input: the recorded request schedule (conv, turn, prompt_tokens,
completion_tokens) only. Everything else is derived from source semantics:

  size(tokens)  = 113.514 + 0.03005176 * tokens  MiB
                  (affine fit to EVERY stored/evicted log line; max resid 0.037 MiB,
                   i.e. at log precision. The intercept is the fixed payload cost of
                   upstream issue #711 -- it makes density tokens/size strictly
                   INCREASING in tokens, so "lowest density" == "fewest tokens".)
  score(e)      = ANCHOR(2.0) * (eff_hits + 1) * tokens / file_size     [ds4_kvstore.c:538-565]
                  eff_hits = hits * 2^(-(now-last_used)/21600), floored to 0 below 0.01
                  (every store in these runs is reason cold/evict/shutdown, all anchor
                   reasons, so the 2.0 factor is uniform and cancels; no CONTINUED entries
                   exist, so the supersedes-continued factor never fires)
  victim        = argmin score, tie -> smaller last_used          [ds4_kvstore.c:583-592]
  lookup        = longest matching text prefix                    [ds4_kvstore.c:1196-1218]
  hits++        = only on a successful disk load                  [ds4_kvstore.c:1327]
  re-store      = refresh last_used, KEEP hits                    [ds4_kvstore.c:848-871]

Store model, read off the logs and confirmed against u400 (78 cold = 78 turn-1 requests):
  * a cold-prefilled request stores one `cold` checkpoint at floor(P/2048)*2048 tokens
  * a request stores its full live session (`evict`, P+C tokens) iff the NEXT request
    does not continue it live
Policies: "upstream" | "boost" (evict-fresh-boost.patch) | "lru" | "none"

VALIDATION against the live arms (re-run independently 2026-11-18; `policies` sibling sweeps all policies):
  arm / budget              measured                                     replay                       verdict
  u16a  16 GiB   63.4% cold, 2,459,409 tok, 635 evict, 34 files   63.4% / 2,459,409 / 636 / 34     exact
  u400 400 GiB    0.0% cold,   145,836 tok,   0 evict, 447 files   0.0% /   145,836 /   0 / 447     exact
  u1     6 GiB   71.1% cold, 3,390,964 tok, 903 evict, 11 files  71.1% / 3,390,964 / 903 / 11      exact
                            (also disk 58, live 75, ret 460, zero-hit 98.1% -- all match)
  evict counts read +1 vs raw grep because of prefix segmentation (see RESULTS.md "Column note").

KNOWN LIMITS -- read before quoting any replay number:
  * Load-bearing input is the affine size model. Zero the intercept and upstream goes 63.4% -> 52.3%; double it -> 67.9%.
    Re-fit `size(tokens)` from the target model's own log (assert residual < 0.05 MiB) before trusting absolute numbers.
  * All four headline quantities (cold%, rebuilt tokens, eviction count, files-at-end) reproduced exactly on all three
    validation arms, on the first try, with nothing tuned to them. The one non-exact arm is the boost arm (~0.5 pts, SHA issue above).
  * Lookup keys text identity by token count, not prefix SHA, so arms whose replies diverge drift (boost arm 5.4-5.7% vs 5.1% measured).
  * Cannot model: reply-text divergence, tokeniser effects, concurrency > 1, vision/MTP, the fork's delta chains.
  * Clock is near-irrelevant here (0 pts difference vs log timestamps): the zero-hit score has no time term and the 6 h
    half-life barely moves over a 50 min run. Do NOT extrapolate that to long-lived caches.
"""
import json, math, sys
from collections import OrderedDict

ALIGN, MIN_TOK, HALF_LIFE, FLOOR = 2048, 512, 21600.0, 0.01
BOOST, BOOST_HL = 8.0, 300.0
MIB = 1024.0 * 1024.0


def size_mib(t):
    return 113.514 + 0.03005176 * t


class Store:
    def __init__(self, budget_mib, policy):
        self.budget = budget_mib
        self.policy = policy
        self.e = OrderedDict()      # key (conv, ntok) -> dict
        self.total = 0.0
        self.evicted = []           # (tokens, hits, lifetime, reason_of_store)

    def score(self, x, now):
        eh = float(x["hits"])
        used = x["last_used"]
        if now > used:
            eh *= math.exp2(-(now - used) / HALF_LIFE)
            if eh < FLOOR:
                eh = 0.0
        s = 2.0 * (eh + 1.0) * x["tok"] / x["size"]
        if self.policy == "boost" and eh == 0.0:
            age = max(0.0, now - x["created"])
            s *= 1.0 + BOOST * math.exp2(-age / BOOST_HL)
        if self.policy == "lru":                      # pure recency control arm
            s = x["last_used"]
        return s

    def evict_for(self, extra, now):
        if self.policy == "none":
            return
        target = self.budget - extra
        while self.total > target and self.e:
            k = min(self.e, key=lambda k: (self.score(self.e[k], now), self.e[k]["last_used"]))
            x = self.e.pop(k)
            self.total -= x["size"]
            self.evicted.append((x["tok"], x["hits"], now - x["created"], x["reason"]))

    def store(self, conv, tok, now, reason):
        if tok < MIN_TOK:
            return
        k = (conv, tok)
        if k in self.e:                                # re-store: refresh, keep hits
            self.e[k]["last_used"] = now
            return
        sz = size_mib(tok)
        self.evict_for(sz, now)
        self.e[k] = {"tok": tok, "size": sz, "hits": 0, "created": now,
                     "last_used": now, "reason": reason}
        self.total += sz

    def lookup(self, conv, prompt_tok, now):
        best = None
        for (c, n) in self.e:
            if c != conv or n >= prompt_tok:
                continue
            if best is None or n > best[1]:
                best = (c, n)
        if best is None:
            return 0
        x = self.e[best]
        x["hits"] += 1
        x["last_used"] = now
        return x["tok"]


def replay(reqs, budget_mib, policy, clock="model"):
    st = Store(budget_mib, policy)
    t = 0.0
    out = []
    for i, q in enumerate(reqs):
        c, P, C = q["conv"], q["prompt_tokens"], q["completion_tokens"]
        prev = reqs[i - 1] if i else None
        live_reuse = bool(prev and prev["conv"] == c)   # live session continues
        X = 0
        if live_reuse:
            X = prev["prompt_tokens"] + prev["completion_tokens"]
            cls = "live"
        else:
            X = st.lookup(c, P, t)
            cls = "disk" if X > 0 else "cold"
        rebuilt = P - X
        if clock == "model":                            # ~1340 tok/s prefill + decode
            t += rebuilt / 1340.0 + C / 54.0 + 0.3
        else:
            t = q["t0"] - reqs[0]["t0"]
        if cls == "cold" and P >= ALIGN:                # mid-prefill cold checkpoint
            k = (P // ALIGN) * ALIGN
            if k > X:
                st.store(c, k, t, "cold")
        nxt = reqs[i + 1] if i + 1 < len(reqs) else None
        if not (nxt and nxt["conv"] == c):              # full session snapshot
            st.store(c, P + C, t, "evict")
        out.append((cls, rebuilt, q["turn"]))
    ret = [o for o in out if o[2] > 1]
    cold = [o for o in ret if o[0] == "cold"]
    zh = sum(1 for e in st.evicted if e[1] == 0)
    lt = sorted(e[2] for e in st.evicted)
    return dict(policy=policy, budget=budget_mib, ret=len(ret), cold=len(cold),
                coldpct=100.0 * len(cold) / len(ret), disk=sum(1 for o in ret if o[0] == "disk"),
                live=sum(1 for o in ret if o[0] == "live"),
                rebuilt=sum(o[1] for o in ret), ev=len(st.evicted),
                zhpct=100.0 * zh / len(st.evicted) if st.evicted else 0.0,
                medlife=lt[len(lt) // 2] if lt else 0.0, files=len(st.e))


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "runs/u400/requests.jsonl"
    N = int(sys.argv[2]) if len(sys.argv) > 2 else 430
    reqs = [json.loads(l) for l in open(src)][:N]
    hdr = ("policy", "budgetMiB", "ret", "cold", "cold%", "disk", "live", "rebuilt",
           "evict", "zh%", "medLife_s", "files")
    print(f"schedule={src} N={N}")
    print(("{:>9} {:>9} {:>4} {:>5} {:>6} {:>5} {:>5} {:>9} {:>6} {:>6} {:>9} {:>6}").format(*hdr))
    cases = [(16384, "upstream"), (16384, "boost"), (16384, "lru"),
             (409600, "upstream"), (16384, "none"), (6144, "upstream"), (6144, "boost")]
    for b, p in cases:
        r = replay(reqs, b, p)
        print(("{:>9} {:>9} {:>4} {:>5} {:>6.1f} {:>5} {:>5} {:>9} {:>6} {:>6.1f} {:>9.0f} {:>6}")
              .format(r["policy"], r["budget"], r["ret"], r["cold"], r["coldpct"], r["disk"],
                      r["live"], r["rebuilt"], r["ev"], r["zhpct"], r["medlife"], r["files"]))
