# KV checkpoint deletion vs. the delta-chain children guard — audit finding resolution

Date: 2026-11-17. Repo `/Users/naz/Projects/ds4`, HEAD `a3cacc0` + my uncommitted changes.
Finding under investigation: *"the loader deletes entries via raw `unlink()` without going
through `kv_cache_unlink_entry()`, which defers deletion when `children > 0`"* — treated as a
hypothesis, not a spec.
Explicitly out of scope and untouched: `kv_cache_maybe_store_continued` and every store-length
decision (the other lane's work, shipped in `2b06dc7`). All line numbers below are the **final
tree**, i.e. after my comment insertions.

## Verdict (one line)

**(c) — leave the unlinks as they are; the current behaviour is already correct, and the
accounting is already right too.** The loader's raw `unlink()` deletes the file the chain walk has
*already proven unloadable* — the candidate, i.e. the child end of a chain, never the ancestor —
so the `children > 0` guard has nothing to protect there; `children` is a refcount rebuilt from
the directory by `kv_cache_refresh()`, which runs at the top of every load and every eviction pass,
so a raw unlink cannot strand a parent; and routing the loader through the helper would *keep*
provably-dead files and corrupt the load retry loop's index-keyed skip list. What was missing was
the record and the tests, so I landed comments at every site plus two model-free tests, and **no
production statement changed**.

## 1. Enumeration of every `unlink(` site

`ds4_kvstore.c` (7 sites):

| # | Site | Reached when | Chain role of the deleted file | In-memory entry | `children` / parent accounting |
|---|---|---|---|---|---|
| 1 | `kv_cache_unlink_entry()` `ds4_kvstore.c:1974` | every eviction route: `redundant` `ds4_kvstore.c:2275`, `legacy-lru` `ds4_kvstore.c:2329`, `conversation-retired` `ds4_kvstore.c:2070`, `redundant-divergent` `ds4_kvstore.c:2125`, `frontier-superseded` `ds4_kvstore.c:2209` — and only after the `children > 0` deferral branch at `ds4_kvstore.c:1954` fell through | any (leaf, or a parent whose children already died) | dropped (`memmove` + `kc->len--`) | **decrements the surviving parent in-pass** (`ds4_kvstore.c:1977`, note at `ds4_kvstore.c:1975-1976`), because a pass never re-scans between picks |
| 2 | `kv_cache_sweep_orphan_temps()` `ds4_kvstore.c:2390` (at open) | `*.kv.tmp.*` older than 1 h, **or** a `*.kv` older than 24 h whose header no longer parses | never a usable parent — the chain walk reads the parent's *header*, so an unparseable file serves nobody; invisible to refresh and to the budget, hence reaped | not indexed (that is the defect it fixes) | none needed |
| 3 | `ds4_kvstore_existing_compatible()` `ds4_kvstore.c:3004` (store reuse path) | the on-disk file for this sha is **incompatible** (model / quant / ctx / text mismatch) and the store is about to write the *same path* | pure opportunistic space release | not indexed (stores never push; the scan re-derives) | **guarded by dependents**: `kv_dir_has_dependents()` at `ds4_kvstore.c:3003` skips the unlink if any live file names this sha as `parent_sha` (review M-4); harness Scenario H pins it |
| 4 | `ds4_kvstore_rewrite_trailer()` `ds4_kvstore.c:3118` | an in-place trailer rewrite could not be completed, leaving an inconsistent envelope | the file being reused; it *can* be a chain parent (the payload region is untouched by design, so failure means the envelope is untrusted) | not touched here | self-heals at the next refresh; a descendant declines at the ancestor walk |
| 5 | store failure `ds4_kvstore.c:3430` | the staged write failed | a `.kv.tmp.*` — never indexed, never referenceable | none | none |
| 6 | loader, `delta chain broken` `ds4_kvstore.c:3838` (block `ds4_kvstore.c:3822`, log `ds4_kvstore.c:3826`) | the candidate is a v3 delta child and its ancestor walk failed: parent file absent, or the parent's header disagrees with the link (`ph.tokens != cur.delta_from`, quant, model, `model_fp`, byte-prefix text, tokenizer fingerprint). The 63-link depth cap is **not** here — that path keeps the file (`ds4_kvstore.c:3804`/`ds4_kvstore.c:3815` "file kept") | **the candidate itself = the child end**, never the ancestor | **deliberately left in place** — see §4(a)2 | stale until the next refresh, which always precedes any consumer |
| 7 | loader, `discarded corrupt text-prefix payload` `ds4_kvstore.c:3929` (block `ds4_kvstore.c:3918`, log `ds4_kvstore.c:3934`) | the payload loaded (`lrc == 0`) but the session's token history != the envelope's `tokens` (`ds4_kvstore.c:3896`): the file self-contradicts and can never serve a terminal load | **the candidate itself**; it may be a mid-rung with live children — this is the site that produced the measured orphan | left in place (same reason as #6) | same |

`ds4_server.c` — production code has exactly two (everything from `ds4_server.c:25900` on is test-fixture
cleanup; `tool_memory_unlink` `ds4_server.c:11256` is a linked-list removal, not a syscall):

| # | Site | Reached when | Notes |
|---|---|---|---|
| 8 | `kv_cache_reload_repeat()` `ds4_server.c:13115` (reason `frontier-contradicted`) | two consecutive disk restores of the same file at the same frontier for this slot → its tail is provably dead weight for this prompt | deletes the file that just loaded (the chain's terminal link; may be a parent of newer rungs). Outside the kvstore index; the next refresh drops it and re-derives `children` |
| 9 | `kv_cache_discard_failed_disk_entry()` `ds4_server.c:13131` (reason `prefill-failed`) | a disk entry restored fine and the prefill that followed failed | as #8. Zero field events, so this one is reasoned, not observed |

And the branch that **does not** unlink: `ds4_kvstore.c:3940-3950` (`lrc != 0`, "load failed: …
(falling back)"). It cannot distinguish "this file is garbage" from "the blade hiccupped" — that
branch also takes accelerator-sync failures, I/O errors, and links that raced with eviction
(`delta chain file vanished` `ds4_kvstore.c:3864` / `changed mid-load` `ds4_kvstore.c:3875`). Deleting there would lose
good checkpoints, so the loader only ever deletes a file it proved unusable from its own contents.

## 2. What `children` means, who moves it, and what a chain walk does today

* `children` is **in-memory only, never persisted** (`ds4_kvstore.h:93-97`: "a refcount built at
  scan time"). Three writers:
  * `kv_cache_refresh()` (`ds4_kvstore.c:704-742`, counting loop `ds4_kvstore.c:721-730`) — `clear()` + full
    directory rescan + a `parent_sha` match that increments the parent for every delta child found
    on disk. Authoritative, idempotent, and the reason raw unlinks are safe.
  * the store path `ds4_kvstore.c:3434` — `children++` after a successful delta store;
  * `kv_cache_unlink_entry()` `ds4_kvstore.c:1977` — the in-pass decrement.
  The last two are conveniences *within* a pass; the next scan reproduces their result.
* Every consumer that could act on a stale count refreshes first: `ds4_kvstore_evict` `ds4_kvstore.c:2221`,
  `ds4_kvstore_try_load_text` `ds4_kvstore.c:4005`, the delta parent pick `ds4_kvstore.c:3238` (gated by the same
  `kv_delta_enabled()`), `ds4_kvstore_find_text_prefix` `ds4_kvstore.c:3614`, the post-store divergent sweep
  `ds4_kvstore.c:3514`+`ds4_kvstore.c:3516`, and the server after a load (`ds4_server.c:13273`). **No accounting consumer
  reads `children` without a preceding scan** — which is the whole safety argument for #6/#7/#8/#9.
* Chain walk on a missing or corrupt ancestor (block `ds4_kvstore.c:3754-3822`, per-link check `ds4_kvstore.c:3762-3791`): for a v3 child it re-opens each
  ancestor by `ds4_kvstore_path_for_sha()`, reads its header, and requires frontier exactness
  (`ph.tokens == cur.delta_from`), equal quant/model, a matching-or-legacy `model_fp`, the
  ancestor's text to be a byte-prefix of the child's, a present tokenizer fingerprint, and a
  consistent `delta_from`/`parent_sha` pair — **all before the session is touched**. Any failure →
  `chain_bad` → drop the candidate (#6), `*retryable = true` → the caller tries the next-largest
  anchor or cold-rebuilds. The message is
  `kv cache delta chain broken, dropping orphan <file> (falling back)` — a deletion line, not a
  warning. So a broken chain is detected, never retried against the same file, never silently
  degraded, and never an output-correctness risk. The only post-payload failure paths
  (`ds4_kvstore.c:3864`/`ds4_kvstore.c:3875` mid-load vanish/mutation, and `ds4_kvstore.c:3896`'s length check) invalidate the session
  and fall back, which is safe because a reset session is exactly what the fallback needs.

## 3. Field evidence (read-only; current disk state)

Census of `/Volumes/FireCuda520/ds4-kv-qwen` (753 indexed files, 255.96 GiB against a 256 GiB
budget — pinned):

* chain-unloadable files (some ancestor absent): **1** — `323882ce9b03eb8b81d1666dc2f6453b5b1798bd`
  tokens=96387, `delta=81920..96387`, reason=evict, **0.571 GiB** = 0.22 % of budget, parent
  `25c52da8ceb5…` absent, its own `hits=0` (never loaded).
* log counts over all `log/ds4-qwen.log*` (~10 days): `delta chain broken` **0**,
  `discarded corrupt text-prefix payload` **50**, `frontier-contradicted` **96**,
  `reason=prefill-failed` **0**, `delete deferred` **17 715** (17 488 of them `reason=redundant`),
  `kv cache evicted` **1087**.
* **Provenance of the one orphan — it is a symptom of the store bug, not of a missing guard:**

  ```
  1116 07:41:20 … kv cache stored tokens=81920 trimmed=1 reason=continued sha=25c52da8 delta=65536..81920 size=647.43 MiB
  1116 07:45:06 … kv cache stored tokens=96387 reason=evict sha=323882ce delta=81920..96387 size=585.11 MiB
  1116 07:45:20 … kv cache discarded corrupt text-prefix payload …/25c52da8ceb5e5b80773274bb2e576728330a829.kv (falling back)
  ```

  `trimmed=1` on a `reason=continued` store is exactly `2b06dc7` FIX 1: the envelope carried the
  snapped-down rung length while the staged payload carried the live length, so the terminal check
  (`ds4_kvstore.c:3896`) rejected it every time. Deleting it was **correct** — as a terminal link it could never
  load. The orphan is that bug's fossil, and the bug is closed.
* The audit's `grep -c "delta chain broken" == 0` is explained by candidate ordering, not by the
  guard: a deeper file normally matches first, so an orphan is only reached as a fallback — at
  which point #6 deletes it. Zero occurrences plus one stranded file over 10 days is consistent
  with "shadowed, then reclaimed by the budget passes".
* Content-addressing does most of the self-healing: of 69 distinct files the raw sites deleted in
  the logs, 38 are still named as `parent_sha` by a live file today — and 37 of those 38 sha
  paths exist on disk again, re-written by a later store of the same text (the file name is
  `sha1(text)`), which restores the chain. Exactly one was not re-written, because that rung's key
  text never recurred: the 0.571 GiB orphan above.

## 4. Why each alternative was rejected

* **(a) route the raw sites through `kv_cache_unlink_entry()`** — three concrete harms, all
  visible in the code, any one of them disqualifying:
  1. The helper's `children > 0` branch (`ds4_kvstore.c:1954`) **keeps the file** and drops only the index row.
     For a file the loader has just proven unloadable that means: it is re-picked on the next load
     of that prefix, re-read in full, and — at #7 — calls `ds4_session_invalidate()` on the live
     session *again*, forever, while the file's descendants can never load through it either. The
     deferral wall (17 715 `delete deferred` lines, 22-25 re-proposals per blocked node per day,
     the pathology the backoff table exists for, `ds4_kvstore.c:1857-1873`) would move from the eviction path
     into the read path.
  2. The helper mutates `kc->entry` (`memmove` + `kc->len--`). `ds4_kvstore_try_load_text` retries
     largest-first with `int skip[16]` (`ds4_kvstore.c:4012`) holding **entry indices**; a mid-loop removal
     shifts every later index, so a valid candidate gets skipped (needless full prefill) and an
     already-tried failing candidate can be re-picked (repeated payload reads and session
     invalidations, bounded only by `skip_len == 16`).
  3. It is eviction accounting: it decrements a `total` the load path does not own and logs
     `kv cache evicted reason=…`, erasing the forensic distinction that made this finding
     measurable (`discarded corrupt text-prefix payload` vs `evicted`).
* **(b) cascade-invalidate/delete descendants of a corrupt parent** — rejected on cost and risk.
  Those descendants are (i) already fail-closed: they decline at the ancestor walk when picked;
  (ii) leaves or soon-to-be leaves, so no deferral can pin them; (iii) exactly the class the budget
  machinery already owns — the measured orphan is `reason=evict`, `children==0`, i.e. the eager
  frontier-supersede sweep's target class (doctrine `ds4_kvstore.c:2129-2153`, `children == 0` predicate
  `ds4_kvstore.c:2173`, unlink `ds4_kvstore.c:2209`). An eager cascade would need a directory scan *inside* the load loop —
  the one place that must not mutate the index — to reclaim a measured 0.22 % of a pinned volume,
  and it would delete files whose parent may yet be re-written under the same sha.
* **(c) leave the unlinks; fix the accounting instead** — the accounting needs no fix (§2), and the
  "stop offering a corrupt parent's descendants" framing would be a scan-side filter, which is
  provably the wrong layer: mutation M5 in §6 shows it silently drops candidates that the
  content-addressed in-place rewrite makes loadable again.
* **(d) something better** — I found one genuine asymmetry, and it is *not* a deletion bug: the
  `ds4_kvstore.c:3940-3950` branch leaves a payload-level-failing file in place forever, so every future load of
  that prefix pays a full payload read plus a session invalidation. It is right to leave it (see
  §1's last paragraph), and it is now documented as such. If the `fsync`-before-`rename` gap
  (handoff step 7) ever lands, the zombies it produces surface exactly there, and that deserves a
  classify-then-drop change with real failure-mode evidence — not this audit's guesswork.

## 5. What changed

`ds4_kvstore.c` — **+40/-1, and `git diff -U0` contains no non-comment `+`/`-` line** (the single
`-` is a re-wrapped comment tail). Comments only, at:

* `kv_cache_unlink_entry` header (`ds4_kvstore.c:1937-1950`) — what it does, that it is eviction's route, the
  two reasons the loader must not use it, and that nothing strands either way;
* the in-pass parent decrement (`ds4_kvstore.c:1975-1976`);
* #6 (`ds4_kvstore.c:3830-3837`, above the unlink at `ds4_kvstore.c:3838`) — the deleted file is the candidate, and why
  deferring it would be wrong;
* #7 (`ds4_kvstore.c:3920-3928`, above the unlink at `ds4_kvstore.c:3929`) — self-contradicting file, re-invalidations
  avoided, content-addressed replacement;
* the `lrc != 0` branch (`ds4_kvstore.c:3942-3950`) — why there is *no* unlink there.

`ds4_server.c` — production: two comment blocks only (`ds4_server.c:13110-13114` at #8, `ds4_server.c:13126-13130` at #9).
Test region (+250 lines, no production line changed): fixture `test_kv_delta_stub_file` (`ds4_server.c:25907`),
helper `test_kv_entry_index` (`ds4_server.c:25956`), and two tests registered at `ds4_server.c:29743-29744`:

1. **`test_kv_cache_raw_unlink_recounts_children_on_refresh`** (`ds4_server.c:28448-28553`) — the loader's
   deletion model at the index layer. Scan arms `children` (`ds4_server.c:28487`); a raw unlink of the delta
   child leaves the index untouched and the parent's count merely stale (`ds4_server.c:28498`, `ds4_server.c:28501`) — the
   property the helper would destroy and the reason the retry loop's `skip[]` stays valid; the next
   refresh drops the phantom and re-derives `children` to 0 (`ds4_server.c:28507`, `ds4_server.c:28508`, `ds4_server.c:28511`); in the
   corrupt-parent direction the orphan stays a leaf (`ds4_server.c:28531`) *and* stays offered by the scan
   (`ds4_server.c:28544`, `ds4_server.c:28545`), pinning the division of labour: the index offers, the load path's ancestor
   walk declines.
2. **`test_kv_cache_unlink_entry_children_bookkeeping`** (`ds4_server.c:28555-28638`) — pinned at
   `ds4_kvstore_sweep_small_dense_divergents`, a production entry point called by
   `ds4_kvstore_open` (`ds4_kvstore.c:2463`) and by the post-continued-store maintenance path (`ds4_kvstore.c:3516`), and the
   only production route to `kv_cache_unlink_entry` that runs on the caller's index *without*
   refreshing first (`ds4_kvstore_evict` refreshes at `ds4_kvstore.c:2221` before unlinking at `ds4_kvstore.c:2275`/`ds4_kvstore.c:2329`,
   so it re-derives the counts and cannot show in-pass bookkeeping). It pins that freeing a delta
   child drops the parent's count in the same pass (`ds4_server.c:28618`), and that a victim with a live child
   is **deferred — entry gone, file kept** (`ds4_server.c:28624`, `ds4_server.c:28625`, `ds4_server.c:28627`), which is precisely the
   behaviour the load path must not inherit.

A real disk-level chain-load round-trip is **not** possible model-free: `kv_cache_try_load_one`
needs an engine, a session and the Qwen3.8 graph (`ds4_session_load_payload_span` is
`DS4_HAS_QWEN4_GPU`-only), and `make test` needs the engine stopped. The tests above therefore pin
the index/accounting contract at production call sites and assert nothing about loader behaviour —
stated so nobody reads them as loader coverage (see §8).

## 6. RED/GREEN mutation evidence

Five mutations, each applied to `ds4_kvstore.c` alone, object files force-rebuilt (`rm -f
ds4_kvstore.o ds4_server_test.o ds4_server_test`) because the Makefile compares at second
granularity, failing asserts captured by name, source restored after every one:

| # | Mutation (the regression it stands for) | Result |
|---|---|---|
| M1 | `kv_cache_refresh` stops deriving `children` from the scan (`ds4_kvstore.c:721-730` loop never runs) | 5 failures: `ds4_server.c:28487`, `ds4_server.c:28501` (test 1) · `ds4_server.c:28603`, `ds4_server.c:28605` (test 2 preconditions) · `ds4_server.c:28625 kv_file_exists(dir, gb_parent)` — the guard cannot even arm without the scan |
| M2 | `kv_cache_unlink_entry` stops decrementing the surviving parent (`ds4_kvstore.c:1977`) | **1** failure: `ds4_server.c:28618 kc.entry[a].children == 0` — exactly the in-pass accounting claim |
| M3 | the `children > 0` deferral is removed (`ds4_kvstore.c:1954`) | **1** failure: `ds4_server.c:28625 kv_file_exists(dir, gb_parent)` — proves the "helper keeps the file" half that disqualifies it for the loader |
| M4 | `kv_cache_refresh` keeps phantom entries (`clear()` disabled) | 5 failures: `ds4_server.c:28507 kc.len == 1`, `ds4_server.c:28508`, `ds4_server.c:28511`, `ds4_server.c:28527` (test 1's self-healing claim) plus pre-existing `ds4_server.c:27263` |
| M5 | the candidate scan filters out a child whose parent file is absent (the tempting index-side "fix", inserted in `kv_cache_find_text_prefix_skip`) | **1** failure: `ds4_server.c:28544 offered >= 0` — the decline belongs in the load path; a scan-side filter silently drops candidates that a later in-place rewrite restores |
| — | restored tree | `ds4-server tests: ok` (exit 0); `make` exit 0 with **0 warnings** in the full build log |

## 7. Expected operational effect

None at runtime. No statement changed: no checkpoint is deleted or retained differently, no chain
walk altered, no on-disk format or header version change, no new gate or flag, no budget or
policy change. The measured orphan (0.571 GiB, 0.22 % of a pinned 256 GiB volume) stays until the
budget passes take it, exactly as today.

What changes is the failure mode of the next audit: the invariant is now written at each site, and
the five regressions that *would* make this finding real — a scan that stops re-deriving `children`
(M1), an unlink that stops decrementing the parent in-pass (M2), a deferral that stops protecting
live children (M3), a refresh that stops dropping phantoms (M4), a scan that starts filtering chain
children (M5) — each fail `make test-server-logic` by name.

If the operator wants the 0.571 GiB back sooner than the budget passes get there: the file is a
`reason=evict`, `children==0` snapshot (the frontier-supersede sweep's own class), or it can simply
be `rm`'d — nothing loadable references it, and its parent is already gone.

## 8. Could not verify

1. **A disk-level load round-trip of the chain walk** (as stated in §5): the loader needs engine +
   session + the Qwen3.8 graph, and `make test` needs the engine stopped, which the standing
   instructions forbid. So the loader's own behaviour — ancestor decline, session invalidation, the
   `ds4_kvstore.c:3838`/`ds4_kvstore.c:3929` deletions — is asserted **nowhere**; the tests pin the index and accounting
   contract that the deletion model relies on.
2. **That the helper's `memmove` would actually break `skip[]`** in a live retry loop: argued from
   the code (`skip[16]` at `ds4_kvstore.c:4012` holds indices into `kc->entry`; the helper shrinks it at
   `ds4_kvstore.c:1969-1971` (deferral path) and `ds4_kvstore.c:2007-2009` (free path)), not executed — the loop is unreachable model-free.
3. **Whether the frontier-supersede sweep really reclaims `323882ce9b03…`**, i.e. whether
   `tail_anchors` live snapshots strictly byte-prefix-extend its text. Verifying means reading
   hundreds of multi-MiB text sections off the blade, which would perturb the concurrent KV-I/O
   hold-attribution work in the other lane. Deliberately skipped.
4. **`reason=prefill-failed` (site #9) has zero field events**, so its behaviour is reasoned, not
   observed.
5. **Multi-link dead ladders** (a dead rung that still has a dead child): argued from the code —
   the child keeps the parent deferred until the child itself dies, so the ladder unwinds leaf-first
   over successive passes — but only the single-link case is pinned by a test.
6. **`delta chain broken` has 0 occurrences in ~10 days of logs**, so "descendants self-delete at
   the chain check when picked" is code-derived, not field-confirmed.
7. Pre-`2b06dc7` behaviour of the payload-discard site toward a *mislabeled* (not corrupt) parent:
   reading `qwen4_session_load_payload_span` (`ds4.c:66595+`) suggests a descendant could still have
   loaded through such a parent, since the span uses the payload's own row count. Untested — it
   needs a model, and the class no longer exists after the store fix.

## 9. Tree state (modified, not committed)

```
$ git status --short
 M ds4_kvstore.c
 M ds4_server.c

$ git diff --stat
 ds4_kvstore.c |  41 +++++++++-
 ds4_server.c  | 250 ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 290 insertions(+), 1 deletion(-)
```

Checks run: `git diff -U0` on `ds4_kvstore.c` yields **only comment lines**; `ds4_server.c`'s only
production hunks are the two comment blocks (`ds4_server.c:13110-13114`, `ds4_server.c:13126-13130`); `make` exit 0 with 0
warnings; `make test-server-logic` green (`ds4-server tests: ok`, exit 0); `git ls-files | grep -E
'\.o\.tmp$|^tests/(kv_policy_harness|test_prompt_prefix|test_spec_rejection)$'` empty and
`git check-ignore tests/kv_policy_harness` matches. The engine (PID 69332) was never stopped,
restarted or loaded — only logs read, plus a read-only header census of the KV directory. Nothing
committed, nothing pushed.
