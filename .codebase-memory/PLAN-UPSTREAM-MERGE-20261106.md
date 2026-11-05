# PLAN — Upstream merge-debt digestion (20261106)

Trigger: operator ask 00:32 — "keep lanes clear on patches, allow for
merge debt with upstream". Audit below found + fixed lane gaps; this doc
is the executable plan. **Nothing here runs without an operator go.**

## 1. Lane audit (measured 20261106 00:4x)
| branch | state | action taken |
|---|---|---|
| main | `2d7e8dc..` == nazerim/main; ahead 458 / **behind 234** of origin | none - synced |
| feat/kvstore-tokenizer-fingerprint | worktree `ds4-pr1020`, clean tree, synced; **already merges origin/main incrementally** (5915b7b) | precedent, keep cadence |
| feat/kvstore-reload-loop-guard | synced | none |
| pr1149 (2c) pr1179 (17c) pr1179-oct11 (18c) | **LOCAL-ONLY, 37 commits zero remote copies** (Qwen/Metal experiments) | **pushed to nazerim tonight** |
| fix/unterminated-tool-envelope | on nazerim, 1 commit never merged to main | decide upstream-PR vs retire |
| int8-moe-prefill | on nazerim; lever REJECTED (PLAN-INT8-MOE closed) | archive |
| pr1149-ab, pr1238 | fully contained in main | deletable (keep remote, harmless) |
| worktree ds4-head (detached, stale) | prunable | pruned tonight |

**Doctrine rule this enforces (was violated silently): every lane pushes
its branch before idle; unpushed work = lost work risk.** 45 unpushed
commits existed across lanes at audit time including tonight's 37 — the
headline answer to "are all commits up to date" is now yes; it wasn't
20 minutes ago.

## 2. Merge shape (measured, not guessed)
- merge-base `b5aedb9` (early Sept). Upstream pace since: Sep 100, Oct
  129, Nov 26 → **~110 commits/month; this debt regenerates every 4-6
  weeks**. Weekly cadence is the only stable endgame.
- 113 upstream files changed, 502 fork files changed, 52 overlap.
- **Full big-bang probe (`git merge-tree main origin/main`, read-only):
  exactly 7 CONFLICTING files** — `.gitignore`, `ds4.c`, `ds4_server.c`,
  `ds4_metal.m`, `metal/qwen4.metal`, `tests/test_metal_command_memory.c`,
  `tests/test_qwen4_kernels.c`. Git auto-merges the other 45 overlaps.
- Collision weight: ds4.c (63 fork vs 150 up commits), ds4_server.c
  (83 vs 10), ds4_metal.m (27/27), Makefile (21/33 auto-merges clean).
- Note: `test_metal_command_memory.c` conflicting means upstream
  absorbed *our Qwen lane's own PR work* (pr1179-oct11 head commit is
  "Block on Metal command buffers...") → supersede sweep below will
  retire several fork-side branches rather than re-applying them.

## 3. Strategy — 3 batched merges in an isolated worktree
No big-bang: 7 conflict files sounds fine until ds4.c's 150-vs-63
hunks land in one review sitting. Batching by upstream month bounds
each conflict-set and keeps bisection possible.

- **Setup**: `git worktree add /Users/naz/Projects/ds4-merge -b
  merge/upstream main`. Live engine (49807, day-16 collecting) and main
  untouched throughout. AGENTS.md: real merges, never force-push.
- **Batch A** = origin/main@end-Sep (~100c), **B** = end-Oct (~129c),
  **C** = head (~26c).
- **Batch 0 (first, ~1h, zero risk): supersede sweep.** For each
  fork-only branch (pr1149*/pr1179*/int8-moe/fix-unterminated) and for
  the top fork commits in main touching the 7 conflict files: check
  whether upstream since base already carries the equivalent
  (`git log $base..origin/main --grep`, file-level diff probes vs merged
  result). Mark branches SUPERSEDED-archived vs KEEP-unique. This tells
  us which conflicts resolve "take upstream" and protects fork-only
  semantics.
- **Per-batch gates, in order:**
  1. tag batch base (rollback anchor `merge-pre-A/B/C`)
  2. `git merge --no-ff` milestone; resolve trivial (.gitignore) then
     core with the **fork-preservation checklist** per hunk in ds4.c /
     ds4_server.c: KV-cache-v2 lineage hooks; DSpark head paths +
     stats-flush-at-free behavior; DSML rewind-resume + fast-path;
     capture env `:-` defaults (train dumps must stay redirected-safe);
     session-sync reset semantics; tool_choice parsing; 524288 KV budget
     plumbing.
  3. `make` (all 5 binaries) in worktree.
  4. test tiering: **coexist-tier** while engine runs: `--server` +
     `--mtp-slice` subsets, rewind pin test; **stop-tier** (process
     lock — engine must be stopped, operator-window item): bare
     `./ds4_test` full suite + `make dspark-acceptance`. Fold stop-tier
     once per batch (~45 min) or after B if B's conflicts are clean in
     A.
  5. artifact convention re-check (mandate after every merge):
     `git ls-files | grep -E '\.o\.tmp$|^tests/(kv_policy_harness|test_prompt_prefix|test_spec_rejection)$'`
     must print nothing; `git check-ignore tests/kv_policy_harness`
     must match (regression precedent: 7d7b8cc resurrected artifacts).
  6. push `merge/upstream` to nazerim + append batch note here (state +
     conflict resolutions + gate results).
- **Final landmine step (operator window, combine with day-16 seal):**
  full stop-tier green on C → seal day-16 (engine already down) → merge
  `merge/upstream` into main → boot from fresh binary via snapshot
  recipe + 4-point verification → sentinel re-arm → first real tool
  traffic confirms rewind field behavior (0 rebuilds expectation).
- **Rollback:** `git revert -m 1` per batch on main only if landed;
  pre-merge worktree needs nothing. Old binary rebuildable in minutes;
  blade keeps corpus, not binaries.

## 4. Steady state after digestion
- Weekly: fetch + `merge-tree` probe (30 s); upstream delta > ~30
  commits on overlap files → mini-merge batch that week. Small
  conflicts forever beats 234-commit storms.
- Lane discipline: branch-per-lane, pushed before idle (audit command:
  `git log --oneline --branches --not --remotes` must be empty);
  lanes rebase within a day of any main move.
- `fix/unterminated-tool-envelope`: check upstream appetite (115ec43)
  before it ages out of relevance.

## 5. Effort & windows
Batch 0 sweep ~1h · A 2-4h · B 2-4h · C ~1h · final+seal 1-2h.
Needs from operator: 2-3 × ~45 min engine-stop windows (batch stop-tier
checks) + the final combined window. Recommend starting batch 0 + A
during the next day you want autonomous engine work anyway; merges
never touch main until C is green.
