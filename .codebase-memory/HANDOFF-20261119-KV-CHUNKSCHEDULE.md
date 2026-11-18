# HANDOFF 2026-11-19 — disk-KV chunk schedule, eviction density, and the fork mitigation

**Read this first.** It supersedes the accumulated addenda of
`HANDOFF-20261117-PREFILL-DIPS.md` for this lane; that file stays as the trail (585 lines,
includes claims this one retracts). Full index: `20261118-kvstorm/README.md`.

Status: investigation closed, one mitigation **live in production**, one issue filed upstream.
Nothing mid-flight.

---

## 1. The finding

`--kv-disk-dir` changes greedy completions. Writing the cold checkpoint syncs the session to
`cold_store_len` **mid-prefill**, which inserts an extra chunk stop; reduction order follows the
stop (`ds4.c:1059` says so outright) and greedy decoding amplifies a near-tie flip.

- Same prompt, same binary, one flag different: **fork 6/6 differ, upstream `b3f5018` 4/6** on
  real ~12k-token prose (pooled 10/12, median similarity **0.30**, range 0.059–0.757). The
  cache-off side never reads disk — verified: 0 `kv cache hit` / 0 `stored` lines.
- **The checkpoint is not corrupt.** `resume@8192 == cold-with-cache-off` 6/6, so the
  mid-prefill `continued` store is a faithful snapshot. Confirmed independently by a static
  audit of the persisted inventory (GDN recurrent + conv state, attention K/V and index keys,
  PLE history, mRoPE delta, position rows).
- 6-vs-4 is **not** "the fork is worse": Fisher exact two-sided **p = 0.455** at n=6.

## 2. Live in production

`7d2a4ec` — one file, `ds4_server.c`, +45/−18. It computes the cold stop even with the cache off
and inserts the same sync, so the plan depends on the request alone. It does **not** move the
stop, therefore cache-ON output and reuse depth are unchanged (store still
`tokens=12288 trimmed=1570` for a 13,858-token prompt). Cost: one extra chunk stop in cache-off
runs only.

Verified with a **negative control** (`20261118-kvstorm/verify_parity.sh`): cache ON `8192→12288→13858`; cache OFF
+ parity identical plan and answers 2/2; parity disabled → `8192→13858` and 1/2 diverges again.
`20261118-kvstorm/verify_prefix_equal.sh` built the **pre-fix binary in a temp worktree** and compared cache-ON
output: identical sha256, 2/2 — that is why deploying was safe.

Back out: `git revert 7d2a4ec && make ds4-server && ./ds4-server.sh restart-qwen`, or
`--disable-kv-cache-cold-stop-parity` (no rebuild). Nothing else depends on it.

## 3. Public artefacts

- **antirez/ds4#1359** (open) — the filed finding. `20261118-kvstorm/ISSUE-POST-BODY.md` is the
  exact posted text; the draft file keeps the framings I tested and rejected.
- **antirez/ds4#711** — evidence comment: measured fixed cost, offered as a cross-check for
  `ds4_engine_checkpoint_fixed_overhead_bytes()`.
- **antirez/ds4#1358** (ours, open, mergeable) — durability barrier. `origin/main` has not moved
  off `b3f5018`, so there is nothing to rebase.

## 4. Eviction side (settled, third-hand — do not re-file)

`size(MiB) = 113.518 + 0.0300516·tokens`, max residual **0.0073** over 1342 lines; six upstream
arms agree within 0.001 MiB. Positive intercept ⇒ `d/dt[tokens/size] = a/(a+bt)² > 0` ⇒ for
`hits == 0` the score **provably** reduces to "evict the fewest-token file". Observed: all **309**
`reason=cold` mid-prefill checkpoints evicted; **98.5 %** (644/654) of victims had `hits == 0`.

Same-capacity controls at 16 GiB (burst clock, `u16a` schedule, offline replay): upstream **63.4**
% cold vs LRU 15.9, random 10.8–13.4, adversarial MRU 48.0, largest-first 36.4. Upstream is worst
of seven. Zeroing the intercept → 52.3 %.

**Format-scoped**: fork delta stores are *not* affine (8,868 tok = 133.9 MiB < 2,048 tok =
175.1 MiB), so 113.5 MiB is a full-snapshot-format constant, not a model constant.

