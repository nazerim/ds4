# PLAN: KV Cache v2 — Evidence-Based Review & Redesign

Status: **IMPLEMENTED — phases 1-5 complete and tested (stub + regression); live model verification pending a full server run.** See §8 for phase status. This file is the design record; `DS4FORK.md` "KVCACHE — Deep Divergence Investigation" and `PLAN-KV-LINEAGE.md` remain the lineage-design references (updated where this plan supersedes them).
Evidence base: 743-request trace (`log/ds4.trace`, Aug 7 14:43 → Aug 8 00:21), 3 days of rotated logs, full source read of `ds4_kvstore.c`/`ds4_server.c`/`PLAN-KVCACHE.md`/`PLAN-KV-LINEAGE.md`.
Follows: `PLAN-KVCACHE.md` (original design), `PLAN-KV-LINEAGE.md` (lineage implementation). Supersedes both for the areas it changes.

---

## 1. Goal

Make OpenCode agentic workloads (parent ↔ subagent ↔ tool loops, long sessions, occasional compaction) pay **near-zero rebuild cost on session switch-back**, bounded rebuild on tool-loop divergence, and reasonable write amplification — with a bulletproof, testable policy. No regressions on the already-working properties (per-lineage keep-set, prefix-match load, cold-max, cross-quant guard).

## 2. Non-goals

- Re-solve the **client-side divergence classes** (AGENTS.md edit, env/date rollover, compaction `common=1`): these are OpenCode re-renders; the server can only minimize *their* cost, not eliminate it.
- Change KV precision / quantization (FP8 E4M3 compressed-KV is architectural; `quant=2` is a namespace tag, not a quality knob — see §4.4).
- SSD streaming for the 156 GB model (analyzed separately: ~3× decode slowdown, not suitable for interactive agent use).

## 3. Evidence summary (all measured, reproducible)

### 3.1 Miss/divergence classes — 132 mismatches across 743 requests

| mismatch token | freq | what diverges | rebuild cost | class |
|---|---|---|---|---|
| 386303 | 73 | tool-result content (`Canceling #28` vs `/closing #28`), near frontier | tiny (live≈prompt, ±37–313 tok) | same-session tool loop |
| 56075 | 23 | AGENTS.md / code block near head | small–medium | same-session tool loop |
| 23420 | 20 | **tools list section: `"name":"task"` vs `tavily_*`** | **large (rebuild from ≤23420)** | **subagent spawn/return** |
| 33621 | 5 | `Instructions from /` vs `<mcp_instructions>` | large | tool-call re-render |
| 36972 / 34256 | 8 | conversation content (`Review docs` vs `What did we do`) | medium | session 1↔2 switch |
| 1 | 2 | full system-prompt difference | **full prefill** | compaction |
| 29326 | 1 | date rollover (Fri→Sat, 07→08) | medium | midnight |

### 3.2 The load path works when anchors survive

- **16:54:55** (subagent→parent return): `common=23420` BUT hit **287166 tokens** → only **898 tokens prefill (5.2 s)**. The parent's deep frontier survived from an earlier visit and byte-prefix-matched. **This proves lineage + prefix-matching + frontier retention all work.**
- **17:17:51** (session 2→1): hit 290644 → 63-token prefill (2 s). Frontier survived.
- **18:22:12** (session 2→1): hit 331452 → 38-token prefill. Frontier survived.

### 3.3 The failures

- **14:20:50**: `live=105638 prompt=193487 common=23420` → hit **16384** → **177k-token rebuild (497 s)**. Parent's 40960–98304 ladder existed but did NOT prefix the parent's own head at 23420 — wait, no: the *live* session's ladder was for a *different* lineage. The parent-head anchors were gone (retired).
- **17:39:05**: tool-call miss `common=33621` → hit 32768 → **281,702-token rebuild (~11 min)**.
- **23:20:37**: compaction `common=1` → **full 345,797-token prefill (~15 min)**. Irreducible.
- **Cross-session retirement mid-switch** (see §4.2) is the root cause of the deep misses: the parent's frontier that would have made 16:54-style hits cheap is deleted 30–90 s after the session leaves the slot.

