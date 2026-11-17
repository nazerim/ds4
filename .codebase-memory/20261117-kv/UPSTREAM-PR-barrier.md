# Upstream PR — durability barrier for KV checkpoint writes

Branch **`fix/kvstore-write-barrier`** = `4aa5130`, one commit, +45/-0 on
`ds4_kvstore.c`, signed, based on `origin/main` (`b3f5018`, still current). Pushed to
**`nazerim`** only — nothing has been opened against `antirez/ds4`. Superseded shas:
`a5e9e77` (first port) → `a2cda53` (dropped Linux/CUDA claims) → `f7db783` (cost
provenance) → `7c49f0b` (review fixes) → `4aa5130` (measured cost, final).

Worktree: `~/Projects/ds4-pr-kvstore-barrier` (matches the existing `ds4-pr*`
convention; the temporary worktree this was first drafted in has been removed).

**Not filed yet, but every gate that can be closed on this host is closed.** The
engine-stop window was taken 21:16–22:58 and all of it was spent: clean build, the
`--server` suite, a full-`make test` baseline control, four `ds4-bench` sweeps in
alternating order, and a live execution proof on the upstream tree. What remains
blocked is not a gate we can pass here — see *Gate results*.

## Why this one and not the rewrite

`ds4_kvstore.c` is upstream's file, not ours: upstream is 1,352 lines, our fork
4,086 (**+3,163/−206 over 46 commits for `.c`+`.h`**; `ds4_kvstore.c` alone is +2,933/−199 over 45 commits — the two scopes were previously conflated here). Upstream has **none** of `anchor_step`,
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
| Correctness track: `make test` | **Cannot complete here; control proves it is not the patch** | `./ds4_test --server` → `server: OK` / `ds4 tests: ok` (this is the subset CONTRIBUTING says covers *KV disk-cache bookkeeping*). Full `--all` needs the engine stopped — see remaining gates. |
| Don't regress speed; only an important correctness bug justifies a penalty | **Pass — 2 reps/arm, alternating** | It is a correctness fix, which is the sanctioned exception. Barrier cost measured directly: ~3 ms `fsync` + ~1.7 ms `F_FULLFSYNC` per ~640 MiB store, against ~155 ms for the write itself (~3% of store tail). |
| CPU target build (`make cpu`) | **Built on macOS only** | 0 errors/warnings for the CPU target on this machine, which is the check CONTRIBUTING asks for. No Linux host was used, so **no portability claim is made for Linux**. |
| Report commands, machine/backend, quant, failures | **Pass** | Machine: Apple M5 Max, 128 GiB, macOS, Metal backend. Cache volume: external NVMe (FireCUDA 520), APFS. Model used for `--server`: `gguf/DeepSeek-V4-Flash-MTP-Q4K-Q8_0-F32.gguf` (3.8 GB). No failures observed. |
| `make cuda-regression` | **Not run** | No CUDA host available. **No claim is made about CUDA behaviour** -- it is untested. |
| Quant/`quality-score` track | **N/A** | No GGUF or numerics change. |

### Gate results

Engine stopped 21:16:46, restarted 22:58 (PID 83032, verified answering on :8002, 0
errors in the log since restart).

| Gate | Result |
|---|---|
| `make clean && make` on the upstream base | **Pass** — 0 errors, 0 warnings, repeatedly |
| `./ds4_test --server` | **Pass** — `server: OK`, `ds4 tests: ok` |
| full `make test` | **Cannot run here.** Unpatched `b3f5018` produces the **identical 17 failures**, so the patch is exonerated by control. Root cause is deeper than RAM: every DeepSeek V4/V4.1/Vision-Exp GGUF on this host fails to load in *upstream's* build (`required metadata key is missing: deepseek4.block_count`) — only the Qwen3.8 file loads, at 177 GB. |
| Speed, before/after, `prefill_tps` and `gen_tps` | **Parity.** Order A/B/B/A, 32 ctx points (2048–65536), 2 reps/arm: prefill 115.81 → 116.42 tps (**+0.52 %**) vs same-arm spread 0.92 %/0.50 %; gen 23.08 → 23.01 (**−0.27 %**) vs 0.13 %/1.47 %. Both deltas inside repeatability. |
| Execution proof on the upstream tree | **Pass.** 10 real stores, `fsync=0/0 fullsync=0/0` every time → `F_FULLFSYNC` honoured, not ignored. See `proof-execution-upstream-tree.md`. |
| Independent review of the exact sha | **Done, 2 findings, both fixed.** Tolerated-`F_FULLFSYNC` errno was leaking into upstream's `saved_errno` and could mislabel a later `fclose` failure; and the comment claimed load-time validation by a "tokenizer fingerprint" that **does not exist upstream** (0 occurrences; it is ours, unmerged, #1020). |
| `make cpu` | Builds on macOS. **No Linux claim** — see *Platform scope*. |
| `make cuda-regression` | **Not run. No CUDA claim.** |

