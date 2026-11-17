# RFC draft: retention & lineage for the disk KV cache — a design conversation

**Target:** `antirez/ds4` issue tracker. **Status: draft, not posted.**
**Drafted:** 2026-11-17 from `nazerim/ds4` (fork). Baseline read against
`origin/main` = `b3f5018` (Nov 3). Every upstream line number below was read
with `git show origin/main:<file>` at drafting time.

> Scope note: this is an RFC, not a code proposal for the 3k-line fork rewrite.
> It is meant to be complementary to #1340, #1342 and #1323, and it asserts
> nothing about what their authors want — only where the mechanisms touch.

## The question

Is the disk checkpoint layer meant to serve *one mostly-linear conversation at
a time* (score each file on its own hits and density), or *several long-lived
lineages under a switch-heavy workload* (where a checkpoint's value depends on
the lineage it belongs to and on the frontier its session is at)? Our fork
runs multi-hundred-k-token agent sessions for days across restarts, and we
keep landing on retention questions that per-file eviction cannot answer: what
to keep when a session switches, what is safe to drop when a child checkpoint
depends on a parent, and which frontier is worth a write at all. #1340 and
#1342 are actively changing *when* continued checkpoints fire; before more
scheduling lands in `ds4_kvstore.c` / the server listener, would maintainers
want to settle whether lineage should exist in the data model at all — since
retention policy and store scheduling end up editing the same call sites?

## What the cache does today upstream (verified at `b3f5018`)

All of this is read-only code inspection; nothing here was benchmarked by us
against upstream. Platform of all *fork-side* measurements below: macOS /
Metal (Apple M5 Max, external NVMe, APFS). **Nothing here has been built or
tested on Linux or CUDA; we make no claim about those platforms.**

- **Score-based eviction before a write.** Each candidate scores
  `(effective_hits + 1) × tokens / file_bytes`, with hits decayed by age
  (`exp2(-age_hours / 6)`), a ×2.0 prior for anchor-reason entries, and a prefix
  supersede discount of `×(0.05 + 0.45·h)`, `h ∈ [0,1)`. Eviction runs pre-store.

## The design axes we found load-bearing

Fork experience, in the order they bit us:

  budget 4096 MiB and opt-in via `--kv-disk-dir`; ours: 256 GiB, which is the current
  `ds4-server.sh:38` default (`KV_SIZE="${KV_SIZE:-262144}"`) and was 64 GiB when the
  motivating trace ran.

## Relation to the open PRs

Mechanism-level only; no claims about author intent.

- **#1340 (align continued step with the prefill chunk schedule, +116/−18).**
  Same code we would touch (step computation / target rule) → **textually
  competing** with any fork ladder port; **directionally orthogonal** to
  retention: it changes *which grid positions are reachable*, not who is kept.
  Our step-doubling policy would sit on top of whatever grid rule #1340 lands
  with, and needs re-derivation if that rule changes.
- **#1342 (store at the first chunk past each step bucket, +74/−3).**
  Same listener call sites → **competing** as a second "when to fire" rule;
  **complementary** to retention in effect: denser, schedule-robust rungs
  raise the value of *not* evicting them (which is exactly the question the
  grace/lineage axis asks). Note both #1340 and #1342 cite "#1323 root cause"
  investigation in their bodies — the three are one conversation we should
  join, not fork.
- **#1323 (`--kv-cache-cold-anchor last-user`, +86/−6).** **Orthogonal in
  mechanism** (cold-store cut position, a different code path), **same
  underlying axis**: it exists because long fixed histories want *reusable*
  prefixes across requests — cross-restart/cross-session reuse, our axis 2,
  attacked from the cold-store side. We'd want the answer to "how large may a
  cold cut grow under budget?" (`cold_max_tokens` = 30000 today) settled in
  the same conversation; #1228 ("retain bounded cold cache prefixes for long
  prompts", by title) is in that space too.


- **#1343** (*save image conversations in the disk KV cache*) is the largest open
  change to `ds4_kvstore.c` — **+1080/−63 across both kvstore files**, plus docs and
  two new test files, open and updated 2026-11-10. It also introduces payload-side
  identity, so any format-level lineage discussion must treat it as prior art and a
  textual conflict, even though image handling is out of scope for this RFC.
- **#1345** (*make layer-major prefill chunk boundaries saveable*, `ds4.c` only) sits in
  the same grid-reachability space as open question 2 and should be counted there.
