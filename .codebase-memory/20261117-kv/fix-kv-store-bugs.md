# Fix: two KV store-length bugs in `kv_cache_maybe_store_continued()` (+ tests)

Date 2026-11-17. Repo `/Users/naz/Projects/ds4`, base commit `e651b3f`.
**Nothing committed** — one modified file in the working tree:

```
$ git status --short
 M ds4_server.c
$ git diff --stat ds4_server.c
 ds4_server.c | 502 ++++++++++++++++++++++++++++++++++++++++++++++++++++++-----
 1 file changed, 467 insertions(+), 35 deletions(-)
```

Live engine untouched: `ps -o pid,etime,command -p 64188` → `64188 … ./ds4-server
--model gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx 524288 …` (same process before and
after; no traffic, no restart — `make` only rewrote the on-disk binary).
Tests that used the shared KV dir were not run (`make test` was not available by
design; see §6).

---

## 1. Diff at a glance

| # | hunk (final tree) | what |
|---|---|---|
| 1 | `ds4_server.c:12723-12741` | test-only seam declarations (`DS4_SERVER_TEST`) |
| 2 | `ds4_server.c:12752-12763` | test-only store recorder inside `kv_cache_store_live_prefix_text`, before it takes `inference_mu` |
| 3 | `ds4_server.c:13137-13181` | `kv_continued_store_plan` + `kv_cache_continued_store_plan()` — **FIX 1**: store/note = live length |
| 4 | `ds4_server.c:13183-13204` | `kv_anchor_store_plan` + `kv_cache_divergence_anchor_plan()` — **FIX 2**: below-target anchor stores nothing, stays pending |
| 5 | `ds4_server.c:13207-13282` | `kv_cache_maybe_store_continued()` drives all three decisions through the helpers (early-return anchor 13229-13246, continued store 13247-13251, sibling anchor 13265-13279) |
| 6 | `ds4_server.c:25337-25345, 25388-25403` | `test_kv_cache_continued_uses_aligned_frontiers` corrected: all **33** original asserts kept (measured: `git show HEAD … \| grep -c TEST_ASSERT` = 33), plus the trigger-vs-length contract for the same numbers |
| 7 | `ds4_server.c:25421-25453` | new `test_kv_cache_continued_store_uses_live_length` (FIX 1 truth table) |
| 8 | `ds4_server.c:25455-25502` | new `test_kv_cache_divergence_anchor_waits_for_target` (FIX 2 truth table) |
| 9 | `ds4_server.c:25504-25552` | new fixture `kv_store_env` + open/close (empty temp KV dir, hooked store, fake live view) |
| 10 | `ds4_server.c:25554-25635` | new `test_kv_cache_maybe_store_continued_stores_live_length` — drives the **real** function, 6 cases |
| 11 | `ds4_server.c:25637-25670` | new `test_kv_cache_continued_store_cadence_across_rungs` — the `note_store` decision (a) |
| 12 | `ds4_server.c:25672-25718` | new `test_kv_cache_cold_store_suppression_survives_live_length` — the suppression interaction (b) |
| 13 | `ds4_server.c:29435-29439` | 5 new tests registered in `ds4_server_unit_tests_run` |

The two behavioural deltas, exactly:

```c
/* FIX 1 — was:  kv_cache_store_live_prefix(s, slot, tokens, target, "continued")
 *               kv_cache_slot_note_store(slot, target);                       */
p.store_len = live_tokens;
p.note_len  = live_tokens;

/* FIX 2 — was (target==0 branch): if (div_target > 0) { store at tokens->len }
 *       i.e. the store was guarded only by `div_target > 0`, while the grid
 *       check above it required `tokens->len >= div_target`.                   */
if (div_target <= 0 || covered || live_tokens < div_target) return p;
```

`covered` keeps its per-site meaning, unchanged: grid coverage
(`step_at(div_target)` divides `div_target`) at the early-return site, and
`div_target == target` at the sibling site (HEAD `ds4_server.c:13163`:
`if (div_target == target || tokens->len < div_target) div_target = 0;`).
Target-field clearing happens **only** when the anchor fires — same as before.

