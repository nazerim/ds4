# ADR: KV Cache v2 — retire-grace, divergence anchors, 128 GiB budget

## Status
Accepted (implemented, PLAN-KV-REWRITE.md; committed with phases 1-5 + regression).

## Context
A 743-request trace (Aug 7) showed the dominant KV-cache rebuild cost was cross-session retirement, not the anchor-hole: at session switches, a just-live conversation's whole ladder (incl. the frontier persisted seconds earlier) was `conversation-retired` because LRU recency ranked by file touches (max last_used among exclusive members) rather than slot activity. The 23420-token divergence class (subagent tool-list re-render) also rebuilt from 16384/24576/32768 because no anchor sat between the deepest surviving head anchor and the divergence point. Disk sat at 63/64 GiB so every store ran an eviction pass.

## Decision
1. **Retire-grace (frontier pinning):** `--kv-cache-retire-grace-seconds` (default 3600). A lineage whose leaf was touched within grace is exempt from PHASE C retirement and over-cap retirement; PHASE B halving is grace-agnostic (non-destructive). Stops switch-back deep rebuilds.
2. **Divergence anchors:** on a miss loading anchor A < common, set a per-slot pending target; once the live session reaches it, store a reason=cold anchor at exactly common (grid-aligned targets skipped — covered by continued store). `--kv-cache-max-divergence-anchors` (default 8, 0=off).
3. **Budget:** KV_SIZE default 65536 -> 131072 MiB so two ultra-long conversations coexist.

## Consequences
- Session switch-back should hit the prior frontier (<100-token prefill, 16:54-style 287166-token hits).
- Genuinely idle lineages (>grace) are still retired under pressure; halving preferred over retiring.
- Divergence-class misses rebuild from `common` instead of the older anchor.
- New knobs are config-overridable; tests: 3 new unit tests in `--server`, `tests/kv_policy_harness` in `make test`.
- Live model-backed verification remains a follow-up server run (phase 6).

## Supersedes
- The LRU-retire ranking in PLAN-KV-LINEAGE (unchanged except grace).
- The 65536 MiB budget default from the Aug 6 fix.
