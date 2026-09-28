# Bounded review — DS4_KV_DELTA_FULL_REASONS (kvstore) + tool-map bootstrap index (server)

## Verdict: APPROVE-WITH-NITS (conditional on must-fix M1, ~3 lines)

Neither diff weakens a verification check and neither can corrupt a KV file. The
index change does introduce one permanent silent-degradation path (M1) that did
not exist when every request rescanned. Budget note: 6 of 8 calls spent; the KV
*load* path and `id_list_push_unique`'s copy semantics were not read (see R1/R2).

## KVSTORE

**1. Can a newly chainable reason pick a non-prefix parent?** No. The diff only
widens the gate at ds4_kvstore.c:2162-2172 (`kv_delta_reason_chainable`);
`kv_store_pick_parent` (ds4_kvstore.c:1804-1883) is untouched and still requires
all four, in order: header re-read matching the index entry (`ph.tokens ==
o->tokens`, `ptb>0`, `ptb<=text_len`, :1847-1849), byte-exact text prefix
(`memcmp(pt,text,ptb)`, :1854), tokenizer fingerprint present and matching
(:1859-1860), and exact payload token span against the child's tokens
(:1862-1864) — the last is ground truth, so a coincidental text prefix still
fails. Candidates are pre-filtered on quant/model/`tokens%4==0`/strictly-shorter
(:1821-1825). Each rejected candidate is permanently skip-marked, so the loop is
bounded by `kc->len`; **no ancestor verifying ⇒ return -1 ⇒ full node** (:1834).
Fail-closed confirmed. `cold`/`evict`/`shutdown` reach the same call site with
the same `text`/`store_tokens`, so the reason cannot influence parent selection.

**2. Retention / orphaned shutdown node.** Eviction does defer a parent while a
child lives (ds4_kvstore.c:673; "delete deferred … children=" at :1236), so the
common case holds. If the parent is nonetheless gone: a delta node's payload
carries only tokens `[from..len)`, so **no shallower ancestor can substitute** —
the resume either restores from an independent full node that happens to cover a
prefix of the same text, or cold-prefills. The orphan file is header-valid, so it
is not "corrupt", but it stays in the index and on disk, counts against
`cold_max`, and is re-verified-and-rejected on every load attempt. **Not checked
in this lane:** whether the loader actually degrades that way or errors out
(R1) — that is the one kvstore question I could not close within budget.

**3. getenv per store.** Functionally fine and intentionally uncached. getenv is
a linear environ scan, negligible against a multi-MiB/GiB write; it is *not*
thread-safe against concurrent `setenv`, but only the test harness calls setenv
(tests/ds4_test.c:537,597,648,763) — a race there only if a slot worker stores
during the toggle. Mid-flight toggling cannot corrupt anything (pick_parent
re-verifies per store). Nit: only the literal `"0"` disables, so `false`/`no`
silently leave chaining ON, and unset = ON.

**4. Test edits load-bearing?** Partially. Four sites (536-540, 596-600,
647-652, 762-767) bracket a store with `DS4_KV_DELTA_FULL_REASONS=0` and restore
after — intent preserved, and env restore is exception-unsafe only if an assert
returns early. The real weakness: a **negative-only** test. With the switch set to
0 the test passes both when the switch is honoured *and* when no chainable parent
existed at all, so "forces FULL" is load-bearing only if the assertion inspects a
full-node property (no `delta=`/`delta_from`, full payload size) **and** a paired
positive control asserts a delta *is* produced with the switch on. I did not read
the assertion bodies; if there is no positive control, add one (M2).

**5. `sha=` on the log line.** Safe. It is appended inside the existing format
(`key=%s sha=%.8s%s`) with `delta_note` still leading its own space, so
`reason=`/`key=`/`size=` token parsers keep working; only a parser that assumes
`size=` immediately follows `key=` breaks. `%.8s` on a 41-byte NUL-terminated sha
cannot over-read. Nit: 8 hex chars is a label, not an identity — ~50% collision
by ~65k distinct files, so do not join forensics on it alone.

## SERVER

**6. Locking.** No self-deadlock: phase 1 holds `tool_mu` and calls only
opendir/readdir/fopen/fread + index mode (which touches nothing but the rax);
phase 2 releases it before `kv_trailer_walk_tool_map(…ix=NULL…)` →
`tool_memory_put_source`. Two threads cannot index the same new file (phase 1 is
serialized, and `tool_map_index_note` is first-wins via its `raxFind` guard).
Real cost: phase 1 does **blocking disk I/O for up to cold_max files under the
global `tool_mu`**, so the first request after boot head-of-line-blocks every
other request thread. Torn read: phase 2 uses `files.v[i]` after unlock — safe
*iff* `id_list_push_unique` copies the string (R2); if it aliases the rax-owned
value, `tool_map_index_drop_file`/`clear_entries` in another thread frees it →
use-after-free. Confirm R2 before trusting this.