### Extraction fidelity (why the RED step is trustworthy)

Before adding any test I wired the three decision points through the helpers
with **production's own decisions** and ran the untouched 52-test suite:

```
1117 12:39:08 … ds4-server tests: ok
```

The extraction alone changed nothing. Everything that fails afterwards fails
because of the two fixes' absence, not because of restructuring.

---

## 2. Reasoning: (a) `note_store`, (b) suppression, (c) log truthfulness

### (a) What must be recorded — and why it is the live length, not the rung

`kv_cache_slot_note_store` (`ds4_server.c:13068-13072`) is a max-assign. Its
consumer is `ds4_kvstore_continued_store_target` (`ds4_kvstore.c:2656-2658`):

```c
int target = live_tokens - (live_tokens % step);
if (target <= kc->continued_last_store_tokens) return 0;
```

* **No re-fire.** With `L` recorded, the next call's snapped target
  `B = L - L%step` satisfies `B <= L` = watermark → 0. With `B` recorded the
  same test passes. Identical cadence either way.
* **No skipped rung.** The next boundary is `B + step`, and `L < B + step` by
  construction, so `B + step > watermark` in both variants → it still fires
  when crossed. Verified by driving the real function across the measured
  resume frontier: 4 fires at live `251255, 262519, 278903, 295287` in **both**
  variants; only the recorded value differs (`…cadence_across_rungs`, case
  output in §4).
* **So the choice is semantic, and the code already decided it.** The field
  means "deepest length this session is durable at", not "deepest rung
  issued":
  1. every other writer of it records a *store length* —
     `ds4_server.c:16045` / `16095` (`= disk_cached`, the loaded frontier),
     `16250` / `16311` (`note_store(cold_store_len)`);
  2. the resume watermark is documented as off-grid by construction
     (`ds4_server.c:25375-25378`, the measured 306163 frontier) with the rule
     "snapping must never write a rung *below* that watermark — it would
     re-serialise rows that are already durable", i.e. the watermark tracks
     durability;
  3. after FIX 1 the file that exists *is* the frontier at `L`; recording `B`
     would make the watermark disagree with the key and `hdr.tokens` of the
     file just written.
* **One interaction I checked and dismissed as harmless:** with `L` recorded, a
  cold anchor at a boundary `C` with `B < C <= L` reports "nothing to suppress"
  (`kv_cache_slot_suppress_continued` → -1, `ds4_server.c:13074-13080`). That
  cannot occur: the cold path requires `cached == 0`, and `cached == 0` resets
  the watermark to 0 first (`ds4_server.c:16054`).

### (b) Cold-store duplicate suppression is not defeated

The suppression is a *trigger* mechanism and the fix does not touch triggers.
`kv_cache_slot_suppress_continued(s, slot, cold_store_len)`
(`ds4_server.c:16219`, block 16210-16220) sets the watermark to
`cold_store_len`; `kv_cache_maybe_store_continued` then computes
`continued_target(live) = 0` for every live length whose snapped boundary is
`<= cold_store_len` — including the exact case it exists for (the prefix sync
stops precisely at `cold_store_len`, live == watermark → 0 → no duplicate
"continued" write). On failure, `kv_cache_slot_restore_suppressed` puts the
watermark back (call sites `ds4_server.c:16233`, `16253`, `16269`, `16314`) and
the rung becomes due again. Pinned by test (12) which drives the real function: zero
stores at live 16384 and 16896 after `suppress(16384)`; the next rung fires and
now fires at live `25088` (rung = 24576); after restore it fires again. The
last two asserts are **discriminating** — they fail in RED because RED stored at
the rung.

The brief's "16069-16090" is the responses-visible-replay warning; the
suppression block is 16210-16220 in the final tree (HEAD 16106-16116). Nothing
there was changed.

