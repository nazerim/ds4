# PLE decode-read finding — 2026-10-23 (RESOLVED: dead end)

Question: is the Qwen3.8-Flash-Next Q4 decode-side n-gram `pread` a meaningful,
attackable cost (justifying a concurrent reader, porting `ds41_engram_prefetch`)?

Instrument: `DS4_QWEN4_PLE_TRACE` decode-phase timer around `qwen4_ngram_read()`
(patch: `ple-trace.patch`). Opt-in, tail-call when unset (no behavior change).

## Result — PLE read ≈ 1.4–1.5% of decode wall-clock, context-independent
| run | ctx | pread delta | decode_s | share |
|-----|-----|-------------|----------|-------|
| high rep1 | 58k | 0.1207s | 8.38s | 1.44% |
| high rep2 | 58k | 0.1178s | 7.92s | 1.49% |
| short | 2.5k | 0.1177s | 7.84s | 1.50% |

- avg **393 µs/read**, rows/call **31.9 CONSTANT** across regimes → the read is
  per-token (≈ DS4_N_PLE_HEADS rows), NOT proportional to context.
- reads already internally parallelized: `dispatch_apply`, up to 16 readers.
- No I/O tail: 0 reads >5 ms, 4/1200 >1 ms, max 2.83 ms. Table is warm; blade fast.
- Trace did NOT perturb throughput: high-ctx 61–64 t/s ≈ pre-trace baseline 64.

## Why this closes both PLE levers
1. **Prefetch ceiling ~1.5%** at best, realistically ~0.5–0.8% (read feeds the
   forward; overlap hides part, not all). Not worth a second code path.
2. **Does NOT explain the short→high 3 t/s gap** (the thing we were chasing):
   PLE read is constant per token; the gap grows with context. So the gap is
   attention/KV + prefill-quantum, not disk. Confirms PLAN-PLE-PRELOAD's own
   Engram lesson ("concurrency won, caching didn't / wrong idea") — here even
   concurrency has almost nothing left to win.

## Related, ruled out earlier (same session)
- PLAN-DECODE-HOTLIST (streaming-expert hotness) N/A: Q4 Qwen is **resident**, no
  `--ssd-streaming`; `metal_graph_streaming_expert_hotlist_enabled` never true.
- Host decode-loop hygiene (getenv etc.) already hoisted (TOKENIZER-AUDIT fix #5).

## If we want a real Q4 decode lever next
Profile says look at what *scales with context*: (a) the MTP verify acceptance /
draft path (PLAN-DSPARK-*), (b) attention/KV cost growth. PLE is off the table.

## Restore
Source reverted to `9acedbc` (patch archived). Production restored: instrumented
binary replaced with pristine build, :8002 relaunched without the trace env.
