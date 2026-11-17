# KV cache audit — correctness and durability

Research only. No repo file was modified; PID 64188 was not touched; logs were
read aggregated.

Every `file:line` and number below was read or measured in this session. Where
something is an inference rather than a measurement it says so. `UNVERIFIED`
marks what I could not settle.

Config under audit (from `log/ds4-qwen.log:13-18`, the running engine's own
banner): `ctx=524288`, `resident_sessions=2`, `anchor_step=8192`,
`continued_step=8192` doubling above 49152, `prefill_chunk=4096`,
`small_dense=49152`, `tail=2`, `mid_spacing=131072`, `min_anchors=4`,
`max_conversations=0`, `cross-quant=accept`, budget 262144 MiB.
Snap-down is ON (no `DS4_KV_CONTINUED_SNAP` in the environment).

Log used for measurement: `log/ds4-qwen.log.20261117-094911` (1115 07:00 →
1117 09:49). Unless stated otherwise every count comes from that file.

---

## Ranked findings

| # | Finding | Class | Live? | Measured impact |
|---|---|---|---|---|
| 1 | Continued store writes a header/payload token mismatch; the file can never load | lost durability | yes | **144 files / 89.03 GiB**, 41 discards, 19.0 GiB of reads thrown away |
| 2 | Divergence anchor fires on every chunk instead of once at the target | wasted I/O + budget | yes | **342 stores / 44.4 GiB in 12 min**, 97.4 % of all cold-store bytes |
| 3 | No `fsync` before `rename`; a torn payload is structurally valid | wrong output (potential) | yes | 0 observed; the whole 280 GiB written in this log is exposed |
| 4 | Loader `unlink`s without the children check → orphans descendants | lost durability | latent | 1 orphan on disk now (0.57 GiB); 41 unlink events bypassed the check |
| 5 | Deferral machinery never converges on the delta ladder | budget pressure | yes | 14 352 deferral lines over 856 files (~17 each) |
| 6 | `kv_cache_refresh` is O(files) and runs 2× per store / 1× per load | wasted I/O | yes | 751 files now, `cold_max` 30000 |
| 7 | Trailer rewrite truncates in place (not atomic across a crash) | silent degradation | yes | rare |
| 8 | `hit_half_life` / `hits` are dead retention inputs | cosmetic | yes | — |

Audited and **not** defects: `cross-quant=accept` (§9.1), the
`key=token-text` vs `key=visible-transcript` split (§9.2), the
trailer-corrupt "continue without it" path (§9.3), and the sha/prefix keying
(§9.4).

---

## 1. Continued store writes a header/payload token mismatch — the file can never load

**One line.** `kv_cache_maybe_store_continued()` passes the snapped-down grid
`target` as the store length while the payload is staged from the *live*
checkpoint, so the header records fewer tokens than the payload holds, and the
loader's terminal check rejects the file on every load.

**Severity: lost durability** (a rung of the reuse ladder is written, charged to
the budget, and can never serve as a load target) **plus wasted I/O**
(19.0 GiB of reads and 41 full payload writes over two days). Not wrong output:
the length check fails closed.

### Evidence

The invariant is written down in the code, 24 lines above the violation:

```
ds4_server.c:13117  /* No continued-store boundary here, but a divergence anchor may be
ds4_server.c:13118   * pending and reachable.  Store it at the CURRENT live length (the
ds4_server.c:13119   * payload graph is the session's, so header tokens MUST equal the
ds4_server.c:13120   * session length — storing at `common` with a full-session payload
ds4_server.c:13121   * produced corrupt files on load). ... */
...
ds4_server.c:13135            const int store_len = tokens->len;          <-- correct
ds4_server.c:13136            if (kv_cache_store_live_prefix(s, slot, tokens, store_len, "cold"))
...
ds4_server.c:13143    if (kv_cache_store_live_prefix(s, slot, tokens, target, "continued")) {
ds4_server.c:13144        kv_cache_slot_note_store(slot, target);
```

The vision path enforces the same invariant explicitly:
`ds4_server.c:12766  bool okv = vs->valid && store_len == tokens->len && ...`

The library permits the mismatch:
`ds4_kvstore.c:3147-3149` rejects only `live_tokens->len < store_tokens.len`.

The payload is the whole live session:
`ds4.c:66475  const uint32_t rows = (uint32_t)s->checkpoint.len;` (inside
`qwen4_session_save_payload_span`, reached from
`ds4_session_stage_payload_impl` at `ds4.c:66253`, which takes no length).

The loader rejects it after reading the payload:
`ds4_kvstore.c:3872  if (loaded_tokens && loaded_tokens->len == (int)hdr.tokens)`.

**There is a fourth corroboration in history**: `969bc97` (2026-08-08) fixed
exactly this bug for the *divergence* path. Its message: "the store fired at the
target (common) while `ds4_session_stage_payload` stages the FULL session graph
- so the KV header recorded `store_len` tokens but the payload restored the full
session length. On load: `loaded_tokens->len != hdr.tokens` -> 'corrupt
text-prefix payload'". The `continued` path at `13143` was not covered by that
fix and still has it.

### How I know it is real (measured, not inferred)

Store log lines carry both numbers (`sorted by log order, last store per sha`):

```
distinct shas stored: 737
  last store trimmed != 0 : 145   89.66 GiB
  last store trimmed == 0 : 592  160.37 GiB
    continued n=144  89.42 GiB
    cold      n=  1   0.24 GiB
```