Two things the speed work changed in the claims, not just the table: the first
single-rep pair showed `gen_tps` at **−1.02 %**, which a second rep dissolved (22.85
vs 23.18 within the patched arm). And the cost numbers moved: the barrier is ~4 ms
when the kernel has already written the data back, but **17–48 ms** when it has not,
so the earlier "~2.4 % of store time" was the favourable case only. `PERFORMANCE.md`
asks for alternating repeats precisely because of the first effect; had I filed on one
rep each I would have shipped a regression that did not exist, and a cost claim that
understated the unfavourable case by ~10x.

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

Measured on this tree with temporary instrumentation around **real** stores
(internal NVMe, `--ssd-streaming` active, 168–195 MiB checkpoints, 7 stores):

| situation | barrier cost |
|---|---|
| first barrier, pages still dirty | 17 – 48 ms |
| second barrier, same file, nothing pending | **3.7 – 4.0 ms** |
| fork production build, external NVMe, ~647 MiB stores, `save=` ~166 ms | 4.05 ms mean, n=6 |

So the calls themselves cost ~4 ms; the rest is writeback the kernel had not yet
performed. That write is not new work — without the barrier it happens asynchronously
later, or not at all before a power loss — but it now lands inside the store's
synchronous window, which is the honest way to state the cost. A store whose own
duration is long enough for background writeback to finish sees only the ~4 ms floor.
Both syscalls returned 0 with errno 0 on all ten stores, so `F_FULLFSYNC` is honoured
here rather than silently accepted.

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

**Full `make test`: not completed, and I'd rather say so than bury it.** On this
machine the unmodified base commit fails the same 17 cases as this patch (control run,
same command), so the failures are not introduced here. The cause is that the GGUFs I
have locally cannot be opened by this build at all — `required metadata key is missing:
deepseek4.block_count` for the DeepSeek V4/V4.1/Vision-Exp files — so the model-backed
cases never start. The subset that covers KV disk-cache bookkeeping does pass:
`./ds4_test --server` → `server: OK`.

**Executed, not just compiled:** with temporary instrumentation on this tree, ten real
checkpoint stores each logged `fsync=0/0 fullsync=0/0` — both calls succeed and
`F_FULLFSYNC` is honoured rather than silently accepted. The instrumentation was
reverted before this commit was finalised; the tree I pushed builds warning-free with
no probe code in it.

### Deliberately not included

- **No directory `fsync` after `rename`.** Losing a rename loses a checkpoint, which is
  a safe miss.
- **No payload checksum.** The more complete fix — it detects corruption from any cause,
  not just reordering — but it changes the on-disk format and adds a CPU pass over
  hundreds of MiB. Happy to open a discussion.
- The trailer rewrite opens the live file with `fopen(path, "r+b")` and truncates in
  place, a related atomicity gap. Separate change.
- Two sibling call sites in `ds4_agent.c` write the same envelope through
  tmp→rename with no barrier either. Left alone deliberately: this patch fixes the
  disk-cache path the server reloads, and widening the blast radius makes a small
  correctness fix harder to review for no extra safety.
- Payload checksum, which would detect corruption from any cause rather than closing
  one window for it. It is the better fix and a much bigger conversation.

## Review posture

Expect pushback on `F_FULLFSYNC` cost on slower media; the fallback path and the
measurement table are the answer. If a maintainer prefers `fsync`-only, that is still a
strict improvement and should be accepted rather than held up — the bug is the missing
barrier, not the choice of barrier.
