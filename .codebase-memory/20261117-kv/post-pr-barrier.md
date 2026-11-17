# Post: new PR against antirez/ds4

Branch `nazerim:fix/kvstore-write-barrier` → `antirez/ds4:main`. One commit `dcf77c4`,
+47/−0, `ds4_kvstore.c` only, base `b3f5018` (= current `origin/main`).

Evidence retained in our repo: `20261117-kv/proof-execution-upstream-tree.md`,
`maketest-control-b3f5018.log`, `server-suite-dcf77c4.txt`, `barrier_probe.py`.

**Title:** Barrier checkpoint writes before the rename that exposes them

---

## Problem

`ds4_kvstore` publishes a checkpoint by writing `<path>.tmp.<pid>`, `fflush`ing,
`fclose`ing and `rename`ing it into place. Nothing between the last `fflush` and the
`rename` ensures the bytes have left the page cache — `ds4_kvstore.c` contains no `fsync`
at all (count 0 at `b3f5018`).

Entries are found by the sha1 of their prompt text and, on load, accepted on the strength
of the envelope: magic and header version, the payload-ABI byte, file size not below the
header's expectation, the header token count against the restored token count, and the
payload's own magic/version/layout tags. The stored text is re-hashed and compared against
the file name. **The KV rows behind it are not checked** — there is no checksum over the
payload anywhere in the file.

So a checkpoint whose *directory entry* survived while its *data* did not is accepted as a
valid cache hit carrying stale or zeroed rows. That is the difference between a slow
session and a wrong one: the load succeeds, the session resumes, and the next miss looks
like model behaviour.

A plain process crash is already safe — the kernel keeps the bytes and finishes writing
them. The exposure is a kernel panic (dirty pages die with the RAM while the rename can
still land through the journal), power loss, or a device that drops a volatile write
cache. This project documents targets where external NVMe is the norm — `docs/DGX_SPARK.md`
and `docs/STRIX_HALO.md` — which is the configuration this hazard needs.

## Change

`fsync(fileno(fp))` after the final `fflush`, before `fclose` and `rename`; on Apple
platforms additionally `fcntl(fileno(fp), F_FULLFSYNC)`, which asks the device to commit
what it holds in its own write cache. Both run while the file is still the hidden `.tmp`
name, so no observer can ever see a partially durable entry.

- **A barrier failure is a failed store.** `ok = false` ⇒ the existing cleanup skips the
  rename and unlinks the temp file. No visible-but-broken entry, no leaked temp file.
- **An unsupported ioctl degrades instead of failing.** `EINVAL`/`ENOTSUP`/`EOPNOTSUPP`
  leaves the checkpoint standing on `fsync` alone. Tolerating it also clears `errno`, so a
  rejected ioctl cannot later be reported as the reason an `fclose` appeared to fail.
- **`fsync` runs first** because `F_FULLFSYNC` on its own does not flush the buffer cache.
- **A compile-time `#error` on Apple builds if `F_FULLFSYNC` is not visible.** Not
  decoration: without `<fcntl.h>` the macro is undefined, the `#if` below it is skipped
  silently, and this becomes an `fsync`-only barrier with a clean warning-free build. I hit
  exactly that while writing the patch.

## Cost

Measured in a build of this path with temporary instrumentation around real stores (the
instrumentation is not in the diff; `grep -c BARRIER-PROBE` is 0 on the pushed commit):

| configuration | first barrier | second barrier, same file, nothing pending |
|---|---|---|
| this tree, internal NVMe, `--ssd-streaming`, 167–218 MiB checkpoints, Qwen3.8, 12 stores | **16.7 – 51.6 ms** | **3.7 – 4.1 ms** |
| our fork's build of the same path, external NVMe (journaled APFS), ~647 MiB checkpoints whose unbarriered `save=` was already ~166 ms | 4.05 ms mean over 6 stores | — |

The two runs bracket the real cost rather than agreeing, and the gap is not noise: the
calls themselves cost ~4 ms, and everything above that is writeback the kernel had not yet
performed. That write is not new work — without the barrier it happens asynchronously
later, or not at all before a power loss — but it now lands inside the store's synchronous
window. A store long enough for background writeback to finish sees only the floor.