Joining those 145 sha prefixes to the on-disk directory
(`/Volumes/FireCuda520/ds4-kv-qwen`, 751 `.kv` files, 255.9 GiB):

```
doomed (trim!=0)  n=145  present_on_disk=144  logged 91 162 MiB = 89.03 GiB
good   (trim==0)  n=592  present_on_disk=583  logged 160 471 MiB
doomed share of the indexed budget: 36.2 %   (34.8 % of the 256 GiB ceiling)
```

And the loader's behaviour matches exactly:

```
41 "discarded corrupt text-prefix payload" events, 37 distinct shas
41 of 41 discarded files were last written by a store with trimmed != 0
 0 of 41 by a store with trimmed == 0
36 of the 37 are back on disk today and only 2 are still mismatched
 -> the system self-heals by re-storing, at the cost of one full read + one full write each
```

Worked example (`sha e8877a36…`): stored at log line 528 `tokens=81920
trimmed=705 reason=continued delta=65536..81920`; discarded at 1203; re-stored
at 1214 `trimmed=0` (session rebuilt to exactly 81920); first hit at 1644.
Every trim magnitude in the log is small — 68 stores 1-255, 83 at 256-1023, 31
at 1024-2047, 4 at 2048-4095, 1 at 4096+ — i.e. exactly the "post-prefill live
is a partial chunk past the grid" case, not an exotic one.

### Is the snap-down itself wrong?

The snap-down (`ds4_kvstore.c:2656-2658`) is *not* wrong as a scheduling
decision, and the 2026-09-30 rationale in the test
(`ds4_server.c:25229-25240`: a resumed off-grid session wrote zero rungs and
paid 19.5 GiB) is sound. What is wrong is using the scheduling answer as the
*store length*. The two roles were conflated. Storing at `tokens->len` keeps the
ladder and satisfies the header/payload invariant; the only cost is that an
off-grid rung is not `tokens % 4 == 0` and therefore cannot be a delta parent
(`ds4_kvstore.c:2771`), which costs sharing, not correctness.

### Is unlinking the right loader response?

Yes for the *terminal* — the file is genuinely unloadable, and I checked that it
is not recoverable for a shorter or longer prompt either: the loader splices the
suffix at `prompt_text + text_bytes` (`ds4_kvstore.c:3880`) while the payload
token array is `live > store_len` long, so the effective prompt would either
double-count `live - store_len` tokens (longer prompt) or sit ahead of the
request (shorter prompt). The header, text and payload disagree about where the
history ends; no consumer can repair that by reading.

What is *not* right is the unlink itself — see finding 4.

### Minimal fix

One line, in the caller:

```c
/* ds4_server.c:13143 */
-    if (kv_cache_store_live_prefix(s, slot, tokens, target, "continued")) {
-        kv_cache_slot_note_store(slot, target);
+    if (kv_cache_store_live_prefix(s, slot, tokens, tokens->len, "continued")) {
+        kv_cache_slot_note_store(slot, tokens->len);
```

Belt-and-braces in the library, so the class cannot come back through a new
caller — make the invariant explicit rather than permissive:

```c
/* ds4_kvstore.c:3147 — require exact equality, or make the caller say
 * it really wants a prefix (which cannot be staged today). */
  if (!live_tokens || live_tokens->len != store_tokens.len) { ... skip ... }
```

That second change is the one to think about: with `!=` the store refuses when a
caller passes a shorter prefix, which converts a silent wrong file into a
skipped store. Both are a cache miss; only one is a budget leak. I would land
the caller fix and a *log-only* assertion in the library first, then tighten to
`!=` after one week of clean logs.

**Format/version impact:** none. Same header, same payload, same names. The only
change is what value goes in `tokens`, and what text gets hashed as the key.

**Risk:** off-grid rungs are not `%4`-aligned parents, so delta sharing thins.
Counter-evidence that it is affordable: the ADR already accepts 16 384-token
spacing above 48k, and the 41 discards show the current rungs are not usable
anyway.

### Regression test

Model-free, in `DS4_SERVER_TEST` (this is the layer the existing tests stop
short of — see §10):

```
test_kv_store_refuses_prefix_shorter_than_live
  - constructs a kv_disk_cache and asserts that
    ds4_kvstore_store_live_prefix_text(kc, ..., store_len = live-100, ...)
    either fails or writes a file whose header tokens == the payload's rows.
```
A fully faithful version needs a real session (the payload is engine state), so
the *unit* test should pin the guard, and the model-backed half belongs in
`tests/ds4_test.c::test_kv_delta_parity` (`tests/ds4_test.c:496`), which already
has the machinery: add a third arm that stores at `full - 128` while the session
is at `full`, then loads it and asserts the file is either absent or loads to
`full - 128`.

---

## 2. Divergence anchor fires on every chunk instead of once at the target

**One line.** In the `target == 0` branch the "have we reached the target yet"
test guards only the grid check, not the store, so once a target is set every
subsequent chunk writes a complete cold anchor.

**Severity: wasted I/O and budget** — the single largest I/O consumer on the
path. Not wrong output.

### Evidence