Open PRs ranked on one recorded stream (`20261118-kvstorm/evict_prs.py`): at 3600 s dwell #986 → 15.9 %,
#814 → 11.9 % (upstream 57.7). **Those dwell columns are UNVALIDATED** — only the burst clock has
live arms. Our own fresh-boost patch is burst-only and converges on upstream at hour-scale dwell;
it was over-claimed in an earlier draft.

## 5. Retracted — do not repeat these

- ~~"a `continued` checkpoint restores bad state"~~ — refuted by `C == D` above.
- ~~"161 comparisons prove the cache path is faithful"~~ — all 17 resumes in that run landed **on**
  a cold chunk boundary, so byte-identity was forced by construction; it could not have failed.
- ~~"missing age term" is the harm~~ — it is the **density** term; the age term is only the
  enabling condition (~3.7 pts).
- ~~"~2× slower for hours"~~ → 1.891× at 1 h, 1.500× at 6 h.
- ~~tie-break on `last_used` matters~~ — exact-double ties are 1 pair in 710 on the live cache.
- ~~capacity explains it~~ — refuted; same-capacity controls are the isolation.
- Downtime "~25 min / 11 windows" was an estimate: **actual 63 min / 19 windows**, counted.

## 6. Parked, with what unblocks each

| item | unblocked by |
|---|---|
| Off-schedule `continued` resume still changes answers (deliberately unfixed) | antirez's response to #1359 |
| Dwell columns unvalidated | a live arm only if someone disputes the replay |
| Short-prompt divergence rate | a ~10-min window; may well be lower than ~12k-token rate |
| Cache-off throughput cost of parity | one window, trivial to add if anyone cares |
| `ds4-ab-upstream` worktree (b3f5018, built) | drop whenever; findings already reproduced |
| Commit signing badge | operator: `gh auth refresh -h github.com -s admin:ssh_signing_key` then `gh ssh-key add ~/.ssh/github_signing.pub --type signing` |

## 7. Method debt — read before building any probe here

Five defects this lane produced were all caught by **independent counters**, none by a pass/fail
summary: prompts under the 2048-token store step (compared cold vs cold, "100 % agree", 34 s);
`max_tokens` that never bound (all `finish=stop` at ~17 tokens); a preflight that pre-cached the
very prompt it gated; assumed chars/token (real values measured: 6.37 synthetic, 3.06 repo prose);
`+/-1 s` log attribution double-counting. Also: `fh.tell()` after a `with`-close; `$?` after a
pipe; `git ls-files --others` needs `--exclude-standard`. **An experiment that finishes fast and
reports perfect agreement is a prompt to verify the mechanism fired.**

Newest instance: `./ds4_test --server` passed and proved nothing — `ds4_test` links `ds4.c`; the
suite that compiles `ds4_server.c` is `make test-server-logic` → `ds4_server_test`.

## 8. Resume cold, next session

No window needed for any of these — run from `.codebase-memory/` (paths below are relative to it):

```sh
python3 20261118-kvstorm/evict_policies.py runs/u16a/requests.jsonl 430 16384   # same-capacity controls, ~1 s
python3 20261118-kvstorm/evict_prs.py     runs/u16a/requests.jsonl 430 16384    # ranks the four open PRs
python3 20261118-kvstorm/evict_prs.py     runs/u16a/requests.jsonl 430 16384 1800   # + dwell (unvalidated)
gh api repos/antirez/ds4/issues/1359 --jq '.state, .comments' # upstream response?
```

Read order: this file → `20261118-kvstorm/README.md` → `20261118-kvstorm/CACHE-DIVERGENCE.md` → `20261118-kvstorm/PR-RANKING.md` →
`20261118-kvstorm/RESULTS.md` (FINAL section only; below the rule is superseded) → `20261118-kvstorm/PRIOR-ART-AND-CORRECTIONS.md`.

Caveat on paths: `.codebase-memory/DOC-ROT-20261117.md` is regenerated **in place** despite the
date in its name (`doc-rot-scan.py`). Three entries are benign and share one cause: the repro
writes `prompt.json` in its own working directory two lines above citing it, so it is a
generated artefact, not a dead reference.

State at hand-off: `dirty=0`, `unpushed=0`, remote `nazerim` only; written at `4a6b202`, so the commit that adds this file is `HEAD` from a clean `main`. Prod PID 65953
on the patched binary, port 8002, KV cache untouched at **753 files**. Two worktrees: main +
`ds4-ab-upstream`. Scratch KV dirs deleted; cache volume 806 GiB free.
