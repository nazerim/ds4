# Fix: KV checkpoint write barrier (durability before rename)

**Verdict (one line):** Added a `fsync` durability barrier on the store tmp file
immediately before the `rename` that makes a checkpoint visible, so a torn/stale
payload can no longer be renamed into a live, wrong-output entry; measured cost
~2.2 ms/store on the real KV volume, warning-free build, suite green 3×.

Diff: 1 file (`ds4_kvstore.c`), +22 lines, 0 deletions. Uncommitted.

---

## 0. Re-verified anchors (line numbers had drifted)

| Claim | Original ref | Actual | Status |
|---|---|---|---|
| tmp name `"%s.tmp.%ld"` | `:3299` | `ds4_kvstore.c:3315` | confirmed |
| `save=` timing start (before `fopen`) | `:3301` | `:3317` (`save_t0 = kv_now_sec()`) | confirmed |
| `fflush`/`fclose`/`rename` store tail | — | `:3384` fflush, `:3408` fclose, `:3422` rename | confirmed |
| no `fsync` anywhere in KV store path | — | grep: `fsync` only at `ds4_agent.c:7193` | confirmed |
| sha in log line + "hash/prefix mismatch" comment refer to sha1 of the **prompt text** (== file name) | `:3630-3634`,`:4009-4011` | load validates `ds4_kvstore_sha1_bytes_hex(cached_text,…) vs e.sha` at `:3688-3696`; comment `:3440`; `:4009` area | confirmed |
| payload rows are NOT checksummed | — | load checks header/magic, model_id, text len, **text** sha, byte-prefix, tokenizer fingerprint (`:3672-3738`); nothing hashes the KV rows | confirmed |

Fact #2 holds: the only integrity signal on the payload is the *tokenizer
fingerprint trailer* (`kv_file_tokfp_ok`, `:2738`), which is stamped by the
engine, not a content hash of the rows. A file renamed with rows still only in
the page cache can come back stale/zeroed and pass every load check → **wrong
model output**, the one path in the investigation that is not a cache miss.

---

## 1. F_FULLFSYNC settled in C

Throwaway C programs in the session temp dir (not the repo). Device:
**Seagate FireCuda 520** (`ZP2000GM30002`), NVMe, `Device Location: External`,
protocol PCI-Express, journaled APFS (`/dev/disk5s1`, `noowners`).

`fullsync_probe2.c` — write ~256 MiB, then time each barrier against
freshly-dirtied data (separate runs so each sees clean dirty):

| op | run1 | run2 | run3 | ret/errno |
|---|---|---|---|---|
| `fsync(fd)` 256 MiB | 2.9 ms | 3.2 ms | 3.0 ms | 0 / 0 |
| `fcntl(fd, F_FULLFSYNC)` 256 MiB | 4.6 ms | 4.4 ms | 6.1 ms | **0 / 0** |
| `fcntl(fd, F_BARRIERFSYNC)` 256 MiB | 4.5 ms | 4.5 ms | 4.6 ms | 0 / 0 |
| `fsync` 32 MiB | 2.4 ms | — | — | 0 / 0 |
| `F_FULLFSYNC` 32 MiB | 4.2 ms | — | — | 0 / 0 |

(First probe `fullsync_probe.c` also showed F_FULLFSYNC ret=0; its 1.6 ms was
measured *after* an `fsync` had already pushed the data, hence anomalously low —
the isolated runs above are the honest numbers.)

**Conclusion:**
- **F_FULLFSYNC works from C** (ret=0, errno=0). The Python EFAULT in fact #4 was
  a calling-convention artifact, exactly as suspected — not ENOTSUP.
- Cost ≈ **1.5–2× `fsync`** here (~4.5 ms vs ~3.0 ms at 256 MiB; ~4.2 vs 2.4 at
  32 MiB). It is NOT 10–100× fsync.
- The delta over `fsync` is small and noisy. For a genuine media-cache flush of a
  freshly-dirtied 256 MiB on an NVMe SSD behind an external bridge that is too
  cheap to be trusted: either the drive/bridge has effectively no volatile write
  cache to force (NVMe drives with power-loss protection), or the bridge **drops
  the flush and F_FULLFSYNC behaves like the APFS journal/barrier** (`F_BARRIERFSYNC`
  ≈ F_FULLFSYNC ≈ fsync, all ~3–4.5 ms, which is consistent with the journal
  dominating and the media command being a near no-op).
