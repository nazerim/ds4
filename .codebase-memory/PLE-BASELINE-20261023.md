# PLE / Decode throughput BASELINE — 2026-10-23 (pre-change control)

Purpose: pinned control measured **before** any PLE-read instrumentation or
concurrent-reader code changes, so the post-change A/B has an exact target.
Drives the **live production server** over the OpenAI API (greedy → MTP path).
No code touched, no rebuild, no second instance (memory: single instance only;
host had ~1.9 GB swap used at capture).

## Serving config (live, unchanged)
`./ds4-server --model gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx 524288 --tokens 65536
 --host 127.0.0.1 --port 8002 --kv-disk-dir /Volumes/FireCuda520/ds4-kv-qwen
 --kv-disk-space-mb 524288 --kv-cache-small-dense 49152
 --kv-cache-retire-grace-seconds 3600 --kv-cache-max-divergence-anchors 8
 --vision gguf/mmproj-Qwen3.8-Flash-Next-Q8_0.gguf --batched-session 2 --mtp
 --mixed-prefill-quantum 512`

- Fork state: `main` @ `9acedbc` (PR #1238 already adopted+merged: `244519b`).
- Model: Qwen3.8-Flash-Next **Q4, resident weights + disk-only n-grams**
  (95.37 GiB n-gram table on the FireCuda520 blade; experts fully resident, NOT
  SSD-streamed — so the streaming-expert-hotlist path does not apply here).
- `temperature: 0` → greedy → exercises the `--mtp` batched spec path.
- `finish=length` every run (300-token budget fully consumed).
- THINKING mode active on the high-ctx runs (matches production agent load).

## Method (reproducible)
`POST /v1/completions {"model":"qwen3.8-flash-next","prompt":P,"max_tokens":300,
"temperature":0}` against :8002; read the server's own decode rate from
`log/ds4-qwen.log` (`… decoding chunk=<t/s> avg=<t/s>`). 1 warmup + 2 reps.
Prompt = slice of `speed-bench/promessi_sposi.txt` + "continue ≥400 words" tail.

## Results

| Regime | prompt tokens | ctx at decode | steady decode avg | reps |
|---|---|---|---|---|
| Short | ~2.5k | ~2.6k | **67.0 t/s** | 66.7 / 67.1 / 67.0 |
| High  | 58477 | ~58.7k | **64.0 t/s** | 64.5 / 63.8 / 63.8 |

- Chunk profile, short ctx: 71.8 → 67.3 → 61.6 t/s as decode advances.
- Chunk profile, high ctx: 59.9 → 63.6 → 64.0 t/s (settles ~64).
- Reproducibility: ±0.5% across reps at both regimes.
- Matches the #1238 adoption note (69.4 cold / ~65 steady) and the live agent
  log (45k ctx, avg 65.4 t/s).

## The signal to decompose (why this matters)
**short→high decode drop ≈ 3.0 t/s (~4.4%, 67.0 → 64.0)** as context grows to
~58k. Candidate contributors, in order of what the plan predicts:
1. **PLE n-gram `pread`** — serial `qwen4_ngram_row()` (ds4.c:58412) reads the
   disk-only 95 GB table per token/row; at high ctx the table pages are colder
   and the read is NOT overlapped in decode (existing `ds4_gpu_qwen4_prefetch_next`
   is `T==1`-only and prefetches down-proj weights, not n-gram rows; the batched
   multi-reader path `qwen4_ngram_part` is prefill-side). Under `--mtp` verify
   (multi-token) these serial preads stack up.
2. KV / attention cost growth with context (expected, not our target).

The prefill-side `--ssd-streaming` expert hotlist (PLAN-DECODE-HOTLIST) is
**irrelevant here** (Q4 resident). PLAN-PLE-PRELOAD's *caching* idea is also the
wrong lever — Engram precedent (PR #1072) measured 22.5% row reuse → "concurrency
won, caching didn't." The right lever is a **concurrent/prefetched n-gram reader
for the Qwen4 PLE path**, mirroring `ds41_engram_prefetch_*`.

## Next step (NOT yet done — this file is baseline-only)
Wire a PLE-read timer (`DS4_QWEN4_PLE_TRACE`) to decompose the 3 t/s gap into
"n-gram pread wall-clock share of decode" + "serialization under MTP verify."
That requires a rebuild → **stop :8002 first (single-instance), build, relaunch,
re-run this exact baseline for an apples-to-apples instrumented control.**
