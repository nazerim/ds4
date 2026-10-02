# oMLX cache-policy fixes (#3908 / a28e5a87) ↔ ds4 V2 disk-KV — mapping verdict

Lane 1 of `.codebase-memory/omlx-v070-final-perf.md` §4 action #1. Read-only
(2026-10-02, engine RUNNING — no builds/tests/writes outside this file).
Sources: omlx-glimmer `cb157991` (#3908), `a28e5a87`, `omlx/cache/prefix_cache.py`
(5342 lines, byte-identical at v0.7.0), `paged_ssd_cache.py`,
`boundary_snapshot_store.py`; ds4 `ds4_kvstore.c/h`, `ds4.c` payload writers,
FLOOR-20261001.md, MTP-20261001.md.

## 1. What oMLX actually fixed (mechanism, with evidence)

- **The tail.** Each store persists the trailing partial block ("tail") carrying
  every NON-sliceable layer state — GDN recurrent matrix + conv window, full
  sequence, O(1) in context but ONE PER TURN (prefix_cache.py:870-871,
  :1136-1140, :1508-1516). Split mode moves the recurrent state to a per-node
  **GDN sidecar** keyed by the tail's chain hash (:1355-1364, :2026).
- **The pile-up (pre-a28e5a87).** Reclamation was gated on
  `any(is_rotating_family(...))` — tip-lineage was NEVER recorded for
  GDN/QSA/GLM-5-linear/DSv4.1 layouts ⇒ unbounded full-state tails per turn,
  hot tier + SSD + sidecars (a28^:prefix_cache.py:1470-1472).
- **The fix.** `_tip_lineage` tracks tails on every layout; on the second
  extend the two-generations-back tip is deleted (`_discard_tail_block`,
  :1562-1593) WITH its sidecar (`forget_gdn_checkpoint`,
  paged_ssd_cache.py:2641-2675); the previous turn's tail is kept intact as the
  edited-turn/re-branch fallback. Steady state: **two heavy blocks per chain**
  (:1551). Guard: lineage only chains from real stored tips (:1578-1581).
- **#3908 exact-prefix sidecar.** SpecPrefill's `store_exact_prefix` (arbitrary,
  non-grid boundary, restored only on exact token match) previously REFUSED
  split-GDN layouts; it now commits a whole-layernet recurrent snapshot
  (`_commit_exact_split_gdn_checkpoint`, :461-510) for the TERMINAL block only,
  safetensors `layer_{i}_state_{k}` promoted atomically to
  `_gdn_sidecars/<sig>/<block-hash>.safetensors`, keyed by chain hash
  (boundary_snapshot_store.py:1211-1350; paged_ssd_cache.py:2184-2198,
  2449-2536). Domain isolation: separate block-key namespace, all-or-nothing
  fetch, fails closed (:137, :1158-1160, :1661-1670, :1724-1773). Scheduler
  rewalks one block back before reconstruction (N-1 safety,
  tests/test_prefix_cache_gdn_split.py:700-745).

## 2. MAPPING VERDICT for ds4 V2

**The unbounded-retention half: ds4 avoids it by design.** Proof lines:
1. Entry sha = hash of rendered text prefix (ds4_kvstore.h:62-66) — re-store at
   the same frontier rewrites the trailer only, cannot pile up
   (ds4_kvstore.c:2594-2603).
2. Keep-set per lineage = frontier + `tail_anchors` (2) + window maxima
   (kv_cache_chain_kept, ds4_kvstore.c:1011-1053) ⇒ steady state *frontier + 2
   heavy rungs* — structurally identical to oMLX's post-fix "two per chain".
3. Text sessions write NO per-turn disk tail at all — `reason="turn"` stores
   exist only on the multimodal path (ds4_server.c:16400-16412). Disk volume =
   grid rungs (**8192, doubled to 16384 past 49152 live tokens, boundary-aligned
   2048** — ds4_kvstore.c:1975-2070) + off-grid frontier stores.
   **Correction to the rc1-era phrasing: "quantum 512" is `min_tokens` + the
   mixed-prefill scheduling quantum (ds4_kvstore.c:36, ds4-server.sh:95,
   ds4_server.c:13994-14008) — NOT the rung grid.**

**The redundant-snapshot half: two residues.**
- (R1, closed in format, live in files) V1 payloads re-copied the whole MTP
  block per delta link ≈ 2.625 KiB × session rows (776 MiB @295k rows;
  MTP-20261001.md:50-55) — fixed by `87e11f3`, live dir rebuilt pure-V2
  2026-10-02. BUT: V1-era envelopes are **exempt from the redundancy sweep**
  ("legacy singleton, always kept", ds4_kvstore.c:648-652, :964) and exit only
  via PHASE C legacy-LRU (:1700-1717) — any surviving V1 file elsewhere keeps
  its duplicate bytes until LRU reaches it.