- **Decision: use `fsync`.** It is the standard, portable, cheap barrier that
  makes the payload durable through the APFS journal before the rename — which
  closes the renamed-but-unwritten window. F_FULLFSYNC adds ~2 ms and its extra
  media-forcing is **not verifiably effective on this external bridge**, so it
  buys no correctness we can rely on. Trusting F_FULLFSYNC here would be trusting
  a command the bridge may silently ignore.

---

## 2. Site enumeration — every place that makes checkpoint bytes visible or mutates them in place

| # | Site (function @ line) | Effect | Barrier needed? | Action taken |
|---|---|---|---|---|
| 1 | `ds4_kvstore_store_live_prefix_text` — tmp `fopen(...,"wb")` @ `:3318` → rename @ `:3422` | Makes a NEW checkpoint **visible** | **Yes** (this is the defect) | **Implemented `fsync(fileno(fp))` after `fflush`, before `fclose`/`rename`.** |
| 2 | `ds4_kvstore_rewrite_trailer` — `fopen(...,"r+b")` @ `:3061` + `ftruncate(fileno,@end)` @ `:3074` | **In-place** truncate+rewrite of a LIVE file's trailer + header flags | No — a barrier can't fix it (see defect §3) | **Left unchanged, defect described** |
| 3 | `ds4_kvstore_touch_file` — `fopen(...,"r+b")` @ `:746` | In-place, **size-preserving** header-only refresh (hits/last_used/level/stale); identity/version fields are read-back-and-rewritten; payload region never touched | No | Left unchanged (justified: cannot misrepresent KV rows; worst case a stale counter, or a torn header that fails magic → **cache miss**, not wrong output) |
| 4 | `fopen(...,"rb")` reads @ `:681,899,956,2382,2808,2909,3579,3660,3766,3862` | Read-only | No | n/a |
| 5 | unlink / cache-drop / crash-orphaned-tmp reaper @ `:1974,2390,3004,3118,3430,3838,3929` (+ reaper `:2355–2390`) | Deletion / cleanup only | No | n/a |

Only site #1 was changed. The `ds4_agent.c:7193` reference path already `fsync`s
the raw fd before its `rename`/`link` — correct and in scope of a different
module, left untouched.

### Error / leak analysis for the implemented barrier

Production control flow around the new lines (`:3408` onward, unchanged):
- rename is guarded: `if (ok && rename(tmp,path) != 0)` — a barrier failure sets
  `ok=false`, so **rename never runs**.
- the `!ok` branch calls `unlink(tmp)` — so a barrier failure **cleans the tmp**.
- `saved_errno` is captured after the barrier, so the failure is reported via the
  existing `kv cache store failed (%s): <strerror>` log and the `err` out-param.

Net: **a barrier failure is treated as a failed store — no visible-but-broken
entry, no leaked tmp.** (A barrier failure is never fatal to cache *correctness*:
the session just doesn't get persisted this round; the next store retries.
Silently proceeding and renaming an un-fsynced file would be the unsafe choice.)

errno reasoning: line `:3377` sets `errno=0`; the short-circuit `&&` chain leaves
it 0 on the success path and sets it on any earlier failure. The `if (ok)` guard
means a failed write chain skips the barrier entirely (identical to prior
behaviour); a successful write chain runs `fsync` and `saved_errno = errno`
captures an `fsync` error. No path reads a stale errno as if it were the barrier's.

### `save=` / `sync=` measurement

The barrier sits inside the existing `save_t0`→`save_ms` window (`:3317` →
`:3424`), so its cost is already inside the reported `save=` number — it stays
observable. I added a `sync=` sub-measurement gated by the existing
`kv_debug_enabled()` (`DS4_KV_DEBUG`): it emits
`DBG store sync=%.1f ms tmp=%s` only in debug, so the barrier cost is
attributable later at zero production cost / log noise.

---

## 3. Trailer in-place rewrite defect (deliberately NOT fixed)

`ds4_kvstore_rewrite_trailer` (`:3051`) opens the **live** path `r+b`, seeks to
`end = hdr_total + text_bytes + payload_bytes` and `ftruncate`s the trailer off
the live file (`:3074`), then writes the new trailer and flips `ext_flag` in the
header in place (`:3077`,`:3108`), then `fclose`. The payload region `[0,end)` is
never touched (good — that path cannot corrupt the KV rows themselves).

