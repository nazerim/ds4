# Deep miss trace — 2026-10-09, re-validated 2026-10-22 (R2b/Scenario L)

## Original finding (2026-10-09, captured with --trace)

Deep miss: request 24 @ 2026-10-09 12:23:30 — prompt 412516, live 0 (empty
slot), disk-text served 65536 (chain=7, 736 ms, file 20b908f0...), fresh
prefill 346980.

Root cause: **client history clearing, not compaction, not a server bug.**
Byte-diff of rendered prompts, request 23 (warm, prompt 441001) vs request 24
(prompt 412516): divergence at byte offset 280715, exactly at a tool-result
boundary — `Edit applied successfully.` in req 23 vs
`[Old tool result content cleared]` in req 24. opencode cleared one more old
tool result mid-turn; the prompt shrank 441001 -> 412516 and the replay
diverges from the live session's tokens at that message (~65536 tokens in).

Server behavior was correct at the time: live probe no-reuse (shorter prompt,
common != prompt_len -> neither exact nor rewind tier), disk served exactly the
longest valid prefix, the 347k re-prefill is inherent to a mutated replay.

## Re-validation 2026-10-22 (codebase moved since)

- `eb73658` (2026-10-21) rewrote the kvstore lineage side (persistent
  sha-keyed rel matrix, streamed verified-once text refs — after a 21-min
  production store stall at 100% CPU). The disk "longest-prefix serve"
  behavior referenced above now lives on the rewritten machinery; this file
  documents the CLASS, pre-rewrite numbers are historical.
- Misses still occur (15 `live kv cache miss` in the current log; today
  12:19-12:32). Pattern matches the same class: deep mid-conversation
  divergence on extension-sized prompts — live=59427 prompt=59557
  common=16940, live=61198 prompt=61221 common=13646 — client history
  mutated well before the end of the replay. Not tool-turn canonicalization.
- New routing cost observed: 12:26:34 a 40270-token alien request evicted a
  fresh slot-1 checkpoint of 388425 tokens (idle 1 s, protected). Victim
  selection under batched-session 2 when both checkpoints are protected —
  worth a separate look if it recurs.
- tool_replay stats (2026-10-09 window): mem=20-33 disk=158 canonical=225
  missing_ids=225 — replayed tool-call ids not found in the tool memory,
  requests stayed warm via the canonical fallback (client passes raw args).
  Secondary observation, not a miss cause; 2026-10-22 requests show
  missing_ids=0.

## Still open from the original workstream

- The 2026-10-08 18:16:02 post-tool-turn reuse miss was NEVER captured with
  trace (different class from the clearing misses; ~1 per 45 min then).
  Synthetic reproduction failed across 15+ client variants
  (tests/spec_economics/toolturn_reuse_probe.py) — a well-behaved client
  never misses; tool-call text memory attach verified working (deliberately
  wrong arguments still reuse warm).

## Tools

- tests/spec_economics/toolturn_reuse_probe.py — scripted tool-turn reuse
  probe (chained cycles; variants stream/prose/badargs/special/code/trim/
  addnl/nullcontent/think/mresult/reser/nocontent/edit bigargs).
- tests/spec_economics/trace_miss_diff.py — --trace analyzer: per-request
  cache-decision table + first live-miss rendered-prompt byte-diff.
  (Note: loads the whole trace; on >100 MB traces use byte-offset extraction
  via grep -b + dd instead.)