```
ds4_server.c:13123        int div_target = slot->divergence_target_tokens;
ds4_server.c:13124        if (div_target > 0 && tokens->len >= div_target) {   <-- guard
ds4_server.c:13128            const int step = ds4_kvstore_continued_step_at(kc, div_target);
ds4_server.c:13129            if (step > 0 && div_target % step == 0)
ds4_server.c:13130                div_target = 0;
ds4_server.c:13131            else
ds4_server.c:13132                slot->divergence_target_tokens = 0;
ds4_server.c:13133        }
ds4_server.c:13134        if (div_target > 0) {                                <-- NOT guarded
ds4_server.c:13135            const int store_len = tokens->len;
ds4_server.c:13136            if (kv_cache_store_live_prefix(s, slot, tokens, store_len, "cold"))
```

When `tokens->len < div_target` the block at 13124-13133 is skipped, so
`div_target` keeps its value and `slot->divergence_target_tokens` is **not**
cleared, and 13134 fires. The pending target therefore re-fires on every
progress callback (every 512-token chunk) until the live length happens to pass
it.

The `continued` branch got this right:
`ds4_server.c:13161  if (div_target == target || tokens->len < div_target) div_target = 0;`

### How I know it is real (measured)

```
"divergence anchor requested"   1 line     (1116 11:45:52, common=442343, disk=262144)
"divergence anchor stored"     342 lines   all 1116 11:44..11:56
  tokens=262656 (target 442343)            <- live is 179 687 tokens SHORT of target
  tokens=263168 (target 442343)            <- +512
  tokens=263680 (target 442343)            <- +512
  341 of 342 have tokens < target
  delta span per store: 512 tokens (2 stores 510)
  44 423 MiB = 44.4 GiB total
```

Cross-check against the reason totals: `cold` stores are n=345, 46 630 MiB;
divergence anchors are 342 of those 345 and 97.4 % of the bytes. So "cold stores"
in every dashboard is almost entirely this churn.

At ~130-230 ms each (`save=` on those lines) that is ~60 s of executor hold in
12 minutes, which is also why 14 of the 101 sub-10 t/s decode samples in the
whole log fall inside that 12-minute window.

### Minimal fix

```c
ds4_server.c:13123
        int div_target = slot->divergence_target_tokens;
+       /* Not reached yet: keep the target pending, do not store. */
+       if (div_target > 0 && tokens->len < div_target) return;
        if (div_target > 0) { ...grid check... }
```

**Format/version impact:** none.
**Risk:** almost none — this restores the behaviour the `continued` branch
already has. The only behaviour change is that a divergence anchor now lands
when the rebuild actually reaches `common`, which is what the comment at
13117-13122 says it is for.

### Regression test

```
test_kv_cache_divergence_waits_for_target
  - slot.divergence_target_tokens = 1000; tokens->len = 500
  - drive kv_cache_maybe_store_continued with a counting store hook
  - assert the store count is 0 and slot.divergence_target_tokens is still 1000
  - set tokens->len = 1000, drive once, assert exactly one store and the target cleared
```
The store needs a session, so the testable seam is a `DS4_SERVER_TEST`-only
counter incremented in `kv_cache_store_live_prefix`, plus asserting the target
field. That is a pure-logic test and fits the existing suite.

---

## 3. No `fsync` before `rename` — a torn payload is structurally valid

**One line.** The store writes header + text + payload + trailer, calls
`fflush`/`fclose`, then `rename`, with no `fsync` of the file and no `fsync`
of the directory, so a power loss or kernel panic can leave a *renamed* file
whose payload is partially or wholly stale — and the format has no payload
checksum, so nothing detects it.

**Severity: wrong output**, the only class in this audit that can corrupt a
served response. Ranked third only because it needs a power loss inside a
~250 ms window; the consequence when it happens is silent and unrecoverable.

### Evidence

```
ds4_kvstore.c:3362   bool ok = fwrite(h, 1, hdr_bytes, fp) == hdr_bytes &&
ds4_kvstore.c:3363             fwrite(tb, 1, sizeof(tb), fp) == sizeof(tb) &&
ds4_kvstore.c:3364             fwrite(text, 1, text_len, fp) == text_len &&
ds4_kvstore.c:3365-3366        ds4_session_write_staged_payload(&staged, fp, ...) == 0 &&
ds4_kvstore.c:3367             kv_trailer_write(hooks, fp, text, &trailer_bytes) &&
ds4_kvstore.c:3368             fflush(fp) == 0;
ds4_kvstore.c:3370   if (fclose(fp) != 0) { ... }
ds4_kvstore.c:3384   if (ok && rename(tmp, path) != 0) { ... }
```

Grep of the tree for `fsync|fdatasync|F_FULLFSYNC|O_SYNC` outside
`trainer/` returns exactly two hits: `ds4_agent.c:7193` and `ds4.c:63532`
(`flock`, the instance lock). **The KV store is not one of them.**

The pattern is not unknown to this codebase — `ds4_agent.c:7193` does
`if (fsync(fd) != 0) goto failed;` immediately before `rename(temp, target)`,
with the same temp-in-the-same-directory shape.

### What the format actually validates, and in what order

Order in `kv_cache_try_load_one` (`ds4_kvstore.c:3632`):

