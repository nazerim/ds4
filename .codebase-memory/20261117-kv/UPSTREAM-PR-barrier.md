# Upstream PR — durability barrier for KV checkpoint writes

Branch **`fix/kvstore-write-barrier`** = `a5e9e77`, one commit, +31/-0 on
`ds4_kvstore.c`, signed, based on `origin/main` (`b3f5018`). Pushed to **`nazerim`**
only — nothing has been opened against `antirez/ds4`.

Worktree: `~/Projects/ds4-pr-kvstore-barrier` (matches the existing `ds4-pr*`
convention; the temporary worktree this was first drafted in has been removed).

**Not filed yet.** Two CONTRIBUTING gates still need an engine-stop window — see
"Remaining gates" below.

## Why this one and not the rewrite

`ds4_kvstore.c` is upstream's file, not ours: upstream is 1,352 lines, our fork
4,086 (**+3,163/−206 over 45 commits**). Upstream has **none** of `anchor_step`,
`small_dense`, `parent_sha`, `children` or divergence anchors, and seven open PRs by
other contributors currently touch this area — #1340/#1342 propose *overlapping*
continued-store scheduling. Upstream `main` has merged nothing since **Nov 3**, and
our own small KV PR #1020 has sat with one comment since Sep 29. A 3k-line rewrite
would collide with active reviews and stall the parts that could land.

Also established while scoping: **FIX 1's store-length bug cannot exist upstream** —
their `continued_store_target` uses exact equality (`live_tokens % step != 0 → 0`), so
it never stores short of the live session. That bug was created by our snap-down.
**FIX 2's** divergence anchors are fork-only too. Neither is upstreamable.

What *is* upstreamable is the durability gap, because upstream has the identical
tmp+rename path (`:1061`–`:1099`), **zero `fsync`**, and no payload integrity check.

## CONTRIBUTING.md compliance