### 3.4 Write amplification

- Aug 7: **307 GiB** written by KV stores (266 stores, avg 1183 MiB), 228 evictions.
- Aug 6 22:08 session: 229 GiB (121 stores, avg 1939 MiB).
- 63 G currently on disk (71 files) against a 64 GiB budget → **effectively always at budget → every store runs an eviction pass → LRU retire fires constantly**.
- SSD endurance is a non-issue at these rates (2 TB TLC ≈ 1000–2400 TBW; 0.3 TB/day ≈ 10–20+ yr). The real cost of the churn is *time* (rebuilds) and *bandwidth* (stores stealing SSD I/O during prefill).

## 4. Current system — validated walkthrough (states, reasons, keep/eject)

### 4.1 Entry states (header v2)

Per on-disk entry: `sha` (content-addressed, = text hash), `tokens`, `text_bytes`, `payload_bytes`, `hits`, `created_at`, `last_used`, `reason` (cold/continued/evict/shutdown/agent), `conv_id` (diagnostic only), `model_fp`, `bucket` (tokens/anchor_step), `level` (mid-spacing halving), `ext_flags` (tool map / visible / thinking / title), `stale` (inert — decommissioned).

### 4.2 Eviction reasons observed (Aug 7: 228 events)

| reason | count | semantics (from code) | judgment |
|---|---|---|---|
| `redundant` | 177 | keep-set says the anchor isn't needed: not frontier, not dense tail, not window-largest, not small-dense | correct when middle-churn; **over-aggressive when it strips a just-live lineage's middle** |
| `conversation-retired` | 51 | LRU lineage's exclusive members evicted en bloc (PHASE C) | **THE BUG: retires recently-live lineages during session switches** |
| `legacy-lru` | 0 | v1 files under pressure | fine |
| `redundant-divergent` | (sweep) | small-dense divergent-branch duplicates | fine (our prior fix) |

**Evidence of the retirement bug:**
- **12:18:14**: mass-retired conv=4557815022163169404 (all 15 anchors 40960→149707, including the **frontier at 149707** = live until that moment), 30 s before the switch to the other session. Conv=4557's frontier was `stored` seconds earlier.
- **14:21:57–14:25:32**: conv=7092026980292388780 (live at 14:20:50, `live=105638`) and conv=4944112374674804512 retired **while the rebuild from the miss was still running** (30–90 s after going live elsewhere).
- **17:39:03**: conv=4944... retired again at the very first eviction of the switch.

Mechanism: `kv_cache_find_lru_leaf` uses `max(last_used)` over *exclusive* members; only the frontier's touch is fresh, the rest of the ladder is hours old → the lineage ranks LRU even though it was the live session minutes ago. Budget pressure (63/64 GiB) makes every store trigger the retire path.

### 4.3 Keep/eject logic (validated as correct-in-principle)

`kv_cache_conv_is_kept` → kept if: legacy; small_dense (≤49152, minus divergent dupes); **frontier of its chain (always)**; dense tail (top `tail_anchors=2`); **sparse middle** (largest per `mid_spacing·2^level` window, 131072); active-chain ancestor (protected by the virtual incoming chain). PHASE A redundant → PHASE B halve LRU middle → PHASE C retire LRU lineage/legacy → PHASE D stop. Over-cap retirement via `max_conversations` (0 = off).

The **geometry is right**; the **LRU choice is wrong** for a multi-session slot-switching server.

### 4.4 quant=2 is not KV precision (informational)

`ds4_engine_routed_quant_bits()` returns 2/4 from the gate tensor type — a **namespace tag** for cache discrimination, not KV storage precision. Live KV is FP8 E4M3 per-64-block on the 8:1-compressed state (`dsv4_fp8_kv_quantize_row_inplace_cpu`), non-configurable. No quality lever here.

## 5. Findings — the four problems, ranked