But the sequence is **not atomic**: between the in-place truncate and the new
trailer/header becoming durable, a crash can leave the live file with the old
trailer gone and the header flag inconsistent. The existing safety net
(`if (!ok) unlink(path)` @ `:3118`) only fires on a failure **detected in the
same process**; a crash/power-loss bypasses it, so a half-applied rewrite can
survive on disk as an inconsistent envelope.

**A payload barrier does not fix this** — fsync makes bytes durable but does not
make a multi-step in-place truncate+rewrite atomic. The correct fix is
write-then-rename for the trailer update (same shape as the store path). That is
a separate refactor and was left untouched per instructions.

---

## 4. Cost A/B (empirical)

Because the store function needs a live engine+session (see §5), the A/B uses a
**faithful replica of the store I/O tail** — the exact `save=` window:
`fopen tmp "wb"` → `fwrite` header + payload (MB chunks, same fwrite the staged
copy uses) → `fflush` → [`fsync` barrier] → `fclose` → `rename`. Nothing in that
window depends on the engine. Toggle the barrier on/off, 40 iterations each.

| Volume | payload | barrier OFF (store-loop mean) | barrier ON (store-loop mean) | **fsync-only mean / max** |
|---|---|---|---|---|
| External `/Volumes/FireCuda520` (the real KV volume) | 128 MiB | 31.4 ms | 32.1 ms (**+0.7 ms**) | **2.25 ms / 5.71 ms** |
| External `/Volumes/FireCuda520` | 384 MiB | — | 95.3 ms | **2.19 ms / 5.64 ms** |
| Internal APFS (session temp) | 128 MiB | 15.4 ms | 13.4 ms (within noise) | **0.49 ms / 0.63 ms** |

**Headline: barrier ≈ 2.2 ms per store on the external NVMe KV volume.** The
in-loop marginal delta (~0.7 ms) is *smaller* than the direct fsync time because
the 128–384 MiB `fwrite` stream already pushes much of the data toward the device
before the final fsync, so the terminal fsync finds less dirty than an isolated
probe. The fsync-only figure (2.2 ms) is the conservative upper bound; both are
consistent with the pre-established ~2.4 ms.

Against the established `save=` tail (104–175 ms) and the mean executor hold
(1.55 s): 2.2 ms is **~0.15% of a hold**, ~1–2% of the store's `save=` —
affordable, and it lands inside `save=` so it stays observable without a new
metric.

Note the device distinction (per instructions): the KV store runs on the
**external NVMe** (checkpoints live on `/Volumes/FireCuda520`), where the relevant
number is ~2.2 ms; internal APFS is a different, faster device (~0.5 ms) and is
shown only for reference.

---

## 5. Tests & mutation evidence

### What is and is not model-free testable

The model-free suite (`make test-server-logic`, `ds4_server.c` behind
`DS4_SERVER_TEST`) does **not** reach the barrier:

- `test_kv_delta_stub_file` / `test_kv_text_stub_file` / `test_kv_conv_stub_file`
  write checkpoint-shaped files **directly** with `fopen "wb"`+`fwrite`+`fclose`
  (`ds4_server.c:25907` etc.). They never call `rename` or `fsync` — they are a
  real-file *fixture* for the read/compat/lookup predicates, not the store path.
- The only real store invocation is `ds4_kvstore_store_live_prefix_text` @
  `ds4_server.c:12960`, which needs a live `engine` (renders token text) and a
  `session` with a valid staged checkpoint (`ds4_kvstore.c:3184`,
  `:3211`, `:3234`, `:3248`). It cannot run without a model+Metal, i.e. only
  under `make test` (engine stopped) — which is off-limits in this window.

**Therefore the store function has no clean model-free seam.** I did NOT write a
repo test that pretends to exercise it (per the honesty instruction). Mutation of
the new lines is invisible to the existing suite — stated plainly in §6.

### Existing suite = regression gate (my object links, nothing breaks)

`make test-server-logic` rebuilt `ds4_server_test` against the modified
`ds4_kvstore.o` and passed **3/3** (`ds4-server tests: ok`, exit 0), deterministic,
**no `/tmp` litter** (`/tmp/ds4-kv-*`: 0 before, 0 after). This proves no
regression to shared helpers, but it does not cover the barrier.

### Mutation evidence for the invariant the barrier relies on