- (R2, structural) Every rung carries the full fixed GDN `[state][hist]` + PLE
  hist at offset 0 independent of `rows_from` (ds4.c:62886-62893, :62928-62939)
  = **140.3 MiB floor/store** (FLOOR:12-19; 45 linear × 3.117 MiB). Unlike
  omlx, this is NOT dead-by-policy: any rung can be a resume endpoint
  (`find_text_prefix` lands on keep-set members), so the floor is load-bearing
  for the two retained frontier-adjacent rungs — but it is pure dead weight in
  rungs that only ever serve as intermediate chain links. Quantified waste:
  (a) **superseded off-grid frontier stores** (~150 MiB V2 each at 295k rows;
  evict/shutdown/cold spans) linger until `total > target`
  (ds4_kvstore.c:1615, :1648) — omlx prunes **eagerly at store time**
  (prefix_cache.py:1582-1587); (b) chain-walk resume re-reads every link's
  140.3 MiB although only the last link's state survives the sequential load
  (ds4_kvstore.c:3229-3258 + ds4.c:63026-63031) = **(D−1)×140.3 MiB redundant
  READ bytes per depth-D resume** (disk→GPU, load latency, not capacity).

## 3. Actionable items — #1 SHIPPED 2026-10-03 (`460ab6b`)

1. **Scenario L — eager supersede of off-grid frontier rungs** — IMPLEMENTED
   as `ds4_kvstore_sweep_superseded_frontiers` (ds4_kvstore.c, exported in
   ds4_kvstore.h), hooked in `ds4_kvstore_evict` right after
   `kv_cache_refresh` — one hook covers open (:1844 calls evict), every store
   (store_live_prefix_text calls evict internally :2695 with the incoming
   text as active-chain protection), and server-initiated evicts. RED
   verified pre-fix (4 harness failures), GREEN post-fix; full harness +
   `--mtp-slice` pass. Design refinements made during implementation:
   - **Candidates = reason EVICT/SHUTDOWN only.** The lane-1 sketch included
     COLD; implementation excludes it because divergence anchors are stored
     as reason=cold (branch-reuse contract, max_divergence_anchors) —
     eagerly dropping a fired anchor would force full re-prefill of the
     diverged branch it exists to serve. Continued rungs excluded (reuse
     ladder). Legacy v1 excluded (legacy-LRU path owns them).
   - **children==0 is structurally limiting, and correctly so.** On a V3
     delta chain every superseded snapshot is the next rung's parent
     (rows start at its frontier), so mid-chain snapshots are protected by
     construction — the deferral/retire machinery owns them. The sweep
     fires on FULL-store snapshots: weight-swap epochs (H-1 model_fp guard
     forces full writes), delta-staging fallbacks (:2622-2649), and diverged
     branch chains of snapshots. Each hit frees a ~150 MiB GDN-floor file
     that the 128 GiB-default budget (ds4-server.sh:23) would otherwise
     never reclaim.
   - **"Newer" = strict text-prefix extension among candidates** (pairwise,
     no rel matrix — candidate counts stay small; text fetched pairwise per
     the two-slot cache protection contract at ds4_kvstore.c:790-794).
     keep = max(1, tail_anchors) per lineage; diverged branch tips prefix
     nothing and keep their own window.
   - **One-store lag**: the pre-store sweep sees state before the new
     snapshot commits, so steady state is keep+1 snapshots alive between
     stores; converges on the next evict/open. oMLX prunes AT store time;
     the lag is the price of hooking the existing evict choke point instead
     of threading a post-commit callback.
   - Harness: `scenario_frontier_supersede` pins drop-at-open-under-zero-
     pressure, window advance on the next snapshot, continued-rung and
     zero-deferral invariants, and exactly-one-unlink log discipline
     (`reason=frontier-superseded`).
2. **(R2b) Resume-read trim** — chain-walk loads state from every link. Cheap
   fix candidate: skip payload read for non-terminal links whose state is
   overwritten downstream. CAVEAT: only valid when each link's stored state is
   provably recomputable from later links (V3 delta assumption) — needs a read
   pass over `ds4_kvstore_load` chain semantics + tokfp gating before scoping;
   also a disk-READ (latency) win, not a capacity win. NOT STARTED.
3. **(R1) V1 sweep exemption** — decide: keep legacy singletons out of the
   redundancy sweep (status quo, exits via legacy-LRU) or add a
   `quant_bits_loadable`-style write-only gate. Likely fine as-is; the live dir
   is pure V2. NOT STARTED.

## Open questions
- How many V1-tagged files survive in non-rebuilt dirs (e.g. DeepSeek-era
  /tmp/ds4-kv) — not enumerated this pass.
- Post-`87e11f3` batched sessions: `mtp_pos` never advances for batched
  sessions (MTP-20261001 known limits) — how much of the theoretical slice
  gain actually lands on the blade needs a field byte-count once the dir has
  aged (read-only tally, no engine action).
- omlx sidecar namespace stamping across model swaps (`gdn_cache_signature_for`
  layout stamp) — possible mis-namespace hazard, not verified.