Rules from `CONTRIBUTING.md` (byte-identical to upstream's, unmodified).

| Requirement | Status | Evidence |
|---|---|---|
| Build default backend (`make clean && make`) | **Pass** | 0 errors, 0 warnings on the patched upstream tree |
| Correctness track: `make test` | **Partial** | `./ds4_test --server` → `server: OK` / `ds4 tests: ok` (this is the subset CONTRIBUTING says covers *KV disk-cache bookkeeping*). Full `--all` needs the engine stopped — see remaining gates. |
| Don't regress speed; only an important correctness bug justifies a penalty | **Pass by category, measurement pending** | It is a correctness fix, which is the sanctioned exception. Barrier cost measured directly: ~3 ms `fsync` + ~1.7 ms `F_FULLFSYNC` per ~640 MiB store, against ~155 ms for the write itself (~3% of store tail). |
| CPU target build (`make cpu`) | **Built on macOS only** | 0 errors/warnings for the CPU target on this machine, which is the check CONTRIBUTING asks for. No Linux host was used, so **no portability claim is made for Linux**. |
| Report commands, machine/backend, quant, failures | **Pass** | Machine: Apple M5 Max, 128 GiB, macOS, Metal backend. Cache volume: external NVMe (FireCUDA 520), APFS. Model used for `--server`: `gguf/DeepSeek-V4-Flash-MTP-Q4K-Q8_0-F32.gguf` (3.8 GB). No failures observed. |
| `make cuda-regression` | **Not run** | No CUDA host available. **No claim is made about CUDA behaviour** -- it is untested. |
| Quant/`quality-score` track | **N/A** | No GGUF or numerics change. |

### Remaining gates before opening the PR

Both need the engine stopped (`ds4_test` without `--server`/`--mtp-slice` loads a
second model; `AGENTS.md` records the process lock and that the full suite refuses to
run mid-suite):

1. `make clean && make && make test` — full correctness track.
2. Speed before/after per CONTRIBUTING: two `ds4-bench` CSVs on the same
   machine/backend/model/sweep, comparing `prefill_tps` **and** `gen_tps`, on
   `b3f5018` vs `a5e9e77`. Expect parity — the patch touches only the store path, no
   kernel or scheduler — but the doc asks for the CSVs, so run them rather than argue
   it should be exempt.

Suggested commands (from the repo root, engine stopped):

```sh
git checkout b3f5018 && make && ./ds4-bench -m ds4flash.gguf \
  --prompt-file speed-bench/promessi_sposi.txt --ctx-start 2048 --ctx-max 65536 \
  --step-incr 2048 --gen-tokens 128 --csv /tmp/barrier-before.csv
git checkout fix/kvstore-write-barrier && make && ./ds4-bench -m ds4flash.gguf \
  --prompt-file speed-bench/promessi_sposi.txt --ctx-start 2048 --ctx-max 65536 \
  --step-incr 2048 --gen-tokens 128 --csv /tmp/barrier-after.csv
```

## PR body (draft; the temp copy is superseded by this file)

### Problem

`ds4_kvstore` writes a checkpoint to `<path>.tmp.<pid>`, `fflush`es, closes and
`rename`s it into place — with no write barrier. Entries are content-addressed by the
**sha1 of the prompt text** and validated on load by magic bytes, file size, header
token count and the tokenizer fingerprint. **Nothing validates the payload.**

If the rename survives while the data does not, the file is accepted as a valid
checkpoint carrying stale or zeroed KV rows, so the session resumes with wrong state
and produces **wrong model output** rather than the safe outcome — a cache miss and a
re-prefill. A process crash or kernel panic is already safe (the kernel flushes the
page cache); the exposure is power loss, or a device dropping its volatile write cache.

### Change

`fsync(fileno(fp))` after the final `fflush`, before `fclose`/`rename`; on Apple
platforms additionally `fcntl(F_FULLFSYNC)`. Both happen while the file is still the
hidden `.tmp` name, so nothing can observe a partially durable entry.

- **Failure is a failed store** → existing cleanup skips `rename` and unlinks the tmp.
  No visible-but-broken entry, no leaked temp file.
- **Unsupported ioctl falls back, not fails** (`EINVAL`/`ENOTSUP`/`EOPNOTSUPP`).
- **Compile-time `#error` if `F_FULLFSYNC` is not visible.** Without `<fcntl.h>` the
  macro is undefined, the guard silently skips, and this degrades to `fsync`-only with
  a clean warning-free build. Failing the build beats shipping a durability fix that
  never reaches the media.

### Cost

Cache volume (APFS, external NVMe), 640 MiB — a real checkpoint size — 3 reps:

| | write | `fsync` | + `F_FULLFSYNC` |
|---|---|---|---|
| mean | ~155 ms | ~3 ms | ~1.7 ms more |

`F_FULLFSYNC` is cheap *after* `fsync` because the writes are already drained. Measured
in C, not taken from documentation.

### Testing

`make clean && make` warning-free; `make cpu` builds (macOS CPU target);
`./ds4_test --server` passes
(`server: OK`); `nm -u ds4_kvstore.o` confirms both `_fsync` and `_fcntl` are
referenced, i.e. the barrier is actually in the binary.

**Platform scope:** all of the above was run on macOS / Metal (Apple M5 Max, 128 GiB).
**No claim is made about Linux or CUDA** -- neither was built on nor tested on such a host,
and `make cpu` on macOS is a CPU-target build check, not a Linux portability
verification.

**No new unit test:** the store
path needs a live engine and session (`ds4_session_write_staged_payload`), so it is not
reachable from a model-free test, and the durability effect itself needs fault
injection — stated rather than papered over.

### Deliberately not included

- **No directory `fsync` after `rename`.** Losing a rename loses a checkpoint, which is
  a safe miss.
- **No payload checksum.** The more complete fix — it detects corruption from any cause,
  not just reordering — but it changes the on-disk format and adds a CPU pass over
  hundreds of MiB. Happy to open a discussion.
- The trailer rewrite opens the live file with `fopen(path, "r+b")` and truncates in
  place, a related atomicity gap. Separate change.

## Review posture

Expect pushback on `F_FULLFSYNC` cost on slower media; the fallback path and the
measurement table are the answer. If a maintainer prefers `fsync`-only, that is still a
strict improvement and should be accepted rather than held up — the bug is the missing
barrier, not the choice of barrier.