**7. Staleness via in-place trailer rewrite.** Consequence is a **missed restore,
not corruption**: the name stays in `seen`, the new ids are never indexed, so
`tool_memory_attach_to_messages` finds nothing for them, `raw_tool_text` is NULL,
the prompt is re-rendered from structured fields, and the checkpoint is silently
non-exact (plus a KV cache-key miss). KV nodes verify independently, so no bad
data is ever loaded. It is permanent until restart. `kv_cache_rewrite_trailer`
did not grep-match in ds4_server.c, so I reasoned from the described behaviour.

**8. `drop_file` iterator restart.** O(k·n): k ids on the removed file × n total
indexed ids, each restart a full `raxSeek("^")` walk, all under `tool_mu`.
Worst realistic case ≈ tens of ids × 10^5 = 10^6 steps (single-digit ms, but
mutex-held); pathological case (a file with ~500 ids against a 1M-id index) is
~5×10^8 steps = a multi-second stall of every request thread. Reachable only via
Q10's cap. Trivial fix: collect matching keys in one pass, `raxStop`, then remove.

**9. `seen` memory.** ~44-byte key + rax overhead ≈ 100-150 B/entry ⇒ **~3-5 MB
at cold_max=30000 — does not matter.** What matters is that `seen` is keyed on
*every name ever observed*, never pruned on unlink, so a long-lived server with
churn grows without bound (10^7 names over days ≈ >1 GB). The id rax is the
capped one (1M × ~150 B ≈ 150 MB worst case); `seen` is uncapped (M3).

**10. Cap thrash.** Yes, and worse than described: at 1M ids, `note` clears
**both** raxes, so the next request re-opens every file in the dir (30k opens
under `tool_mu`) to rebuild — and 30k files × ~33 ids reaches 1M, so this is
attainable with tool-heavy sessions, not merely theoretical. Failure mode is a
latency cliff rather than wrong output, *except* for one correctness wrinkle: the
clear can happen mid-file, after some of that file's ids were noted; the file is
still marked `seen` at the end of the loop, so its earlier ids are lost
permanently. Acceptable as a bound, not as a policy — prefer LRU/oldest-file
eviction + a log line when the cap fires.

**11. Highest-value missing test.** Every call uses exactly **one** wanted id and
every file holds exactly **one** id, so the dedup that the whole design rests on
is never exercised: a regression that opens a file once per wanted id, or that
fails to resolve two ids from two files, still passes `== 1`. Add: one request
wanting 2 ids that live in the **same** file ⇒ exactly 1 open and both restored;
and 2 ids in 2 files ⇒ exactly 2 opens. Runner-up: a truncated/unreadable file is
marked `seen` on first sight (M1) and never retried — no test covers recovery.

## Must fix
- **M1** ds4_server.c phase-1 loop: `seen` is inserted unconditionally, including
  when `fopen` failed or the trailer walk errored. A checkpoint being written
  concurrently by a slot worker (or any transient I/O error) is therefore
  poisoned for the process lifetime; the old per-request rescan self-healed.
  Fix: mark seen only when the header read *and* the walk succeeded; otherwise
  retry next request (optionally with a small backoff set).
- **M2** Add the positive control for `DS4_KV_DELTA_FULL_REASONS` (switch ON ⇒
  delta node observed) so the forced-full assertions cannot pass vacuously.

## Nice to have
- Cap/prune `seen` on unlink or by size (M3); log when the 1M id cap fires.
- `drop_file`: two-pass remove instead of iterator restart.
- Move phase-1 disk I/O out from under `tool_mu` (build locally, merge under lock).
- Document that only the literal `0` disables the switch.

## Declined to judge
- KV load/resume path and orphan-delta handling — out of remaining budget (R1).
- `id_list_push_unique` copy-vs-alias semantics — not read (R2); flagged in Q6.
- Lock-order inversion between `tool_mu` and `inference_mu` from paths outside
  this diff — not inspected.
- Suite/lint/build results — executor-provided; not re-run per instruction.
- rax internals, `kv_fill_header` test fixture fidelity, image-cache interaction.