## Explicit non-goals

- **Not** upstreaming the fork's KV rewrite (+2,933/−199 on
  `ds4_kvstore.c` alone over 45 commits; 1,352 → 4,086 lines) as a unit.
- **Not** porting FIX 1 / FIX 2. Both fix *fork-created* bugs: upstream's
  exact-equality target (`ds4_kvstore.c:747-754`) cannot store short of the
  live session, and divergence anchors don't exist upstream. They are not
  evidence for any missing upstream feature.
- **Not** claiming any measured win for the ladder (see "What we lack").
- **Not** deciding #1340-vs-#1342; both are better than no rule at all.
- **Not** a vision-disk-KV proposal (upstream has its own open lane there —
  #1322/#1343 by title).
- **Not** any Linux/CUDA claim; nothing was built there.
- The separate durability-barrier change (fsync before rename) is scoped
  apart from this RFC and unaffected by it.

## What we have, and what we lack

Held (fork-side, macOS/Metal):

The trace that motivated the grace window is ours, from 2026-08-07 (743 requests at a
64 GiB cache), and it exercised **our** lineage-retirement ranking
(`conversation-retired`, `4f4a486`, 2026-08-02) — **not** upstream's score-based
evictor, which has been in mainline since 2026-05-19 (`599e49d`) with the anchor prior
and prefix discount added 2026-05-26 (`ec6a82a`). So the finding was never measured
against the evictor you actually ship. The cross-session retirement pathology may be
an artifact of our own ranking rather than anything upstream needs to fix, and we are
saying so before anyone has to ask.

Lacked — stated plainly:

- **The ladder has no measured live win.** The KV-Cache-v2 ADR says so in its own
  consequences list, verbatim: *"Live model-backed verification remains a follow-up
  server run (phase 6)"* — and phase 6 was never run. "Half the anchor writes above
  48k" is arithmetic from `anchor_step` 8192→16384 above 49152, not a measurement.
- Any A/B of grace-vs-no-grace on the *current* upstream eviction scorer.
- Any Linux or CUDA behavior (unverified; not built).
- Any re-derivation of the Aug-7 trace against upstream's score function.

## What we'd offer (small, ordered, each independently reviewable)

1. **Eviction observability** (~log-only): emit the score components and
   reason for each eviction victim, like upstream already does for stores.
   `ds4_kvstore_evict` is model-free → unit-testable with fixtures. Lets the
   retention debate run on data from anyone's workload, including upstream's.
2. **Grid-rule test harness**: pure decision helper + table tests that replay
   backend chunk schedules and assert a frontier per bucket — the same test
   shape #1340 introduced; happy to fold ours into whichever of #1340/#1342
   lands, rather than duplicate.
3. **Recency floor / grace as a score term** (one option, default off): files
   `last_used` within `--kv-cache-*grace` seconds get a score floor instead of
   density ranking. Small patch to `ds4_kvstore_entry_eviction_score`; unit
   tests model-free. If lineage is unwanted, a per-file recency floor is the
   minimal honest version of what we learned.
4. **Miss-driven divergence anchors** (server-side, opt-in): on a load that
   misses mid-prefix, store one anchor at the exact common prefix. Fork-only
   evidence for this one; we'd say so in the PR.
5. **Lineage/parent-chain**: design discussion only until 1-3 land. Format
   change; needs maintainer appetite before code exists.


Two notes that shrink this offer rather than grow it:

- **Slice 1 is smaller than it reads.** Upstream already logs evictions
  (`kv cache evicted reason=disk-cache-full tokens=%u hits=%u size=%.2f MiB file=%s`,
  `ds4_kvstore.c:597`). The increment is exposing the *score components*, not adding a
  log line — so it should be a small, uncontroversial patch.
- **Where coverage already exists we will point at it rather than promise it.**
  `tests/kv_policy_harness` is wired into `make test`, and retention has model-free
  `--server` coverage (`test_kv_cache_retire_grace_protects_recent`).

Separately, and independent of any retention decision: we hold a small
macOS/Metal-tested patch that barriers checkpoint writes before the publish-rename.
`ds4_kvstore.c` currently contains no `fsync` (count 0) while publishing via
tmp→rename, and nothing on the load path validates payload bytes — so an entry whose
data did not survive can load as a valid checkpoint. We are filing that on its own;
it is not contingent on this discussion.
## Open questions for maintainers

1. Is per-file eviction the intended permanent model, or should the kvstore
   know lineages (session/leaf identity, parent links)?
2. Is `continued_interval_tokens`' single flat step meant to lengthen with
   context (write traffic) or stay (re-prefill bound)? #1340/#1342 fix its
   *reachability*; the length-dependence question is still open.
3. Should `cold_max_tokens` (30000) stay a cliff, or interact with
   retention — a cold anchor at a chat boundary is arguably the cheapest
   per-reused-token file in the cache, which is what the score rewards?
4. Naming: upstream "anchor" = reason prior / chat prefix; we'd say "rung" or
   "frontier" for grid checkpoints to avoid collision.
5. How to sequence with #1340/#1342 so retention patches don't rebase-conflict
   with scheduler churn — e.g. land the test harness (slice 2) first?

## Claims ledger (LOCAL ONLY — do not paste into the issue)

Verification methods: **[G]** = read-only `git show origin/main:<file>` /
`git log` at `b3f5018` on 2026-11-17; **[gh]** = read-only `gh pr view`
against `antirez/ds4` on 2026-11-17; **[F]** = fork working-tree file:line;
**[D]** = cited from a fork investigation doc (path given); **[U]** =
unverified, stated as such.

| # | Claim | Source | How verified |
|---|---|---|---|
| 1 | Upstream `ds4_kvstore.c` = 1,352 lines; fork = 4,086 | both files | [G][F] `wc -l` on `git show` output and working tree |
| 2 | Divergence scale: `ds4_kvstore.c` alone +2,933/−199 over 45 commits; `.c`+`.h` +3,163/−206 over 46 | `git diff --numstat`/`git rev-list` at `b3f5018` | [G] re-verified. The barrier doc previously mispaired these scopes; corrected 2026-11-17 23:04 |
| 3 | Upstream continued target = exact equality ⇒ FIX-1-class bug cannot occur upstream | `ds4_kvstore.c:747-754` | [G] direct read |
| 4 | No fork vocabulary upstream (`anchor_step`/`small_dense`/`parent_sha`/`children`/`divergence`/`retire`/`grace`/`ladder`/fingerprint = 0 hits) | upstream `ds4_kvstore.c` | [G] `grep -ci` per symbol |
| 5 | Upstream defaults: 4096 MiB budget, opt-in dir, min 512, cold_max 30000, interval 10000→10240, align 2048, trim 32, half-life 6 h | `ds4_kvstore.h:12-13`, `ds4_kvstore.c:33-42`, `ds4_server.c:16021` | [G] |
| 6 | Eviction score + priors (×2.0 anchor reasons; 0.05/0.45 prefix supersede; pre-store evict) | `ds4_kvstore.c:44-56,510-566,1050-1058` | [G] |
| 7 | Load keeps files; load checks model_id/size/len; no fsync (0 hits); sha1 = text key, payload unchecked | commit `24fa85e`; `ds4_kvstore.c:1254,1286,463-465,1061-1113` | [G] |
| 8 | Cold stores skipped for multimodal and prompts > cold_max | `ds4_server.c:13727-13739,13827,13966` | [G] |
| 9 | #1340, #1342, #1323 open; sizes, authors, quoted in-PR numbers | live `gh pr view` | [gh] verified. Dates: #1340/#1342 opened & updated 2026-11-10; **#1323 opened 2026-11-05, last updated 2026-11-11** |
| 10 | Aug-7 trace (743 req, 64 GiB) motivated the fork's grace window | `adr.md:7`; grace `3faa8d7` 2026-08-08 | [G] verified. **Scope caveat:** the trace ran against fork lineage-retirement ranking (`4f4a486`, 2026-08-02), not upstream's evictor (`599e49d` 2026-05-19 + `ec6a82a` 2026-05-26, both ancestors of the fork). Never re-derived against upstream's scorer. |
| 11 | Ladder live verification (phase 6) never ran; "half the anchor writes above 48k" is by construction (8192→16384) | [D] `adr.md` KV-Cache-v2 ADR consequences (the phase-6 line) + grid ADR (the 8192→16384 arithmetic); `20261117-kv/census-doomed-kv-files.md` "does NOT license" | [D] |
| 12 | Census: 143 files / 88.78 GiB **resident, unloadable, still present** (34.7% of a 255.95 GiB cache; 753/753 payload magics verified); nothing reclaimed | [D] `20261117-kv/census-doomed-kv-files.md` (script `kv_doomed_census.py`) | [D]; belongs to FIX 1/2, **not** ladder evidence; fork production only |
| 13 | FIX 1: 187/792 stores `trimmed != 0`, 113.62 GiB written (≠ resident 88.78 GiB), 41 loader discards; FIX 2: one anchor → 342 stores / 44.4 GiB | [D] `adr.md` (2026-11-17 ADR + correction), census, `HANDOFF-20261117-PREFILL-DIPS.md` §2c | [D]; fork-only bug classes (see #3) |
| 14 | Cross-restart hit 96,883 tok: 75.6 s → 12.1 s, `cached=81920` | [D] `HANDOFF-20261117-PREFILL-DIPS.md` §10 | [D]; fork build, macOS/Metal |
| 15 | Chained delta ~607-609 MiB flat vs full 14,023 MiB @ ~426k (23.0×) | [D] `adr.md` ADR 2026-09-29 consequences | [D]; fork measurement |
| 16 | Eviction pressure: 107 `evicting checkpoint` events / 18.92 M tokens | `HANDOFF` §9 | [D]. Different scope from the 93 / 17,463,570 in `design-kv-io-lock.md` (one log set, slot evictions) — that figure was **reproduced there**, not retracted; both stand for their own scope |
| 17 | Fork production budget 256 GiB | `ds4-server.sh:38` (`KV_SIZE=262144`) | [F] |
| 18 | Fork knobs: anchor_step 8192 / wide step above 49152 / grace 3600 s / max-divergence-anchors 8 | `ds4_kvstore.h:20,25,30,31` | [F] |
| 19 | Tokenizer fingerprint = fork-side, unmerged PR #1020 (#983 superseded by it) | [D] `adr.md` Sep-6 ADR; barrier doc; PR #983 comment | [gh][D]; #1020 still open at draft time |
| 20 | Upstream: no image identity in disk KV payloads | 0 `image` hits in upstream `ds4_kvstore.c`; `!multimodal` gates | [G]; the b0982a1 verbatim "stance quote" in `adr.md` is **not in that commit's message** (subject-only) — not used here |
| 21 | All fork measurements: macOS/Metal, Apple M5 Max, external NVMe/APFS | `20261117-kv/UPSTREAM-PR-barrier.md` test matrix | [D]; Linux/CUDA: **no claim, unverified** |

## Drafting notes (not for posting)

**Claims that need a live measurement run before we could assert them
upstream:**

- Anything ladder-shaped: grace benefit, step-doubling cost/benefit,
  divergence-anchor hit rate. All would need the fork build (or the ported
  slices) run against upstream's current evictor on upstream binaries at
  `b3f5018+patches`, with real agent traffic — including the evict-storm
  regime the synthetic baseline never produced (the 1.5%-of-holds caveat).
- The Aug-7 trace's conclusion must be re-derived against upstream's
  score-based `ds4_kvstore_entry_eviction_score` before it is quoted as an
  upstream problem; it was measured against the fork's pre-grace per-lineage
  LRU at a 64 GiB budget.
- Chained-store ratios (607 MiB flat / 23×) are one fork deployment's
  numbers; upstream would need their own depth distribution to make them
  meaningful.
- The "+3,163/−206 over 45 commits" line in `UPSTREAM-PR-barrier.md` should be
  re-scoped before reuse: those totals are `ds4_kvstore.c`+`.h` over 46
  commits; `.c` alone is +2,933/−199 over 45. Both figures verified — the
  pairing is the error.

**Biggest risk the design is wrong for upstream:** the pathology the ladder
targets was manufactured by *our* configuration — 64–256 GiB budgets holding
dozens of multi-hundred-k lineages with session switching, under an eviction
model that predated upstream's hit-decay/density scorer. Upstream's default is
an opt-in **4 GiB** cache where one deep session's ladder can fill the budget
alone, and its scorer already discounts prefix waypoints (#1340/#1342's
territory suggests their pain is *rungs not landing*, not *rungs being
evicted*). If upstream's intended workload is closer to single-session,
short-lived caches, lineage-aware retention is complexity that buys nothing —
and the grace/granularity machinery we would be asking to standardize is
solving a problem only our deployment has.