| # | Check | Line | Before any session mutation? |
|---|---|---|---|
| 1 | magic / version / payload ABI / quant / tokens != 0 | 3656 → `read_header` 615-673 | yes |
| 2 | `model_id` | 3659 | yes |
| 3 | text length ≤ prompt | 3662 | yes |
| 4 | text read + SHA1 == filename + byte-prefix vs prompt | 3667-3681 | yes |
| 5 | tokenizer fingerprint (TOKFP section seek) | 3710 | yes |
| 6 | full delta chain: per link header, frontier continuity, quant/model, text prefix, TOKFP | 3736-3821 | yes |
| 7 | **payload → GPU tensors** | 3831 / 3856 | **mutation starts here** |
| 8 | `loaded_tokens->len == hdr.tokens` | 3872 | after |
| 9 | trailer (tool map / vision) | 3883 | after |

So the *structural* checks are strong and correctly ordered — text is
content-addressed and hash-checked before anything is touched, which is why the
common failure mode is a clean miss. What is missing is any check that the
payload *bytes* are the bytes that were written:

* a truncated payload is caught, because every read is bounded by `remaining`
  (`payload_read_bytes`, and the `split.total_bytes != payload_total` three-way
  check at `ds4_kvstore.c:66731`);
* a **zero-filled or partially stale** payload is not caught. The 13-u32 payload
  header (`DS4_SESSION_PAYLOAD_MAGIC` at offset 0) and the token array would be
  correct (they are in the first block, most likely flushed), and the KV tensors
  would be zeros or an older generation. The token count still matches, so check
  8 passes and the model decodes from garbage KV.