### (c) Logging stays truthful, and one diagnostic goes intentionally quiet

* `kv cache stored tokens=%d trimmed=%d … delta=%u..%d … size=%…`
  (`ds4_kvstore.c:3430-3434`, `trimmed = original_len - store_tokens.len`):
  continued stores now always log `trimmed=0`, which is **true** — the payload
  is the whole live graph. `delta=` spans now end at the live length instead of
  the rung, also true. `trimmed != 0` survives for the 3 legitimate cold stores
  (`ds4_server.c:16248`, `16309`), where the `tokens` argument is the full
  prompt while the *session* is at `cold_store_len` — the library compares
  against the session (`ds4_kvstore.c:3146-3149`), not against `tokens->len`,
  so `hdr.tokens == live` still holds there.
  Consequence for future censuses: the `continued ∧ trimmed != 0` signature can
  no longer be used to measure this bug — use `discarded corrupt text-prefix
  payload` (should go to ~0) instead.
* `divergence anchor stored tokens=%d (target %d)` keeps printing live length +
  pending target (same format), but now appears only when the anchor is at or
  above its target. Visible in the captured runs: RED prints the three
  below-target churn lines, GREEN does not (§5).

---

## 3. Tests

All model-free, in the `DS4_SERVER_TEST` suite, run with
**`make test-server-logic`** (safe alongside the engine).

Suite size, measured rather than quoted: HEAD defines 52 symbols named
`test_kv*`, of which **48 are registered tests** in
`ds4_server_unit_tests_run` (`git show HEAD:ds4_server.c | sed -n
'/ds4_server_unit_tests_run/,/^}/p' | grep -c '^    test_kv'` → 48; the other 4
are stub-file helpers like `test_kv_stub_file`). The brief's "52" is the symbol
count. My change adds 5 registered tests → **53**, all registered
(`ds4_server.c:29435-29439`), and not one of the 48 (nor the 5) round-trips a
payload — see "What cannot be tested" below.

| test | line | pins |
|---|---|---|
| `test_kv_cache_continued_uses_aligned_frontiers` (corrected, not weakened) | 25337-25345, 25388-25403 | 24 original grid asserts **+** for the same numbers `store_len == note_len == live` |
| `test_kv_cache_continued_store_uses_live_length` | 25421-25453 | FIX 1 truth table: snapped→live, in-grid→unchanged, `target==0`→nothing, `target>live`→fail closed |
| `test_kv_cache_divergence_anchor_waits_for_target` | 25455-25502 | FIX 2 truth table: below-target silent+pending, nothing-pending, reached→fire at live+consume, at-target, covered, covered-and-below (cannot smuggle a store) |
| `test_kv_cache_maybe_store_continued_stores_live_length` | 25554-25635 | **drives the production function**: reason strings, lengths reaching the store, watermark, pending-target state, both anchor branches |
| `test_kv_cache_continued_store_cadence_across_rungs` | 25637-25670 | (a): 88 prefill chunks from the measured 251255 resume frontier → exactly 4 stores, each at live, strictly increasing, watermark = last live length |
| `test_kv_cache_cold_store_suppression_survives_live_length` | 25672-25718 | (b): suppression holds, next rung at live, restore path re-arms |

Seam: `ds4_server.c:12723-12741` declares `kv_test_live_tokens` (a fake live
token *view*, because `ds4_session` is opaque outside `ds4.c`) and a bounded
recorder; `ds4_server.c:12752-12763` intercepts
`kv_cache_store_live_prefix_text` **before** it locks `inference_mu`, records
`(store_len, tokens->len, reason)` and returns `kv_test_store_ok`. Compiled out
of production. Follows the existing precedent `kv_restore_file_opens`
(`ds4_server.c:12316-12319`). The fixture pins
`DS4_KV_CONTINUED_SNAP=1` so the rung expectations do not depend on the
operator's environment (`ds4_server.c:25536-25538`).

### What cannot be tested without a model — stated plainly

