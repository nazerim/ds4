# Evidence: prefill vs decode contention (Qwen lane)

Source: `log/ds4-qwen.log.20261117-094911` (Nov 15 07:43 -> Nov 17 09:49, ~11 MB,
the traffic that ran under `--batched-session 2 --mixed-prefill-quantum 512`).
Collected 2026-11-17 to decide whether a serialize-prefill policy is worth building.

## CORRECTION (2026-11-17, same session)

An earlier version of this file concluded the collapse was "a tail, and only
modestly correlated", based on **mean decode rate bucketed by whole minutes**
(50.0 vs 69.9 t/s, -28%). That reading was WRONG and the operator's correction
is right: mean-over-minute dilutes a stall that lasts seconds inside an otherwise
healthy minute. Re-measured against **long prefills specifically**:

- Minutes containing a prefill progress event lasting >= 5 s: **119**.
- Decode samples in those minutes: n=4,808, mean 63.0 t/s, **92 samples < 10 t/s**.
- Decode otherwise: n=41,728, mean 68.2 t/s.
- **92 of the 101 sub-10 t/s samples in the entire log live in those 10% of
  samples -> ~9x enrichment. The dips are caused by long-context prefills.**

Largest observed: a **233,941-token** prompt prefilling, e.g.
`prefill chunk 98304/233941 ... avg=1311 t/s 74.960s`.

Also wrong in the earlier version: I sampled one window (1116 07:43-07:47) where
the prefills happened to be tiny (96 and 203 tokens) and generalized from it to
"the tail is not caused by long rebuilds". Single-window anecdote; discarded.
That window did show `slot 0: request reuses nothing; evicting checkpoint
(96387 tokens, idle 0 s, protected)`, which remains a lead worth checking, but it
is not the explanation for the population of dips.

Operator domain knowledge to carry forward: **concurrent decode is MORE efficient
than a single decode stream** on this engine, so the collapse is not "two streams
sharing the GPU" - it is specifically interleaving a long prefill with a decoder.

## Established facts (still valid)

1. Decode chunk-rate histogram (n=46,536):
   `<5: 69 | 5-10: 32 | 10-20: 65 | 20-40: 5,596 | 40-60: 21,437 | 60+: 19,337`

2. `dspark req: cycles=0 proposed=0 accepted_draft=0` on some of those turns -
   speculation contributed nothing there, so those are plain decode rates.

3. The open arithmetic gap: at quantum=512 and ~1311 t/s, one chunk is ~0.39 s,
   which cannot alone produce a 1.7 t/s sample (a 50-token chunk would imply ~29 s
   of blocking). Something costs far more than the headline prefill t/s - per-chunk
   cost at depth, repeated chunks without yielding, a checkpoint store, or a rebuild.

## Implication for the design (read this before proposing serialize-prefill)

- A serialize/block policy targets the **long cold-rebuild** case. That case exists
  (ladder rebuilds of 100k+ tokens) but is NOT what produced the observed tail.
- Serializing **tiny** syncs (96-203 tokens) buys nothing and adds head-of-line
  latency for the decoding slot.
- Therefore a blanket "block on the slot" flag is likely the wrong instrument.
  Prefer, and check in the design:
  (a) a **minimum-slice gate** - only interleave when the remaining prefill exceeds
      some tokens, so small syncs complete without yielding at all;
  (b) a **max-block-time / fairness cap** on a serialized run so a 300k rebuild
      cannot stall decode indefinitely;
  (c) whether the real cost is **not** the executor handoff but the checkpoint
      **evict + cold rebuild churn** around those small syncs (see fact 4) - if so,
      scheduling changes will not fix the tail.
- Note the config already changed under us: `QWEN_MIXED_QUANTUM` is now **2048**
  (commit 676eca7), and on Qwen `idle_prefill_quantum` is unset (V4.1-only), so
  mixed == idle == 2048. This evidence is all from the 512 era; re-measure the
  tail before building anything on top of it.

## Not established / abandoned
- A prefill-frequency cross-tab of slow minutes was computed with `join` and
  returned a malformed 48,787 rows from 98x800 keys; discarded, not used above.
- No post-2048 data exists yet: the engine restarted 1117 09:49 and has served
  no traffic (`prefill events since restart = 0`).