Note the exact guarantee the code has today: *the file is never observed
half-written, because it is written to `<sha>.kv.tmp.<pid>` and renamed*
(`ds4_kvstore.c:3299`, `3384`). That is a statement about **atomicity of the
name**, not about **durability of the contents**. Both the code comment at
`ds4_kvstore.c:2339-2348` and `TODO-20260927.md:142` ("Crash-safe via existing
tmp+rename sweep") read the first as if it implied the second. It does not.

### Is the temp file in the same directory?

Yes, always: `path` is `ds4_kvstore_path_for_sha()` = `kc->dir + "/" + sha +
".kv"` (`ds4_kvstore.c:402-407`) and the temp is built by appending
(`ds4_kvstore.c:3299`), so both are in `/Volumes/FireCuda520/ds4-kv-qwen`. The
rename is same-filesystem and therefore atomic. No cross-device `EXDEV` path
exists.

### `.tmp` litter, and whether the budget counts it

* Reaped **only at open**, by `kv_cache_sweep_orphan_temps`
  (`ds4_kvstore.c:2349`, called from `ds4_kvstore_open` at `2441`), and only if
  older than **3600 s** (`2363`).
* **Not** counted by the budget: `kv_cache_refresh` (`704`) admits only names
  passing `ds4_kvstore_sha_hex_name` (`384-391`), which requires
  `strlen(name) == 43`; `<sha>.kv.tmp.<pid>` is longer, so temps are invisible to
  the index and to eviction. They still consume the volume.
* Consequence: a crash inside the last hour before a restart leaves a temp that
  the *next* open will not reap; it sits until the open after that. Bounded by
  restart frequency, not by anything else. Measured: `grep -c "reaped"
  log/…1117-094911` = **0**, so in this log's life nothing was reaped and
  nothing accumulated — consistent with clean shutdowns.

### Could two processes collide?

Not on the temp name: it embeds the pid, and the rename target is the same
content-addressed path either way, so the loser's file is simply overwritten by
an equally valid one. Cross-process collisions on the **same key** are
therefore benign.

The real cross-process hazard is the *in-place* writers, which have no lock
across processes: `ds4_kvstore_touch_file` (`744`, `fopen(path,"r+b")` +
header `fwrite`) and `ds4_kvstore_rewrite_trailer` (`3035`, `ftruncate` +
trailer write + header rewrite). Two processes sharing a KV dir (a server plus a
`ds4-agent` or a `ds4_test --kv-delta` run pointed at the same directory) can
interleave those and produce a half-written header. The fork's instance lock
(`ds4.c:63532`) covers the *engine*, not the KV directory, and
`ds4_agent.c` writes the same format from a separate process.
**UNVERIFIED** whether any deployment actually shares a directory; the
`ds4-server.sh` profiles use per-model dirs, so today this is theoretical.

### Minimal fix

```c
/* ds4_kvstore.c, immediately after fflush / before fclose */
+   if (ok && fsync(fileno(fp)) != 0) { saved_errno = errno; ok = false; }
```
and, optionally, one `fsync` on `kc->dir` after a *successful* rename (missing
that only costs the rename's own durability, i.e. a cache miss, not corruption).

**Cost:** one fsync on a 200-1400 MiB file. On the blade this is the
expensive part of the store; it must not land while `inference_mu` is held
(which is why this fix wants to ride with, or after, the store split). **Do not
ship it as a standalone patch on today's locking** — it would lengthen the hold
that causes the dips.

**Format/version impact:** none (fsync only). Adding a payload checksum *would*
be a format change; see the note below.

**Risk:** low. `ds4_agent.c:7193` is the precedent. The one behaviour change is
that a store now fails if fsync fails, which the existing `ok` path already
handles (unlink the temp, log, return false).

### What would close the class properly

`fsync` gives you "the bytes I wrote are the bytes on disk". It does not give you
"the bytes are the right bytes" if the *writer* is buggy. A 20-byte SHA-1 of the
payload appended as a trailer section (a `KVPAY` section, ext-bit reusing an
unused one — `DS4_KVSTORE_EXT_*` uses bits 0-5, so 1<<6 is free) would make every
future generation of this bug fail closed instead of silently. That is a format
addition, backwards-compatible (old files have no section, are trusted as today),
no version bump needed because `ds4_kvstore_read_header` ignores unknown trailing
sections and the trailer walker stops on an unknown magic — **which is exactly
the mechanism that makes it non-trivial**: the current walker returns -1 on an
unknown section, so a *new* file read by an *old* binary would be reported as
"trailer corrupt". So a new section must go *last* and old binaries must be
taught to stop cleanly, or the ext bit must gate it. I would not bundle this
with the fsync fix.

### Regression test

Not unit-testable without simulating a power loss. The honest test is
operational: after the fix, `log` a `store fsync` timing and confirm the store
still completes; and a manual fault-injection run (`kill -9` mid-store under a
`DS4_KV_TEST_TORN` hook that zeroes the second half of the payload before
rename) asserting the loader rejects it. Given the audit's constraint of no code
changes, I did **not** run that.

---

## 4. Loader `unlink`s without the children check — orphans descendants

**One line.** Four call sites delete a checkpoint file directly, bypassing
`kv_cache_unlink_entry`'s "defer while `children > 0`" rule, so deleting a
parent silently strands every delta child that chained onto it.

**Severity: lost durability**, latent. One orphan on disk now.

### Evidence

Raw unlinks:

```
ds4_kvstore.c:3896   unlink(path);        /* length mismatch (finding 1)   */
ds4_kvstore.c:3814   unlink(path);        /* delta chain broken             */
ds4_server.c:13078   unlink(path);        /* kv_cache_reload_repeat         */
ds4_server.c:13089   unlink(path);        /* kv_cache_discard_failed_disk_entry */
```

against the correct path:

```
ds4_kvstore.c:1940   if (e.children > 0) {   /* defer; the parent becomes
ds4_kvstore.c:1941-1959     deletable once its children die */ ... }
```

`children` is built at scan time from `parent_sha`
(`ds4_kvstore.c:721-730`), and `kv_dir_has_dependents` (`2927`) exists
precisely so the *reuse* path does not orphan a dependent (review M-4). The
loader is the one place that never asks.

### How I know it is real

**All 144 of the finding-1 files are parents**, not leaves:

```
files=751  doomed=144  doomed that are parents: 144   doomed that are roots: 0
deepest chain: 31 links, 22 of them doomed
```

So every discard at `3896` deletes a node with at least one child. Current
on-disk result, computed by walking every header's `parent_sha`:

```
ORPHANS (parent sha not present on disk) = 1 file, 0.57 GiB
orphan subtrees cover 1 file, 0.57 GiB
```

Only one, because the 41 discards were almost all followed by a re-store that
recreated the same sha with a matching payload (36 of 37 distinct discarded shas
are on disk today and only 2 are still mismatched). `grep -c "delta chain
broken" = 0`: no orphan has been *loaded* yet, so the loader's own cleanup
(`3814`) has not fired.

**Silent degradation, not a crash:** an orphaned child keeps its
`delta_from > 0` and its `parent_sha`, so it is indexed, budget-charged and
offered as a candidate (`kv_cache_find_text_prefix_skip` does not check chain
integrity, `3512-3592`). It is only discovered when it becomes the terminal and
the chain walk fails — and then it is unlinked, cascading one level per load.
That is a slow leak of exactly the ladder the retention policy is trying to
protect.

### Minimal fix

Route every delete through one helper that (a) refuses when `children > 0` (or
defers, as `kv_cache_unlink_entry` does) and (b) is the only place that calls
`unlink` on a `.kv` file:

```c
/* returns false if the node still has live children (left in place) */
static bool kv_cache_unlink_node(ds4_kvstore *kc, const char *path,
                                 const char *reason);
```
Callers at `3896`, `3814`, `13078`, `13089` switch to it. The deferred case
wants a WARNING line, not silence.

The 2026-09-27 TODO (`TODO-20260927.md:137-150`) already specifies the proper
long-term answer for a single-child parent: re-anchor the child (concatenate the
spans, re-point `parent_sha`) then unlink. That is the right fix and it is
bigger; the helper above is the safe half.

**Format/version impact:** none.
**Risk:** low for the helper; the only behaviour change is that a delete can now
decline, which leaves a file on disk (a budget cost) instead of destroying a
chain (a durability cost). The right trade.

### Regression test

`tests/kv_policy_harness.c` already has `scenario_delta_parent_deferred`
(`tests/kv_policy_harness.c:276`) which builds a parent+child and asserts the
parent is deferred by *eviction*. Add the loader half:

```
scenario_loader_does_not_orphan:
  build parent P + child C (v3 headers, stub payloads)
  call the new unlink helper on P
  assert P still on disk, C's chain walk still resolves
```

---

## 5. Deferral machinery never converges on the delta ladder

**One line.** The deferral backoff table is size-bounded but not progress-
bounded: 14 352 deferral lines over 856 distinct files, ~17 per file, and the
parents they protect are the ones the budget needs back.

**Severity: budget pressure.** Not a correctness defect; it is why the 256 GiB
budget is full with 89 GiB of files that cannot serve as load targets (finding
1) and 671 parent nodes.

### Evidence

```
grep -c "delete deferred"                                       = 14 352
distinct files in those lines                                   = 856
children=1 : 14 306      children=2 : 46
```

`kv_defer_backoff_advance` (`ds4_kvstore.c:1927`) expires a row after
`KV_DEFER_BACKOFF_PASSES = 8` passes (`1874`), and
`kv_defer_backoff_prune` (`1908`) keeps it while `children > 0`. So each file
is re-proposed roughly every 8 eviction passes forever, because its child never
dies. 14 352 / 856 ≈ 16.8 ≈ two epochs each — consistent with the design, not a
runaway. The ADR's "4 deferral lines across all logs" predates
`DS4_KV_DELTA_FULL_REASONS` (the 2026-09-29 ADR point 1, `037a850`), which let
every store reason chain and thereby multiplied parent count.

**Why it matters now and not before:** the 342 divergence anchors from finding 2
form a 342-link chain of 512-token spans, each a parent of the next. One
12-minute episode manufactured ~342 un-retirable nodes. Fixing finding 2 removes
the worst producer of this.

### Minimal fix / mitigation

No new code needed to *stop the bleeding*: fix finding 2 and re-measure. If
deferral count stays high, the next lever is the one the TODO already scopes
(single-child re-anchor). **Do not** raise `KV_DEFER_BACKOFF_PASSES`; it
converts a slow leak into a wall (the code comment at `1860-1873` records the
"413 times in one day" field incident that motivated the bound).

### Regression test

`scenario_deferral_backoff` (`tests/kv_policy_harness.c:377`) already pins the
bound. What is missing is a *ladder-shaped* scenario: a 20-link chain whose
middle nodes are all redundant, asserting the pass count to reclaim the whole
chain is bounded. That would have made the 342-link episode visible in CI.

---

## 6. `kv_cache_refresh` is O(files) and runs 2× per store, 1× per load

**One line.** Every store re-scans the whole KV directory twice — once in
`pick_parent`, once inside `ds4_kvstore_evict` — and every load once, opening
and reading a header per file, inside the executor lock.

**Severity: wasted I/O.** 751 files today, `cold_max = 30000`
(`ds4_kvstore.c:37`).

### Evidence

Call sites of `kv_cache_refresh` (`ds4_kvstore.c:704`):

```
 741  ds4_kvstore_refresh()  (public, for the server's post-store sweep)
2205  ds4_kvstore_evict()
3222  store: delta parent pick
3296  -> 2205  (evict, reached from the store)
3498  maybe_store_continued (post-store)
3598  find_text_prefix (public)
3966  try_load_text
```

Each is `opendir` + `open` + `fread(48..120B)` per file (`710-717`). At 751
files that is ~1500 opens per store; at the 30k cap it is ~60 000. This is
unmeasured in the `save=` timer (which starts after it, `3301`) but it is inside
`inference_mu`.

Also note the memory: `kv_chain_rel_build_*` allocates an `n × n` byte matrix
(`ds4_kvstore.c:1292`, `1420`) — 564 KB at 751 files, **~900 MB at 30k**. The
comment at `1200-1208` calls this "a real failure mode"; it is latent because
the directory never got near the cap.

**Not fixed by any lock change** — moving it off `inference_mu` (as the S1
design does) removes the stall, not the work.

### Minimal fix sketch

The store already knows the file it just wrote, so the index need not be a pure
function of the directory: an incremental insert plus an mtime+mtime-nsec
directory stamp would let `refresh` become a no-op when nothing changed. Higher
risk than the findings above (the `children` index and the sha-keyed `rel` matrix
both reconcile against `kc->entry`, and a stale index is exactly the class the
ADR's "M-1" notes warn about). I would not do this until findings 1 and 2 are
fixed and the directory has been re-measured.

### Regression test

Benchmark-shaped, not correctness: assert `refresh` is not called more than once
per store in a `DS4_SERVER_TEST`-instrumented run (count calls in a counter, like
`kv_restore_file_opens` at `ds4_server.c:12317` already does for the tool-map
bootstrap).

---

## 7. Trailer rewrite truncates in place and is not crash-atomic

**One line.** `ds4_kvstore_rewrite_trailer` `ftruncate`s the old trailer before
writing the new one, in place; a crash between the two leaves a file whose
header still advertises a trailer that is gone.

**Severity: silent degradation.** Not corruption: the loader catches it as
"trailer corrupt, continuing without it" (finding §9.3).

### Evidence

```
ds4_kvstore.c:3055   const uint64_t end = hdr_total + text_bytes + hdr.payload_bytes;
ds4_kvstore.c:3056-3058  fseeko(fp, end) == 0 && ftruncate(fileno(fp), end) == 0
ds4_kvstore.c:3061       kv_trailer_write(...) && fflush(fp) == 0
ds4_kvstore.c:3092-3094  fseeko(fp,0); fwrite(header); fflush(fp)
ds4_kvstore.c:3098-3109  on !ok: unlink(path)
```

The unlink-on-error covers an *error*, not a *crash*. Crash windows: after the
truncate (old trailer lost, new not written), and after the trailer write but
before the header rewrite (new trailer present, `ext_flags` not updated, so the
loader skips the section it should read — a silent loss of tool-replay state,
which the ADR says is what caused the "255k-token rebuild").

The reuse path runs this on every hit-store collision
(`ds4_kvstore.c:3199-3200`), so it is not rare: `stat_reused` is incremented
there.

**Minimal fix:** skip the in-place rewrite when the new trailer is byte-identical
to the old one (the common case is "same map, later `last_used`", which the
header rewrite alone covers), and otherwise write to a temp and rename. The
first half is a cheap, low-risk win.

**Format impact:** none.

---

## 8. `hit_half_life` and `hits` are dead retention inputs (cosmetic)

`DS4_KVSTORE_HIT_HALF_LIFE_SECONDS` is printed in the open banner
(`ds4_kvstore.c:2449`) and never read again (`grep` finds only the definition at
`ds4_kvstore.h:18` and that log line). `hits` is written by
`ds4_kvstore_touch_file` (`744`) and read only for the next increment and for the
eviction log line (`1972-1977`). Retention ranks on `last_used` only
(`kv_cache_entry_last_used`, `1695`). So "hit decay" is folklore; the banner
implies a policy that does not exist. Cosmetic, but it is the kind of thing that
makes a future reader reason from a false premise.

---

## 9. Audited and **not** a defect

### 9.1 `cross-quant=accept`

`--kv-cache-reject-different-quant` is opt-in (`ds4_server.c:18509-18510`), so
the default accepts. That sounds dangerous — a Q4 checkpoint served to a Q2
engine — but it is unreachable: `quant_bits` is a label on the *routed-expert*
quantisation, while the gate that actually separates weight sets is `model_fp`
(a hash of the model file's path+size+mtime, `ds4_kvstore.c:371-382`), checked
separately at `kv_cache_find_text_prefix_skip:3539` and
`kv_cache_try_load_one` via the chain (`3759`). A different quantisation is a
different file, hence a different `model_fp`, hence rejected. And the payload is
the KV cache, not the weights, so its layout does not depend on `quant_bits`.
`ds4_kvstore_quant_bits_loadable` is `{2,4}` (`482`) and the writer only emits
those (`3139`), so the "q5+ write-only waste" of review M-3 cannot recur.
Residual: a v1 (legacy) file has `model_fp == 0` and is trusted, which is the
documented "unstamped legacy files trusted" decision
(`.codebase-memory/adr.md`, 2026-09-06 decision 1).

### 9.2 `key=token-text` vs `key=visible-transcript`

Both are matched by the same text-prefix scan; there is no key-kind filter in
`kv_cache_find_text_prefix_skip`. That is deliberate: the visible-transcript key
*is* the visible prefix of the conversation, so a request that replays that
visible text legitimately binds to it, and the loader rebuilds the effective
prompt from the payload's token history
(`ds4_kvstore.c:3879-3881`). The dangerous direction — a *text* request
matching a *vision* file — is blocked by
`3528: if (!want_vision && (e->ext_flags & DS4_KVSTORE_EXT_VISION)) continue;`
and, on the vision side, by the `any_below` requirement (`ds4_server.c:13318-13330`).
Measured split in the log: 59 `key=thinking-visible`, 814 `key=token-text`,
0 cross-key surprises.

### 9.3 "trailer corrupt, continuing without it" (`ds4_kvstore.c:3883-3893`)

Safe for the *text* path: the tool map only affects how the *next* prompt is
rendered, so a missing map degrades to a canonical re-render (a miss), never to
a wrong KV state. Not safe in general, and the code knows it: on the vision path
the same corruption is fatal, because `tctx.vision_section_present &&
!tctx.vision_section_corrupt` is required before accepting
(`ds4_server.c:13328-13329`). So the "continue" is scoped to the one case where
it is sound. 0 occurrences in the log.

### 9.4 Sha/prefix keying

The filename is `SHA1(rendered text)` and the lookup recomputes
`SHA1(prompt[0..text_bytes])` per candidate and requires equality
(`ds4_kvstore.c:3548-3549`), then re-verifies in the loader
(`3673-3676`). There is no way to match a prompt whose bytes are not an exact
prefix. The known limitation (same bytes, different token ids) always degrades
to a rewind, never to wrong tokens, because `server_session_sync` restarts at 0
when `common < live` (`ds4_server.c:14594-14596`).

---

## 10. Test coverage: what would have caught this, and what is dormant

**`make test-server-logic`** (`Makefile:1484`) runs the embedded suite: 52
`test_kv_*` functions (grep count), all policy/logic over stub files or bare
`kv_disk_cache` structs. None of them calls
`ds4_kvstore_store_live_prefix_text`, because that needs a session and therefore
a model. So the store→load round trip has **no** model-free coverage.

The one test that looks like it covers finding 1 is the opposite of coverage:

```
ds4_server.c:25229-25240  /* ---- off-grid frontiers must still ladder (2026-09-30 regression) ----
   kc.continued_last_store_tokens = 131072;
   TEST_ASSERT(kv_cache_continued_store_target(&kc, 251255) == 245760);
```

It asserts that the target snaps to 24 576 tokens *below* live — and then the
production caller uses that value as the store length. The test pins the
scheduling decision and is silent on the store invariant, so it actively
documents the bug as intended behaviour.

**`tests/kv_policy_harness`** (`Makefile:1520`, in `make test`) builds real
headers and payload-shaped stubs (`scenario_trailer_rewrite` at
`tests/kv_policy_harness.c:511` is a good example) but never stages a real
payload, so it cannot see a header/payload token mismatch either.

**The only real store→load parity test is `--kv-delta`**
(`tests/ds4_test.c:496`, registered at `8453`, `opt_in = false` so it is in
`--all`, and `make test` runs `./ds4_test` with `argc == 1` → `run_all = true`
at `tests/ds4_test.c:8579`). So it is **not** dormant — but it stores at exactly
the live frontier every time (`ds4_session_sync(&t_half)` then store at `half`,
`ds4_session_sync(&t_full)` then store at `full`), so `store_len == live` always
and the defect cannot appear. It is the right harness, one line short of the
case.

**Dormant/stale, for the record:** `tests/test_qwen4_mtp_prefill.c` is not in
`make test`'s target list (`Makefile:1518-1523`) — it is a build target only, and
`RESUME-MERGE-CLOSEOUT-20261106.md:92` records it as "dormant stale". It is not
a KV test, but it is the same failure mode as `--kv-delta`'s blind spot: the
tests that would notice a shape change are the ones not run.

**Coverage verdict:** findings 1, 2 and 4 were all reachable in principle by the
existing suites and all three slipped through for the same reason — the tests
assert *decisions* (a target length, a keep-set, a deferral) but never *round
trip a payload*. A single model-free "store at `live - k`, then load" test
would have caught 1 and 4.

---

## Closing

### (1) Top 3 to fix first

1. **Finding 1 (store header/payload mismatch).** It is the only finding that
   makes the cache *lie about what it holds*: 89.03 GiB (34.8 % of the budget) in
   144 files that are indexed, charged, offered as candidates and guaranteed to
   fail. The fix is one line and the correct pattern is 6 lines away in the same
   function. It also removes ~19 GiB/2 days of discarded reads and 41
   unlink-and-rebuild cycles.
2. **Finding 2 (divergence anchor misfire).** 342 stores and 44.4 GiB in 12
   minutes, 97.4 % of all cold-store bytes, and the direct cause of the 342-link
   un-retirable chain behind the 14 352 deferrals. One line, and the correct
   guard already exists in the sibling branch.
3. **Finding 3 (`fsync` before `rename`).** The only path that can serve wrong
   output, 2 lines, and the codebase already does it correctly in
   `ds4_agent.c:7193`. It is third only because it needs a power loss; if the
   box has no UPS, promote it to first.

Findings 4 and 5 should follow in the same series — 4 because it is the
destructive half of 1, 5 because it is 2's downstream consequence.

### (2) What I could not verify

* **That a torn payload has ever been served.** No log signature exists for it
  (by construction — that is the defect), so the claim that the current format
  *would* accept a partially-zeroed payload is a code reading
  (`ds4_kvstore.c:3362-3384` + the absence of any payload checksum in the
  validation table in §3), not an observation. Settling it needs a fault
  injection (`DS4_KV_TEST_TORN` that zeroes half the payload before rename) and
  one load.
* **Whether any deployment shares one KV directory between two processes.** The
  in-place writers (`touch_file`, `rewrite_trailer`) have no cross-process lock;
  `ds4-server.sh` uses per-model directories, so I believe this is theoretical,
  but I did not audit `ds4_agent`'s directory conventions.
* **The exact GPU-state consequence of a stale (not zeroed) payload.** If the
  pages are the *previous* generation of the same tensor rather than zeros, the
  model would decode from a plausible-but-wrong KV. I have not constructed that
  case.
* **Whether the 41 discards account for all of the load-time waste.** They are
  the ones the log names. Discards inside `ds4_session_load_payload_span` (which
  logs a different line) and the `--kv-cache-reload-repeat` path (1 occurrence)
  are separate and I did not attribute them to a cause.
* **Memory at the 30k-file cap.** The `rel_bits` matrix is `n²` bytes
  (`ds4_kvstore.c:1292`); I computed ~900 MB arithmetically but did not run it.

### (3) Does this change the S1 store-split recommendation?

**Yes, in ordering, not in direction.**

The S1 design optimises how long the store holds `inference_mu`. But the store
path as it stands writes 44.4 GiB of churn and 89 GiB of unloadable files out of
280 GiB total. Moving *that* off the lock would make the decoder stop waiting for
work it should not have been doing. Fix findings 1 and 2 first:

* they remove ~43 % of store *events* (finding 2: 342 of 792) and the whole
  discard/re-store cycle (finding 1);
* they are one-line changes each, with no lock-ordering risk, no new API, and no
  format change — i.e. a far better risk/benefit ratio than the lock surgery;
* and they make the S1 measurement interpretable. A hold-time win measured
  against a baseline that is 44 GiB of self-inflicted churn is not a measurement
  of the lock.

Concretely I would now sequence: **finding 1 → finding 2 → (baseline the hold
telemetry) → finding 4 → S1 store split → finding 3 fsync.** The fsync
deliberately lands *after* S1 so its cost is paid outside the executor lock;
shipping it before would lengthen the exact hold this whole lane exists to
shorten. If the box is not on a UPS, swap the last two and accept the temporary
hold cost.

One thing the deeper look changed in the earlier analysis: I had written that the
store's measured `save=` was "a lower bound" and that staging was the
irreducible part. That still holds, but the *reason* the under-lock cost is so
much larger than `save=` implies is now concrete — 2× `kv_cache_refresh`
(finding 6) and a second full payload write, on a path that fires 342 times for
one mis-set target (finding 2). The S1 split is still the right shape; it just
should not be the first thing that touches this code.