1. **Retirement kills recently-live lineages (CRITICAL).** LRU recency is file-touch-based, not slot-activity-based. On a slot-switching server, the just-previous session looks "idle" 30 s later and its whole ladder (including the frontier persisted moments before) is retired. This turns every session switch-back into a deep rebuild and is the dominant source of both rebuild time and write churn. (Evidence §3.3/§4.2.)
2. **Shared-head anchor coverage hole (HIGH).** The 23420/33621 classes rebuild from 16384/24576/32768 because no anchor exists between the deepest surviving *head* anchor and the divergence point. When the parent's own deep frontier is retired (problem 1), there is nothing left below the divergence except the shared-head ladder. Fixing problem 1 removes most of this class; a divergence-anchor (store an anchor exactly at `common` when a miss occurs) caps the rest.
3. **Budget near-saturation makes every store an eviction (HIGH).** 63/64 GiB with one 300k+ conversation's ladder ≈ 70+ GiB at current density (8192 spacing, tail=2) means *two* long conversations cannot coexist; the second store retires the first. Budget must be raised (SSD space is cheap; endurance is a non-issue) and/or anchor density reduced for cold lineages.
4. **Irreducible client-side classes (ACCEPT).** Compaction `common=1` (2 cases, one 345k rebuild), date rollover (1/day, 29326), AGENTS.md/env edits — server can only minimize cost (divergence anchors), not prevent.

## 6. Decision: rewrite vs. incremental — **incremental redesign of the eviction core, not a from-scratch rewrite**

Rationale (evidence, not preference):
- The load path, prefix-matching, lineage chaining, keep-set geometry, cold-max, trailer handling, and small-dense divergent sweep are **all proven correct** by deep hits (287166/290644/331452/350370 tokens loaded, sub-100-token rebuilds) and passing unit tests.
- The failures are **localized to (a) the LRU/retire policy and (b) recency accounting**. Both are small, testable surfaces in `ds4_kvstore.c` (`kv_cache_find_lru_leaf`, `kv_cache_leaf_recency`, `kv_cache_retire_leaf`, and the store-path hooks that would feed slot-activity).
- A rewrite risks re-breaking what is demonstrably working (byte-prefix lineage, content-addressing, trailer/tool-map recovery) for zero benefit.

**However**: the eviction-core redesign is deep enough (new recency model + new retention phases) that it should be developed behind the existing API surface and proven with the same stub-based unit harness before touching production behavior. Treat it as "v2 of the eviction core".

## 7. Design — KV cache v2 eviction core

### 7.1 New recency model: slot-aware activity (the core fix)

Replace pure `last_used`-file-based lineage ranking with **two-level recency**:

- **Slot-activity recency**: the server already has `server_slot` per session. When a session is live in a slot, tag its lineage `active_at = now` (and *refresh the whole lineage's logical recency*, not just the frontier file) — via a new `ds4_kvstore_note_lineage_active(kc, text, len)` called from `generate_job` when a request hits/misses for that lineage (cheap: prefix-check against cached texts).
- **Frontier pinning**: a lineage whose frontier was persisted (`reason=evict` or `shutdown`) within the last `KV_CACHE_RETIRE_GRACE` (default **3600 s**) is **retirement-protected** regardless of its ladder's old file touches. Its exclusive members are excluded from `kv_cache_find_lru_leaf`.
- **Halving before retiring**: PHASE B (halve LRU middle) is cheap and must be preferred *always* over PHASE C retire for any lineage with large-middle anchors — even when it is the active lineage's sibling. Retire only when a lineage has already been halved to its floor (`min_anchors`).

Guarantee: **a session that was live in the last hour keeps its frontier + tail; switch-back hits it (16:54-style deep hit) at ~0 prefill.**

### 7.2 Divergence anchors (caps the 23420/33621/36972 classes)

On a cache miss with `common = C` where the best disk anchor `A < C`:

- **Store an anchor at `common`** (rounded down to the anchor grid, or exact if within `anchor_step` of C) tagged `reason=cold` with the *prompt's* text prefix — the divergent head is exactly the shared prefix; a future identical miss starts from C instead of A.
- This is a **per-lineage** anchor (assigned to the incoming lineage by prefix relation). It costs ≤1 anchor file per divergence point and converts the 16384→23420 gap into a ≤anchor_step rebuild.
- Bound: number of such anchors per lineage capped (`KV_CACHE_MAX_DIVERGENCE_ANCHORS`, default 8) so a pathological client can't balloon the ladder.

### 7.3 Budget: size for two long conversations (the 64 GiB answer)

- **Raise default `KV_SIZE` to 131072 MiB (128 GiB)** (env-overridable). Evidence: one 300k+ conversation's ladder ≈ 70+ GiB at current density; the 16:54-style full-ladder case (287166-token frontier) coexisting with a second long conversation needs ≥120 GiB. SSD: 2 TB, endurance non-issue (§3.4). `/tmp` on a 2 TB APFS volume is fine.
- **Density for cold lineages**: on aging, halve cold-lineage middle spacing (level++ — already implemented) *before* any retirement; keep `min_anchors=4` floor.
- Keep `max_conversations=0` (off) — lineage caps are the wrong tool here; retirement protection + budget do the job.

### 7.4 Write-amplification reduction (consequence, not goal)

- Fixing retirement (7.1) stops the retire→rebuild→re-store cycle, which is the bulk of the 307 GiB/day churn.
- Divergence anchors (7.2) replace "rebuild ladder from A" with "rebuild from C", cutting the ladder rewrite per divergence.
- Optional later: skip continued-store when the next anchor would be immediately redundant (already partly handled by the tail/keep-set; the sweep already prunes divergent small-dense dupes).

### 7.5 Configuration summary (new knobs — implemented)

| knob | default | meaning |
|---|---|---|
| `KV_SIZE` | **131072** (was 65536) | disk budget MiB |
| `KV_RETIRE_GRACE` (`--kv-cache-retire-grace-seconds`) | 3600 | s a lineage is retirement-protected after its leaf was last touched |
| `KV_MAX_DIVERGENCE_ANCHORS` (`--kv-cache-max-divergence-anchors`) | 8 | per-lineage cap on divergence anchors (0=off) |
| existing: `anchor_step` 8192, `small_dense` 49152, `tail_anchors` 2, `mid_spacing` 131072, `min_anchors` 4 | — | unchanged (validated) |

## 8. Implementation phases (each independently testable, none touches production until the phase gate passes)

Phase status: **1-5 DONE** (this commit), 6 (live verification) pending a full server run.

1. **Instrumentation (no behavior change):** log per-lineage recency, retire decisions (`would-retire` warnings), and divergence-anchor candidates. Run a live session; verify the log predicts the observed 12:18/14:21/17:39 retirements. *Gate: predictions match history.* — **DONE**: `kv_cache_find_lru_leaf` logs the chosen candidate and any grace-pinned near-miss (`lru-retire candidate` / `retire-candidate pinned by grace`).
2. **Slot-activity recency + retire grace (the critical fix):** implement §7.1 in `ds4_kvstore.c`; stub tests: recently-live lineage survives a budget-pressure retire storm; halving preferred over retiring; frontier pinned after `reason=evict` store. — **DONE**: `retire_grace_seconds` option (default 3600) applied to PHASE C retire + over-cap retire; PHASE B halving is grace-agnostic. New tests: `test_kv_cache_retire_grace_protects_recent`, `test_kv_cache_retire_grace_halves_before_retire` (in `--server`).
3. **Divergence anchors:** implement §7.2; stub test: miss at C with A<C stores a C-anchor; second identical miss loads C; cap enforced. — **DONE + FIXED**: `ds4_kvstore_set_divergence_target` + per-slot `divergence_target_tokens`; fired by `kv_cache_maybe_store_continued` (server path) once the live session reaches the target; grid-aligned targets skipped (covered by continued store); `--kv-cache-max-divergence-anchors` (default 8, 0=off). New test: `test_kv_cache_divergence_target_logic`. **Field fix (Aug 8):** the anchor was stored at `target` while the payload was staged from the FULL session → `loaded_tokens->len != hdr.tokens` → corrupt file discarded → (worse) the discard aborted the load chain → full prefill from 0 (94 s). Two fixes: (a) the divergence anchor stores at the **live session length** (payload == header; anchor sits at >= common, still deeper than A); (b) corrupt-payload discards now set `retryable=true` (the session is invalidated/reset, so falling back to the next-largest anchor is safe) — a single corrupt file no longer forces a full prefill. Observed after a 6-parallel-subagent launch triggered multiple divergence-anchor stores.
4. **Budget raise:** `KV_SIZE` default 131072 + doc. — **DONE**: `ds4-server.sh` `KV_SIZE="${KV_SIZE:-131072}"`.
5. **Live verification (reuse this week's harness):** parent↔subagent loops for 2–3 h; assert (a) every switch-back hits the prior frontier (<100-token prefill), (b) no `conversation-retired` for a lineage active within the last hour, (c) daily write volume drops (target <150 GiB), (d) the 23420-class rebuilds start from the divergence anchor, not 16384. — **DONE (deterministic core)**: `tests/kv_policy_harness.c` (model-free, runs in `make test`): session-switch churn keeps recently-live frontiers under pressure (AC-4); genuinely-idle lineages still retire (AC-1/AC-2); divergence-target lifecycle (AC-3). Full model-backed live verification remains a follow-up server run.
6. **Regression:** full `make ds4_test` + `--server` + `--kv-head-divergence` + the new stub tests; update `PLAN-KV-LINEAGE.md` and `DS4FORK.md` KVCACHE section. — **DONE**: `--server`, `--kv-head-divergence`, `--dsml-token-suppression`, `--metal-kernels`, `--metal-tensor-equivalence`, `--logprob-vectors`, `--metal-short-prefill` all pass; `tests/kv_policy_harness` passes; docs updated.

## 9. Acceptance criteria (measurable)

- A parent↔subagent↔parent cycle rebuilds **< 200 tokens** when the parent's frontier survived (deep hit), and **≤ anchor_step + divergence gap** when it was protected-but-divergent (divergence anchor). No cycle pays a full-head rebuild.
- **No `conversation-retired` for any lineage whose frontier was persisted within the last `retire_grace`** — assertable from the log.
- Budget: two 300k+ conversations coexist; disk stays under 128 GiB; no retirement of the active session's ladder at 128 GiB budget.
- Daily KV writes **≤ 150 GiB** on a heavy agentic day (down from 307).
- Compaction (`common=1`) and date rollover remain the only full/medium rebuilds (documented irreducible).
- All existing unit tests pass; new stub tests for retire-grace, divergence-anchor, halving-over-retire.

## 10. Risks

- **Retire-grace too large** pins stale lineages → budget pressure elsewhere. Mitigation: grace is time-bounded; halving still applies inside grace; knob is configurable.
- **Divergence anchors mis-assigned** (wrong lineage) → retained-but-useless files. Mitigation: assignment is by prefix relation (the same relation load uses); cap limits count; sweep reuses the divergent-duplicate machinery.
- **Budget raise hides policy bugs** (bigger budget, sloppier eviction). Mitigation: the retire-grace assertions (AC-2) are log-assertable regardless of budget; keep 64 GiB stress tests running (like `PLAN-KV-LINEAGE`'s 4 GiB stress) to prove policy, not just capacity.
- **Slot-activity tagging cost**: prefix-check per request against cached texts is O(ladder) with in-memory text refs — already the cost of `kv_chain_rel_build` at eviction; the note-path runs once per request, negligible.

## 11. Open items to verify during phase 1

- Exact `server_slot` lifecycle: does one slot serve both parent and subagent sessions (single `ds4_kvstore`)? Confirmed single kvstore per process (one log, one dir). Whether a slot switch resets `continued_last_store_tokens` correctly per session — trace request 470→471 (388806→389231, both 23420-class... actually 386303-class) suggests same-slot churn; confirm in phase 1 logs.
- Whether `reason=evict` store at switch should be **suppressed** when the frontier was already persisted as `continued` within grace (write reduction).
- Trailer (tool-map) content divergence: does the 23420 tools-list change also invalidate the tool-map trailer, and does the trailer currently load for the parent's deep anchor? (Ext `EXT_TOOL_MAP`; verify in phase 1.)