**A real payload round trip is impossible in this suite, and none of the new
tests claims to be one.** Staging the payload is engine state:
`ds4_session_stage_payload(_span)` → `qwen4_session_save_payload_span`,
`ds4.c:66475` `const uint32_t rows = (uint32_t)s->checkpoint.len;` — it needs a
live `ds4_session` with Metal KV tensors, i.e. a loaded model plus the instance
lock, which `make test` requires and the live engine forbids. What the new
tests prove is the **decision**: which length reaches the store call, how many
calls fire, and what watermark/anchor state is left behind. The recorder makes
the production call site observable; it is never the thing asserted to be
correct.

Exactly what would be needed for the byte-level proof:

1. **Model-backed (the real answer).** A third arm in `test_kv_delta_parity`
   (`tests/ds4_test.c:496`, registered `--kv-delta` at `tests/ds4_test.c:8453`,
   `opt_in = false` so it runs under `make test`): with the session at an
   off-grid frontier `F` (`F - F%step < F`), run the continued store, then load
   that file and assert the restored frontier == `F` and that no `discarded
   corrupt text-prefix payload` fires. The existing arms always store at
   exactly the live frontier, so `store_len == live` trivially and the defect
   cannot appear there — one line short of the case, as the audit says (§10).
   Requires the engine stopped.
2. **Harness-backed (no model, weaker).** `tests/kv_policy_harness.c` already
   writes real v2/v3 headers with payload-shaped stubs
   (`scenario_trailer_rewrite`, `tests/kv_policy_harness.c:511`). A
   `scenario_rung_header_matches_payload` could call
   `ds4_kvstore_store_live_prefix_text` with the payload row count forced to
   the session length and then run the loader's terminal check. Needs a new
   library seam (an override for the row count in
   `ds4_session_stage_payload_span`, or a hooks field) — I did not add it:
   out of scope, and it puts a test switch into the durability path.
3. **Library guard (recommended follow-up, deliberately not landed).**
   `ds4_kvstore.c:3146-3149` accepts `live_tokens->len >= store_tokens.len`;
   requiring equality makes the class unreachable through *any* caller,
   converting a silent unloadable file into a skipped store (both a cache miss;
   only one leaks budget). The audit itself sequences it as log-first,
   tighten-later, and the brief scoped me to the two caller bugs.

---

## 4. Cadence evidence (the (a) decision, computed and then measured)

Grid as shipped: `anchor_step = 8192`, doubled above 49152 (`ds4_kvstore.c:2607
-2613`), snap-down on. Walking `251255 → 296000` in 512-token chunks from
watermark 131072, the fired (live, rung) pairs are
`(251255,245760) (262519,262144) (278903,278528) (295287,294912)`. The test
asserts the lengths are the **live** column and the final watermark is 295287;
RED produced the **rung** column and 294912 (4+1 failures, see §5). Same four
fires either way: no rung re-fires, no rung is skipped.

---

## 5. RED → GREEN evidence

### RED — final tests, decisions reverted to production's