Both syscalls returned 0 with `errno` 0 on all twelve stores, and the barrier's cost tracks
the amount of dirty data, which is consistent with real commits reaching media on the
internal volume. A success code alone cannot prove that for an external bridge, and I have
not tested one here.

Speed, since this is inference software: same machine, model and sweep, alternating order
(A/B/B/A, 2 reps per arm) because one run is not a speed result.

| | baseline `b3f5018` | this patch | delta | largest same-arm spread |
|---|---|---|---|---|
| `prefill_tps` (mean over 32 context points) | 115.81 | 116.42 | **+0.52 %** | 0.92 % |
| `gen_tps` | 23.08 | 23.01 | **−0.27 %** | 1.47 % |

Each delta is smaller than the largest same-arm spread, so: no measurable regression. And
to be precise about what that evidence does and does not cover — `ds4_bench` has no
references to `ds4_kvstore`, so the sweep cannot exercise this patch at all. It is a
control on the inference path; the store numbers above measure what actually changed.

## Testing, and what is missing

- `make clean && make` on the upstream base: 0 errors, 0 warnings.
- `./ds4_test --server` → `server: OK`, `ds4 tests: ok`. That subset is model-independent
  (it does not open an engine), which is why it passes where the model-backed groups below
  cannot.
- **Full `make test` did not complete here, and I would rather say it than bury it.** The
  unmodified base commit fails the identical 17 cases (control run, same command), so
  nothing below is introduced by this patch — but I cannot claim a green suite. The
  reasons are environmental and mixed: most are `ds4_engine_open` refusals because the
  V4.1 test file needs ~156 GiB against a ~112 GiB safe budget; the DeepSeek V4-era files
  I also have are refused outright by this build (`required metadata key is missing:
  deepseek4.block_count`); and at least one is a model-free server unit assertion that
  fails on this base regardless. Tell me which fixture you run CI against and I will
  re-file with a full pass.
- **No new test, deliberately.** The store path needs a live engine and session
  (`ds4_session_write_staged_payload`), so it is not reachable from a model-free test, and
  the durability effect itself needs fault injection. I did not want to fake coverage for a
  change whose entire value is that the old code looked like it worked.
- Machine: Apple M5 Max, 128 GiB, macOS, Metal backend. Checkpoints went to an external
  NVMe/APFS volume in production and to the internal volume in the upstream-tree run; both
  are named because the cost differs between them.

**Platform scope: built and tested on macOS/Metal only. I am making no claim about Linux
or CUDA** — neither was built on nor run on such a host. The `fcntl` call sits under
`#if defined(F_FULLFSYNC)` and the `#error` fires only under `__APPLE__`, so a platform
without that macro compiles to `fsync` alone; that is a reading of the preprocessor, not a
measurement.

## Interaction with open work

#1343 edits `ds4_kvstore_store_live_prefix_text`, the same function this inserts into, in
the `fwrite` chain whose final `fflush` is the barrier's anchor. I expect at most a trivial
conflict and will rebase onto whichever lands first rather than leave that to you.

## Deliberately not included

- **No `fsync` of the directory after `rename`.** Losing a rename loses a checkpoint, which
  for a content-addressed cache is a safe miss. The hazard closed here is the reverse one:
  an entry that survives while its data does not.
- **No payload checksum.** The better fix — it detects corruption from any cause instead of
  closing one window for it — but it changes the on-disk format and adds a pass over
  hundreds of MiB. Happy to open a discussion.
- **Two sibling call sites in `ds4_agent.c`** write the same envelope through tmp→rename
  with no barrier either. Left alone deliberately: this fixes the disk-cache path the
  server reloads, and widening the blast radius makes a small correctness fix harder to
  review for no extra safety.
- The trailer update path opens an existing checkpoint with `fopen(path, "r+b")` and
  rewrites the trailer in place — a related atomicity gap, separate change.