`barrier_invariant.c` (temp dir) reproduces the production orchestration shape
around the new barrier — `ok = … && fflush==0; if (ok){ BARRIER }; fclose;
if (ok && rename) ok=0; if (!ok) unlink(tmp);` — with the barrier failure forced,
and a guarded (production) vs mutated (rename-ignores-barrier) variant:

```
GUARDED  success-case  (visible,no-tmp): GREEN ok
GUARDED  barrier-fail  (absent,no-tmp): GREEN ok
MUTATED  barrier-fail  (absent,no-tmp): RED FAIL (expected: leak caught)  <-- proves the guard bites
```

- GREEN, GREEN: with the production guard, a barrier failure leaves **no tmp and
  no visible entry**, and a success leaves a complete file with the tmp gone.
- RED: with the `if (ok && …)` guard removed (the mutation), the barrier failure
  now renames the un-fsynced tmp → a **visible-but-broken entry leaks**. This
  demonstrates the guard the barrier depends on is exactly what prevents the
  leak, so the invariant is mutation-sensitive.

This validates the *logic shape*; it does not claim coverage of the production
store function (which has no seam — see above).

---

## 6. Could-not-verify list (explicit)

1. **Production store path end-to-end** — that a real `ds4_kvstore_store_*` call
   with the barrier produces a loadable, complete file and removes its tmp: not
   runnable model-free (needs engine+session+Metal, only under `make test` with
   the engine stopped). Not exercised by `make test-server-logic`.
2. **The durability *effect*** — that a torn/stale payload is actually prevented
   on a real crash/power loss. Requires fault injection / panic-after-write /
   physically pulling power on the shared external volume; unsafe and out of
   scope. The barrier's *mechanism* (fsync-before-rename) is standard and the
   orchestration invariant is mutation-checked, but the real-crash outcome is
   unverified by execution.
3. **Whether the external NVMe bridge honours F_FULLFSYNC at the media** (vs
   treating it as the journal flush ≈ F_BARRIERFSYNC ≈ fsync). The near-fsync
   measured cost strongly suggests it is not doing meaningful extra media forcing
   here, but what the drive physically does on genuine power loss is not
   provable from software.
4. **Directory-entry durability of the rename itself** — I did NOT fsync the
   containing directory after `rename`. Its failure mode is the checkpoint
   *disappearing* after a crash → a cache miss (safe, never wrong output), so it
   is outside the correctness defect; left unbarriered to keep the change
   minimal. Flag for the operator if "checkpoint must never be lost" is a
   requirement (it would cost a dir fd open + fsync per store).
5. **Non-macOS behaviour (CUDA/ROCm/Linux)** — the change is portable C using
   `fsync`/`fileno` (already used in this file via `ftruncate(fileno(...))` @
   `:3074` and in `ds4_agent.c` under the same `_GNU_SOURCE`/`-std=c99` flags),
   but I compiled/tested only on macOS Metal. Not exercised: Linux `fdatasync`
   vs `fsync` semantics; APFS-vs-ext4 journal-durability differences.

## 7. Deliberately NOT changed

- **Trailer in-place rewrite** (`ds4_kvstore_rewrite_trailer`) — atomicity defect
  needs write-then-rename; separate change, out of scope (§3).
- **`ds4_kvstore_touch_file`** — metadata-only, size-preserving, cannot produce
  wrong output; no barrier (§2 row 3).
- **`ds4_agent.c:7193`** — already correct (fsync before rename); different module.
- **Directory fsync after rename** — failure = cache miss, not wrong output (§6.4).
- **Store-length logic / `unlink`+children comments from `2b06dc7`/`f90433f`**
  (`kv_cache_maybe_store_continued`) — excluded by the task; untouched. The diff
  touches only the store write block.

---

## 8. Verification summary

- Full `make`: **MAKE_EXIT=0, zero warnings** (`grep -c warning:` = 0).
- `nm ds4_kvstore.o`: **no test symbols**; references `U _fsync`, `U _fileno`
  (barrier present in the production object).
- Barrier comment + `fsync(fileno(fp))` each appear exactly once in the source.
- `make test-server-logic`: green **3×** (exit 0), deterministic, **no `/tmp`
  litter**.
- Artifact guard (`git ls-files` grep, `git check-ignore tests/kv_policy_harness`):
  clean.
- Engine PID **69332 still running** (not restarted, no traffic sent, no `make
  test` run).
- `git status --short`: ` M ds4_kvstore.c` only. `git diff --stat`: 1 file,
  **+22 / -0**. Changes left uncommitted.
