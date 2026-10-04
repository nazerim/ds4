# Deep-context decode regression 2026-10-04 — found (operator report), root-caused, fixed, closed

Symptom (operator): live agent session at ctx ~167k decoded 39-42 t/s; "prior to
all this work" solo deep decode ran 50-60 t/s all the way to 300-400k.

## Bisect record (all solo, same engine binary where noted)

- `tests/spec_economics/ctx_curve.py` (durable tool) exact stack (SINGLE_TREE +
  GROUP_EXACT + rowe): 43.7 @25k / 41.2 @51k / 40.7 @102k / 40.9 @128k /
  42.3 @205k / 35.7 @256k / 36.8 @320k. Short-ctx: 72-75. Cliff at the SPARSE
  switch (~ctx 2048), flat beyond — NOT O(ctx) growth.
- Same tool, drift dispatch (`QWEN_EXACT_VERIFY=0`): 57.4 @3.4k / 56.3 @13k /
  54.2 @51k / 57.6 @102k / 57.1 @205k / 54.3 @320k — the operator baseline
  reproduced exactly. Regression = exact stack, ~-30%.
- `DS4_QWEN4_MTP_PROFILE` at 80k: shallow verify **drift 24.84 ms** vs
  **exact 38.64 ms** (+55%) with near-equal tails — cycle TIME, not
  acceptance; the short-ctx battery (ctx<600) was structurally blind to this:
  the gap at shallow ctx was the known ~1.8 ms, so the fleet looked green.

## Root cause

`qwen4_graph_attention_core`, chunk-wide-indexer branch (ds4.c:58649 pre-fix):
the per-row sparse decode loop passed `r == 0u ? g->attn_part : NULL`.
Row 0 got the split-ladder parallelism; rows 1+ got NO ladder — one threadgroup
serially gathering the ~2052 scattered selected keys per layer. At short ctx the
gather table is tiny and the difference vanished (battery-invisible); at deep
ctx the scattered gather + missing parallelism costs ~+14 ms per verify cycle
(13 attention layers). The NULL was ROOT-CAUSE-2-era conservatism about row 1's
"larger key universe", but the v2 world had long proven every row's OWN ladder
is serial-identical (rcab maxabs=0 with laddered per-row core calls), and the
`g->attn_part` allocation is already 3 rows (ds4.c:57973) — the per-row region
was simply never wired.

## Fix (this commit)

Per-row view into row r's ladder region (`ds4_gpu_tensor_view(attn_part,
r*region, region)`, r<3; r>=3 keeps the old NULL serial fallback; one-time
stderr if the view unexpectedly fails). Row 0's bytes identical to before.
The dense-row loop in the same function already passed the whole part per row —
this only extends the established pattern to sparse rows 1-2. Drift world never
enters this branch (attn_per_row && idx_batch gate). Independent review: no
blockers/majors; invariants (bit-exactness, bounds, &&-chain semantics, 3-row
cap, drift isolation) confirmed line-by-line.

## Gates (all green, post-fix binary)

- rowcount-ab 150 pairs (03/06/07 x {T2,T3} x 25) maxabs=0.0 + spot 4 after
  the review polish; verify-identity 6 runs x 3 grids flips=0.
- `./ds4_test` 26 OK / 0 ERR default AND 26 OK ST-pinned; kernel harness green.
- battery x2 + production batched session texts byte-identical to the banked
  ST+GROUP/rowe/serial-ref streams. Streams UNMOVED — no golden decision.
- Deep probes after fix: **61.8 @102k / 54.5-58 @205k / 55.5 @320k** — at or
  above the drift baseline in the operator's remembered 50-60 band.

## Standing lessons (add to any future deep-ctx work)

1. The 10-prompt/400-token battery CANNOT validate deep decode — any change
   touching sparse-verify geometry must include a ctx_curve probe (>=100k).
2. When two engines disagree on t/s, check concurrency: two batched slots each
   halve the other's chunk rate (that confound bit this investigation twice).
3. KV-disk replay: reusing a probe nonce re-serves the session from the disk
   store near-instantly — fresh nonce for cold timing, fixed nonce only for
   text-identity comparisons (decA/decB reuse produced a spurious "instant" run).
4. `MTP_PROFILE` verify numbers at deep ctx are the cheapest cycle-time oracle;
   acceptance confusion (tokens/cycle) resolves in minutes with trace+profile.
