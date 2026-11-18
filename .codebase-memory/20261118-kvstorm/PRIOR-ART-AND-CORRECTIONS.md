# Independent review outcome -- prior art found, and corrections to my own claims

Written 2026-11-18 ~16:00, after the ERNIE review of the eviction claim returned. Deliberately a separate
note rather than an edit to `RESULTS.md`, because a second agent was reading that file at the time.
Folding these into `RESULTS.md` is the first task of the next session.

## 1. The finding is NOT novel upstream -- verified myself, not taken on trust
All of these are OPEN on `antirez/ds4` right now (verified with `gh issue/pr view`):

| Item | What it is |
|---|---|
| **#1170** | "Keep the disk KV checkpoint the next request loads when storing an evict snapshot" -- the same mechanism I measured |
| **#986** | "kvstore: age the eviction value of unused checkpoints" -- **a written fix for exactly this** (PR touches `ds4_kvstore.c`, `ds4_server.c`; "Age the eviction score's initial `+1` using the existing six-hour hit decay") |
| **#444** | Disk eviction preferentially removes short-prefix files (density bias) |
| **#814** | Cold checkpoints outrank live dumps |
| **#711** | Shared fixed payload cost biases eviction toward big files |
| **#550** | CLOSED; decay eviction freshness -- the same idea, previously attempted |

Nothing merged: `origin/main` is still `b3f5018`. So the defect is real and unfixed, but **the report is already
made and a fix is already on the table.** Filing a new issue would be a third report competing with #986.
**Revised recommendation: do NOT file.** At most a short measured-evidence comment on #986/#1170, and only after
the numbers in it are corrected (see 4) and the owner approves posting.

## 2. My claim (c) is RETRACTED -- I over-corrected in the previous pass
Last pass I wrote, on a reviewer's advice, that zero-hit ties fall to the `last_used` tiebreak and "the cache
discards the OLDEST checkpoint, the one most likely to be revisited". I reproduced the underlying statistics on
the live cache (header layout confirmed by 753/753 epoch-second sanity checks):
- 753 files, 710 with `hits==0`, 43 with `hits>0` (max 7)
- **709 distinct `tokens/file_size` doubles among 710 zero-hit entries: exactly 1 tie pair (2 files)**
- median `created_at` age 51.5 h, p90 75.4 h

So exact-double ties are a 0.14 % edge case. Ordering among zero-hit entries is decided by density, not by age.
The defensible core is narrower and purely code-level: for `hits == 0` the score is `tokens/file_size` with **no
age term at all**, so a checkpoint can be a victim the moment it lands. That stands. "Ties pick the oldest" does not.
*Second caveat, on the reviewer's own argument:* that density-ordering claim was measured on the **fork's** cache,
which the fork's evictor wrote. It shows ties are rare, not that upstream's ordering is density-driven.

## 3. Also too strong, and mine: "~2x for hours"
Exact value of `1 + 2^(-t/21600)` for a once-hit entry: 2.000 at t=0, 1.891 at 1 h, 1.500 at 6 h, 1.250 at 12 h,
1.063 at 24 h, 1.000 at ~39.9 h. So "about 2x" is only true for roughly the first hour; it is >=1.5x for 6 h.
At real day-scale revisit gaps the decay has already clamped most entries to zero effective hits (in the sampled
cache, 11 of 43 hit entries survive the 0.01 floor, max effective_hits 0.117), which is precisely why the synthetic
run -- 44 s median revisit -- shows the effect this strongly.

## 4. Two errors in the review's draft issue text (do not post it as written)
Verified against the raw per-arm logs with a plain grep:

| Review drafted | Reality |
|---|---|
| "98.5 % of victims at 6 GiB" | 6 GiB arms `u1`/`u2` are **98.1 %** (886/903, 887/904) |
| "98.1 % at 16 GiB" | 16 GiB arms `u16a`/`u16b` are **98.5 %** (644/654) **and 98.6 %** (627/636) |

The budgets were swapped. Also unverified: its "70 disk loads served 430 requests" is `u16a`'s figure, but 430 is
the prefix used for the 16 GiB comparisons, so the pairing needs restating before use. And "76 % within 10 s"
should carry the match-order sensitivity (FIFO 75 %, LIFO 58 %) rather than a single number.

## 5. What the review could NOT verify (state this if any of it is ever quoted)
It did not build or run anything: the A/B outcome figures (63.4 % -> 5.1 %, u400 at 0 %) are still mine alone. It
independently reproduced only the victim mix, lifetimes and disk-hit counts from `u16a`. Byte-exact `file_size`
is not recoverable from `%.2f MiB` log fields, so tie frequency in the synthetic runs is bounded, not measured.

## 6. New, genuinely useful detail from the review (code-level, worth keeping)
`hits` is incremented ONLY by a successful disk load (`ds4_kvstore.c:1327`); re-storing an existing compatible file
rewrites the trailer with a fresh `last_used` but keeps `hits` (`:848-871`, `:494-503`). So a prefix re-saved every
turn stays at `hits == 0` forever. And `(void)live;` at `:544` means nothing protects a checkpoint backing a live
session. Default budget is 4 GiB when `--kv-disk-space-mb` is omitted (`ds4_kvstore.h:12`), so pressure is the
default configuration's steady state, not an exotic setting.
