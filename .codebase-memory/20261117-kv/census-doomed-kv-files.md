# Census: unloadable KV checkpoints, measured from the files themselves

Generated 2026-11-17 by `kv_doomed_census.py` (same directory). Engine stopped
21:16:46, census at 21:18:52, so the cache dir was static — no concurrent stores.

## Why this file exists

The FIX 1 write-up quoted "~89 GiB of doomed resident files" from the audit's own
directory walk. The fix author recorded that he **did not reproduce it** ("I did not
walk `/Volumes/FireCuda520/ds4-kv-qwen` (751+ files) myself"), and `adr.md` then
mashed that resident figure together with a *different* measurement (summed store
events) into one sentence. This census replaces the inherited number with our own,
and separates the two quantities.

## Method

A checkpoint is unloadable when its KV header claims fewer tokens than its payload
carries, because the loader accepts a file only if `loaded_tokens->len == hdr.tokens`
(`ds4_kvstore.c`), while the payload always holds the whole live session
(`ds4.c`, `rows = s->checkpoint.len`). Both numbers are read from the file:

- KV header: `version` byte 3, `tokens` u32 @8, `payload_bytes` u64 @40
- extras by version: v1 0, v2 24, v3 68 bytes, then a 4-byte text-size word
- prompt text, then the payload: 13 u32 fields, `magic` @0, **`rows` @field 7**

The payload magic is verified at the computed offset on **every** file; a file whose
magic does not verify is classified `unknown-magic` rather than guessed, so a wrong
offset model would show up as a high unknown count instead of a plausible wrong
total. It did not: 753/753 verified.

## Result

| verdict | files | GiB | % of resident bytes |
|---|---|---|---|
| loadable | 610 | 167.16 | 65.3% |
| doomed (header < payload) | **143** | **88.78** | **34.7%** |

Total cache: 753 files, **255.95 GiB** allocated == apparent (no sparse-file
artifact), against a 256 GiB budget — the directory sits at its ceiling, and
**every third byte in it belongs to a checkpoint that can never load.**

Header/payload gap: min 1, median 378, max 6946 tokens. The min=1 case is the
snapped-down target being one token short of live — the shape FIX 1 removed.

Corroboration: the audit's inherited figure was 144 files / 89.03 GiB. We measure
143 / 88.78 GiB. Different day, different dir state, same answer — the inherited
number was real; it just wasn't *ours* until now.

## FIX 1's live evidence, strengthened

The handoff's caveat is narrower than it first reads: it says the **off-grid
crossing branch** was never observed live, while the aggregate effect *was* already
visible ("24% of stores were `trimmed != 0` historically, and every store since has
been `trimmed=0`"). What this census adds is independent of the log: judged by file
birthtime, **0 of the 143 resident doomed files were created after the fix**, newest
doomed birth 11-17 00:10:12, inside the pre-fix run.

| engine run (log rotation) | stores | `trimmed != 0` |
|---|---|---|
| 1115 07:43 -> 1117 09:49 (pre-fix binary) | 792 | **187** |
| 1117 13:21 -> 14:40 | 25 | 0 |
| 1117 14:40 -> 14:44 | 8 | 0 |
| 1117 14:44 -> 21:16 (final build, mostly idle) | 0 | 0 |

The off-grid branch itself remains unobserved — post-fix traffic never produced a
crossing at an off-grid live length, so that path still rests on code reading plus
the mutation battery.

### Trap worth remembering: mtime is not creation time

Judged by mtime, 10 doomed files look like they were written after the commit
(all at 13:26). They were not. The trailer rewrite path opens an existing
checkpoint `r+b` and rewrites the trailer, which bumps mtime without touching the
payload rows. Birthtime gives 0. **Any future "was this file written by the fixed
binary?" question must use `st_birthtime`, not `st_mtime`** — the mtime-based
reading would have "disproved" FIX 1 ten minutes before it was verified.

A second artifact of the same class: counting `trimmed != 0` with
`grep -v 'trimmed=0'` also matches `divergence anchor stored` lines, which carry
no `trimmed=` field at all. Count the *value*: `grep -oE 'trimmed=[0-9]+' | grep -v 'trimmed=0$'`.

## What this does NOT license anyone to claim

- **Not "89 GiB reclaimed."** The fix is prospective. All 143 files are still on
  disk and will stay until the loader meets them (which unlinks them *without* the
  children check) or eviction retires them. No backfill or census-driven cleanup
  was added.
- **Not a ladder result.** These numbers belong to FIX 1 / FIX 2 (store at live
  length; anchor fires once), both fork-side repairs. The retention ladder's own
  evidence is separate and weaker: an Aug-7 743-request trace as motivation, and
  "half the anchor writes above 48k" by construction — `adr.md` still records
  *"Live model-backed verification remains a follow-up server run (phase 6)"*,
  which was never run.
- **Not upstreamable evidence.** Upstream's `continued_store_target` uses exact
  equality, so this bug cannot occur there at all.