Revert = the two helper bodies only (`store_len/note_len = target`; anchor
predicate without the below-target rule, keeping production's "store but do not
clear" below target). Binary `./ds4_server_test`, 2026-11-17 12:56:51, full log
at `/private/var/folders/gp/hjkf5cnx3vg4pxxv0zbd1c8h0000gn/T/opencode/red-final3.txt`.

```
RED exit=1
ds4-server tests: 29 failure(s)

  6 x 25402: assertion failed: p.store_len == rungs[i].live
  6 x 25403: assertion failed: p.note_len == rungs[i].live
  1 x 25434: assertion failed: p.store_len == 251255
  1 x 25435: assertion failed: p.note_len == 251255
  1 x 25471: assertion failed: p.store_len == 0
  1 x 25501: assertion failed: p.store_len == 0
  1 x 25571: assertion failed: kv_test_store_len[0] == 251255
  1 x 25573: assertion failed: slot->continued_last_store_tokens == 251255
  1 x 25580: assertion failed: slot->continued_last_store_tokens == 251255
  3 x 25593: assertion failed: kv_test_store_calls == 0
  4 x 25662: assertion failed: kv_test_store_len[i] == expected_live[i]
  1 x 25669: assertion failed: e.slot.continued_last_store_tokens == 295287
  1 x 25704: assertion failed: kv_test_store_len[0] == 25088
  1 x 25717: assertion failed: kv_test_store_len[0] == 25088
```

Mapping: 25402/25403 and 25434/25435 and 25571/25573/25580 and 25662/25669 and
25704/25717 = FIX 1 (length + recorded watermark); 25471/25501 = FIX 2 (helper);
25593 ×3 = FIX 2 (the churn, driving the real function). The sibling-branch
case (25623-25628) passed in RED as it should — that branch was already correct.

The RED binary reproduces the engine's own churn lines token for token.
Test-driven (`red-final3.txt`) vs real log
(`log/ds4-qwen.log.20261117-094911`):

```
RED:  1117 12:56:51 divergence anchor stored tokens=262656 (target 442343)
RED:  1117 12:56:51 divergence anchor stored tokens=263168 (target 442343)
RED:  1117 12:56:51 divergence anchor stored tokens=263680 (target 442343)
log:  1116 11:45:57 divergence anchor stored tokens=262656 (target 442343)
log:  1116 11:45:58 divergence anchor stored tokens=263168 (target 442343)
log:  1116 11:45:59 divergence anchor stored tokens=263680 (target 442343)
```

### GREEN — fixes applied

```
GREEN exit=0
1117 12:56:56 ds4-server: divergence anchor stored tokens=442903 (target 442343)
1117 12:56:56 ds4-server: divergence anchor stored tokens=458752 (target 450000)
ds4-server tests: ok
```

Only the two legitimate anchor fires print; the below-target ones are silent.
Full log: `…/opencode/green-final4.txt`. Repeated after the final edit —
`make test-server-logic` ×3 consecutively (last at 1117 12:59:16) plus two
direct `./ds4_server_test` runs: every one `exit=0`, `ds4-server tests: ok`,
deterministic.

### Build

```
$ make                                   # production objects
  (no warning, no error lines)
$ cc -O3 -ffast-math -g -mcpu=native -Wall -Wextra -std=c99 -fsyntax-only ds4_server.c
  → 0 warnings
$ touch ds4_server.c && make ds4_server_test.o | grep "^ds4_server.c.*warning" | sort
  → 10 warnings, byte-identical to the same command on HEAD
     (stop_signal_handler, kv_cache_open, decode_worker_main, worker_main,
      slot_worker_main, client_main, listen_on, configure_client_socket,
      log_context_memory, server_close_resources — all pre-existing
      -Wunused-function from the test build excluding main())
$ git status --short
   M ds4_server.c        # no binaries or artifacts added
```

No new warnings from touched code; no gate/flag added; no format or on-disk
version change.

---

## 6. Numbers I measured myself (I did not take the brief's figures on trust)

From `log/ds4-qwen.log.20261117-094911`:

| quantity | my count | brief/audit |
|---|---|---|
| `kv cache stored` events | 792 | 792 ✓ |
| stores with `trimmed != 0` | **187** = 184 `continued` + 3 `cold` | 187 ✓ |
| distinct shas among them | **181** | 181 ✓ |
| summed `size=` of the 184 continued trimmed≠0 | **113.62 GiB, mean 630 MiB** | mean 629 MiB ✓ |
| on-disk census (last store per sha) | not recomputed (needs the dir walk) | 144 files / 89.03 GiB |
| `divergence anchor stored` lines | **342**, of which **341** have `tokens < target` | 342 / 341 ✓ |
| cold stores inside that 12-min window | **342 / 44.36 GiB**, mean 132 MiB | 44.4 GiB ✓ |
| `discarded corrupt text-prefix payload` | **41** | 41 ✓ |
| trimmed magnitudes | 1 … 6946 (32 stores at trim=1) | same shape ✓ |
| sha `e8877a36` lifecycle | `tokens=81920 trimmed=705` → discarded → re-stored `trimmed=0` | ✓ |

### Expected operational effect

* **FIX 1** — no change in write volume (same payload, same rows, same file
  size class). The win is that ~184 rung writes per two-day window (113.62 GiB
  of events, 89+ GiB of resident files, ≈35 % of the 256 GiB budget per the
  audit) stop being *indexed, charged, offered as candidates and guaranteed
  unloadable*. Removes the discard cycle: 41 events ⇒ 41 full payload reads +
  41 rewrite + 41 session invalidations + full re-prefills. Loadable rungs
  gained: every snapped rung becomes a real reuse target instead of a
  write-only charge.
* **FIX 2** — removes 341 of 342 anchor stores / ~44.36 GiB of writes per such
  episode, ≈60 s of `inference_mu` hold at the measured 130-230 ms/store (the
  audit ties 14 of the 101 sub-10 t/s decode samples to that 12-minute window),
  and stops manufacturing the 342-link 512-token chain that feeds the 14 352
  deferral lines (audit finding 5 — the machinery itself is untouched).
* Combined: ~43 % of store *events* in the window go away (the audit's own
  estimate), which is what makes the S1 store-split hold telemetry
  interpretable. Both fixes land *before* that split, as the audit sequences.

---

## 7. What these fixes do NOT cover

1. **Existing doomed files stay on disk.** Prospective fix only: ~89 GiB of
   `trimmed != 0` files keep their short header until the loader meets them
   (which unlinks them *without* the children check — audit finding 4, not
   fixed here) or eviction retires them. No backfill or census tool added, and
   I could not verify their present count myself (that needs the KV dir walk
   the audit did).
2. **Library-side permissiveness.** `ds4_kvstore.c:3146-3149` still accepts a
   store length shorter than the session length, so another caller can
   reintroduce the class (see §3.3 for the recommended tightening).
3. **`ds4_kvstore_maybe_store_continued` still has FIX 1's bug**, verbatim, at
   `ds4_kvstore.c:3489-3492` (`store_live_prefix(…, target, "continued")` +
   `note_store(kc, target)`). Verified dormant: references are only the
   declaration (`ds4_kvstore.h:360`), the definition, and a comment
   (`ds4_kvstore.h:147`) — **no caller anywhere in the tree** (grep across
   `*.c/*.h/*.m`). It emits no such files today. Left alone: public API,
   outside the two-bug brief. Should get the same change if it is ever wired up.
4. **Audit findings 3-8** (fsync-before-rename, loader unlink vs children,
   deferral convergence, O(files) `kv_cache_refresh`, trailer rewrite, dead
   retention inputs) untouched.
5. **Locking, on-disk format/version, budget policy, retention policy, the
   snap-down feature** untouched. The payload staging path untouched. The cold
   path (`ds4_server.c:16248/16309`) untouched.
6. **Side effect of FIX 1 that no model-free test can cover.**
   `durable_frontier_tokens`/`durable_conv` are only updated when
   `store_len == tokens->len` (`ds4_server.c:12972-12983`), so continued stores
   now mark the frontier durable and reset `durable_conv` to 0 for text — exactly
   what evict/turn/shutdown stores already do, and correct by that block's own
   comment ("any successful frontier store leaves the live session durable at
   this length"). It relaxes the multimodal `newer_unsaved` guard
   (`ds4_server.c:16021-16022`) only in the direction of "the state really is
   on disk".
   Related, and **untested (needs a model)**: a vision session's snapped rungs
   used to be rejected outright at `ds4_server.c:12798-12802`
   (`okv = vs->valid && store_len == tokens->len`, reason `not-frontier`), i.e.
   image-conditioned continued stores never happened off-grid. With FIX 1 they
   go through the frontier-snapshot path, so Vision-Exp sessions gain rung
   writes they previously did not have (still failing closed when an image span
   crosses the frontier — `image-crosses-frontier`). I judge that an
   improvement, but the volume change is real and I could not measure it
   without traffic.
7. **Delta sharing.** A rung is a `pick_parent` candidate only when
   `tokens % 4 == 0` (`ds4_kvstore.c:2771`). Rungs at the live length are
   %4-aligned whenever the frontier is a multiple of 4 (chunk-timed: yes —
   512/4096; turn- or rewind-timed: a coin flip). The old snapped rungs were
   always aligned *and never loadable*, so this trades a paper property for a
   real ladder. Not measured against traffic.

## 8. Where I disagree with the brief, or went beyond it

* **"one line / one token"**: the honest minimal change is one token in the
  store call **plus** the matching `note_store` argument (two occurrences of
  `target` → `tokens->len`), because recording the rung would leave the
  watermark disagreeing with the file just written. I implemented it as two
  pure helpers (the brief's preferred seam) and additionally routed **both**
  anchor sites through one shared predicate. The sibling site is
  behaviour-identical — proven by its case passing in both RED and GREEN — so
  that part is de-duplication, not a third change, and it is the mechanism that
  stops the two blocks disagreeing again (which is how FIX 2's bug happened).
* **Brief's numbers**: 187/181/629 MiB and 342/341/44.4 GiB all reproduce from
  the log ✓. The one thing to keep apart is "88+ GiB" (unique resident files,
  audit's last-store-per-sha census) versus my 113.62 GiB (summed store events,
  including re-stores and delta spans). Both true, different questions.
* **No kill switch.** These are strict correctness fixes: FIX 1 removes writes
  whose output is provably unusable; FIX 2 removes writes the code's own
  comments forbid; the sibling branch is the in-tree proof of the intended
  predicate. A flag would keep the bug as a supported path, against `AGENT.md`
  ("Do not add permanent semantic variants behind flags"). Rollback = revert
  the two hunks. `DS4_KV_CONTINUED_SNAP=0` already exists and stays meaningful:
  it changes the rung *schedule*, and with the fix the legacy exact-equality
  schedule is correct too (store length then equals live by construction).
* **A bug I introduced and fixed mid-task, recorded for completeness**: the
  first version of the test fixture stored the `mkdtemp` template pointer
  (a dead stack buffer) in the struct, so `rmdir()` on cleanup was UB and left
  empty dirs in `/tmp`. The final fixture copies the name into
  `kv_store_env.dir[64]` (`ds4_server.c:25511-25527`); verified: dir count in
  `/tmp` is stable across three consecutive suite runs, and the 45 leaked empty
  dirs from the earlier iterations were removed.
* If you want FIX 1 gated to non-vision stores to avoid the volume change in
  item 7 of §6, say so — I'd argue against (today's behaviour there is a store
  that always fails), but it is a two-line scope narrowing.

## 9. Things I could not verify

* No payload was ever written or read: no file bytes, no loader acceptance, no
  GPU state. All claims about "the file now loads" rest on the code path
  (`ds4_kvstore.c:3146-3149` prefix check, `ds4.c:66475` row count, the loader's
  terminal check) plus the log's own trimmed=0 counter-examples, not on a run.
* The on-disk census (144 present doomed files, 89.03 GiB, 36 % of budget, the
  one orphan) — the audit's measurement; I did not walk
  `/Volumes/FireCuda520/ds4-kv-qwen` (751+ files) myself.
* Runtime behaviour under traffic (bytes actually saved, dips avoided, Vision-Exp
  store volume) — needs a restart with the new binary, explicitly out of scope.
* `make test`, `tests/kv_policy_harness`, `--kv-delta`: not run (engine holds the
  process lock; AGENTS.md). The suite I ran is `make test-server-logic` only.
* `ds4_kvstore.c:3489`'s bug being dormant rests on a grep of this tree; if
  `ds4_agent` or an out-of-tree consumer calls it, that path is still buggy.
