# SPLASH — sidequest intel (2026-10-23)

**What:** `github.com/incoai/splash` — Apache-2.0 local inference engine for Apple
silicon by Inco AI (released 2026-09-17/18). ~1.8k stars, 1.4k commits, active.
"Built around the model": narrow model set, each with fused shape-specific Metal
kernels + a per-model **trained DFlash 2 draft** + auto memory plan. OpenAI +
Anthropic APIs, vision/tool-calls/JSON-schema/PDF, paged prefix-KV reuse,
**GDN recurrent-state snapshots** (hybrid models), INT8 KV + SSD cache tier.
Ships `splash opencode` integration and is a day-zero LM Studio engine.
Requires M3+, macOS 26.4+. `brew install incoai/tap/splash`.

**Serves our exact model:** Qwen3.8-Flash-Next is in its list
(`ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF:Q2_0`, unsloth variants too).
Also Qwen3.8-27B, Qwen3.6-35B-A3B, Ornith-1.5-122B.

## Splash's own numbers (their README, M5 Pro)
| model | decode short | cached TTFT 32K | agg decode 4-conc |
|---|---|---|---|
| Qwen3.6-35B-A3B | 210 (1.7×) | 123 ms (6.6×) | 357 (2.0×) |
| Qwen3.8-27B | 74 (2.0×) | 282 ms (7.3×) | 170 (3.9×) |
| **Qwen3.8-Flash-Next** | **95 (1.3× vs Strata)** | 171 ms (212×) | 171 (2.4×) |

vs llama.cpp (27B, M5 Pro/M3 Max): 74/92 tok/s = 4.5-5.3× plain, 2.7-4.6× vs
llama.cpp+MTP; next-token agreement 99.3-99.45% (quality preserved).

## Maps 1:1 onto our decode findings (DECODE-CONCURRENCY-BASELINE / single-stream)
1. **"Speculative decoding is the decode path, not an option"** + "more of them
   survive verification than with the model's own multi-token prediction" →
   a *trained* draft (DFlash 2) beats built-in MTP. This is exactly our validated
   single-stream thesis (code acceptance 1.5→1.94, +11→+30%); the lever is a
   better drafter, and Splash ships one per model. **We already run DFlash
   drafters in this ecosystem** (`dflash-mlx/`, `eagle/` Glimmer DFlash WIRED);
   Inco's DFlash drafts run in llama.cpp/SGLang/vLLM, 6M downloads.
2. **"Speculation lives inside the batch"** — per-request draft state advancing
   with its own accepted tokens, so spec works *with* concurrency. Directly
   targets the inefficiency we measured: MTP acceptance collapsed 1.50→~1.03 at
   batch width ≥2 (ours fights batching; theirs is designed not to).
3. **"The decode step runs as one unit"** — drafting+verify+accept+state-update
   submitted as ONE Metal command buffer, not step-by-step → kills the per-step
   round-trip overhead we saw (our step p95 climbing with width).
4. **GDN recurrent-state snapshots + paged prefix KV** → cache reuse across turns
   (their 171 ms cached TTFT on Flash-Next). Overlaps our closed ds4-kv-cache
   anchor work + omlx GDN PRs.

## Honest caveats
- Headline "2×" is on the **27B**; on **our** Flash-Next it's only **1.3×** (and
  vs "Strata", not ds4/oMLX, and on **Q2_0** — smaller/faster than our Q4). So
  the advantage does NOT transfer at face value to our primary model.
- Ratios are vs "the next-fastest engine *they* measured"; single-request tok/s
  is a marketing metric. See skeptic piece: darklyenergized "Tokens per second
  is the wrong number" (methodology + end-to-end agent latency critique).
- Different hardware baselines (M5 Pro vs our M5 Max 128GB); no direct ds4 A/B yet.

## Actionable follow-ups (pick later; NOT done this session)
A. **Benchmark Splash directly on our M5 Max** vs ds4 :8002, same Qwen3.8-Flash-
   Next, same quant if possible — is the 1.3× real on our box, or does ds4 match?
   (cheap, decisive, uses our existing bench prompts.)
B. **Port the ideas into ds4**, in priority order our data already ranks:
   1. fused one-command-buffer spec step (remove per-step CPU round-trips);
   2. get a trained **DFlash 2 draft for Qwen3.8-Flash-Next** and wire it as the
      ds4 drafter (ds4 already has external-drafter spec: DSpark support model);
   3. batched speculation with per-request draft state (fix acceptance collapse).
C. Just *use* Splash as the serving engine for opencode if A shows it wins —
   it's a drop-in `splash opencode`, Apache-2.0, no ds4 rebuild.

Sources: inco.ai/blog/splash/, github.com/incoai/splash README, ai-tldr, note.com
deep-dive, darklyenergized critique (unread). DFlash2 numbers: inco.ai/blog/dflash2.

## COMPAT VERDICT — will Splash serve OUR current model? (2026-10-23)
**Our file: NO. The model: YES, but via a different GGUF.** Our GGUF is a
ds4-proprietary **`qwen4exp` "Q40Routed (converted fast-pack)"**, not a stock
GGUF. Proof:
- arch id `qwen4exp` (standard llama.cpp/Splash arch is `qwen3next`) + ds4-only
  KV metadata (`ds4.qwen4.ngram.*`, `qwen4exp.ple.*`).
- custom tensor dtypes: **Q8_1 ×23** (QKV/proj/output), Q4_K ×2 (routed experts
  as one blob), F16/F32 hyper-connection splits — NOT unsloth UD-Q4_K_M.
- **95 GiB BF16 PLE table** = `per_layer_token_embd.weight` [160, 320001536] at
  abs off 74868770080, page-aligned to trail the resident weights. File = 165 GiB
  (69.7 GiB resident weights + this trailing table).
- **Even llama.cpp's `llama-gguf r` aborts on it**: "per_layer_token_embd.weight
  has offset 74868770080, expected 74868746752; failed to read tensor data" — the
  page-separated trailing table breaks the contiguous-offset invariant. Splash
  loads standard GGUF/MLX and REFUSES non-standard (stricter, not looser) → it
  will not ingest our fast-pack.

Consequences for the A/B:
- Must `brew install incoai/tap/splash` and let it pull a **stock** Qwen3.8-Flash-
  Next GGUF (unsloth `unsloth/Qwen3.8-Flash-Next-GGUF` or ISTA GSQ-RCO), ≈ +20 GiB
  download (1.4 TiB free on FireCuda520; disk is fine).
- So the test is **engine-vs-engine + packaging-vs-packaging**, NOT the same
  artifact on two engines. Quant isn't identical (ours Q8_1/Q4_K fast-pack vs
  their UD-Q4_K_M). Still the fair "which is faster on this box for this model"
  question — just label it correctly.
- Splash already serves Flash-Next (95 tok/s on M5 Pro 20-core 64 GB) so it
  streams/handles the PLE n-gram table too, via its own SSD cache tier.
- Host prereqs OK: macOS 26.6.2 (≥26.4), M5 Max (≥M3), ample RAM/disk.
- Swap care: single instance + memory → ds4 :8002 must stop for Splash; Splash
  defaults release model after 10 min idle, `--max-memory` to coexist with apps.
- Tool-call check: verify Splash's OpenAI SSE tool-call deltas don't hit the
  null-function.name crash our toolcall-guard plugin exists for.

## DFlash 2 — the real lever (2026-10-23, from incoai/Qwen3.8-Flash-Next-DFlash2 + blog)
DFlash 2 = **block-diffusion drafter** (drafts a whole block in ONE pass, keeps
top-16 candidates/position, a 2.0M-param **path selector** traces one coherent
path, and **two-tap dynamic convs** (16.5M, +3% params) fix suffix decay). Lossless.
There is a trained draft for OUR model: `incoai/Qwen3.8-Flash-Next-DFlash2`
(0.6B; GGUF quants 362 MB–1.22 GB, safetensors BF16).

Flash-Next acceptance + throughput (SGLang, GB300, block 4 = 3 drafts/step,
xhigh reasoning, temp 1.0; NOT greedy, NOT Mac — treat as directional):
| task | MTP tok/s(×AR) c1 | DFlash2 tok/s(×AR) c1 |
|---|---|---|
| GSM8K | 326.0 (1.59×) | 368.2 (1.79×) |
| MATH-500 | 325.9 (1.55×) | 383.6 (1.82×) |
| HumanEval | 296.9 (1.40×) | 363.8 (1.72×) |
Acceptance length c1: MTP 2.50–3.08 → DFlash2 2.83–3.26 (~+15–25% over MTP).

**Adoption constraints:**
- llama.cpp **cannot drive the Flash-Next draft yet** — the model carries its
  hidden state as **4 hyper-connection streams** and the draft is trained on their
  **mean**, which llama.cpp doesn't compute for the draft (PR #27342 open).
- Official paths: SGLang + a target-capture patch (validated TP1), or **Splash**
  (model-specialized; presumably computes the HC mean → this is why the A/B with
  Splash is the practical way to see DFlash 2 on our Mac).

**ds4 porting insight (big):** ds4 ALREADY consumes mean-over-HC rows for its
MTP/DSpark drafter (`metal_graph_eval_mtp_draft_from_hc`, "DSpark target features
consume mean-over-HC rows") — i.e. it has the exact capability llama.cpp lacks.
So implementing DFlash-2 drafting in ds4 (block-diffusion + selector + two-tap
conv) is feasible and is the top ds4 decode lever: our measured single-stream MTP
acceptance ~1.5–1.9 vs DFlash2's ~3 → the "+30–80% single-stream" we predicted,
via a better drafter, not MTP tuning.

## ds4 :8002 single-stream REFERENCE (portable HTTP harness, greedy 300 tok)
short ~2.6k: **66.4 t/s** (TTFT 0.80s) | long ~58k: **65.4** (1.09s) | code:
**64.1** (0.84s). usage-reported (accurate). = the concurrency-1 number to
compare Splash's Flash-Next decode against. (Matches the 62–67 t/s earlier.)

## UPDATE 2 (same day) — two structural facts found in ds4 source
1. **ds4 cannot load an external drafter for qwen4** (ds4.c:73009 "external MTP
   models and power throttling are not supported"); qwen4 spec uses the model's
   own built-in `nextn` MTP head only. DFlash2 is NOT a nextn head → **cannot be
   dropped into ds4**. llama.cpp's DFlash2 support also can't drive Flash-Next
   (draft needs mean-over-4-HC-streams, which neither computes yet). ⇒ **Splash is
   the only Apple-silicon engine that runs Flash-Next + DFlash 2 today.** That is
   the entire justification for measuring it.
2. **ds4 already has an MTP depth subsystem + instrumentation** (no Splash needed):
   - `DS4_QWEN4_MTP_DEPTH=2|3` — force shallow (T=2) vs deep (T=3) draft depth.
   - `qwen4_spec_depth()` — adaptive: engages depth-3 when recent acceptance is
     high (DEPTH_BITS_ON window, backs off on 2-reject streak). Env:
     `DS4_QWEN4_DEPTH_BITS_ON/_OFF/_R2_OFF`.
   - `DS4_QWEN4_MTP_PROFILE` — per-stage timing bucketed by depth (shallow/deep/
     plain), rolling avg /256 cycles. `DS4_QWEN4_SPEC_TRACE` — per-verify outcome.
   - acceptance% log needs `--mtp-timing` + cycles (ds4.c:75907).

### DFlash2 Flash-Next measured (SGLang GB300, block=4, xhigh, temp1.0)
concurrency 1 tok/s vs AR: MTP 1.35–1.59× → DFlash2 1.63–1.82× (≈ +15–20% over
MTP). c32: MTP 1.12–1.24× → DFlash2 1.43–1.73×. Acceptance length c1: MTP
2.50–3.08 → DFlash2 2.83–3.26. Block limited to 4 by QSA impl. (Different target
build: official FP8, not our ds4 Q4 fast-pack.)

### Revised plan
- **ds4-native lever (in our control, no Splash):** run `DS4_QWEN4_MTP_PROFILE` +
  sweep `DS4_QWEN4_MTP_DEPTH=2/3` on our workloads → is depth-3 engaging enough on
  code (where acceptance was 1.94)? The adaptive policy may be under-engaging
  deep drafts → free single-stream tok/s. THIS is the "MTP worth investigating"
  answer: yes, and the tooling already exists.
- **Splash A/B (the download):** tests whether the one-and-only DFlash2-on-Mac path
  beats ds4+MTP end-to-end (decode + cache-reuse + concurrency), not just single
  stream. Different model build (stock UD-Q4_K_XL vs our fast-pack), so engine-vs-
  engine. Worth it because DFlash2 is otherwise unreachable on this box.

## A/B RESULT (2026-10-23, this M5 Max, same streaming harness, greedy)
single-stream decode tok/s:
| prompt | ds4 (Q4 fast-pack, MTP) | Splash (UD-Q4_K_XL, DFlash2) | gain |
|---|---|---|---|
| short 2.5k | 66.4 | 96.0 | **+45%** |
| long 58k  | 65.4 | 96.4 (n=1/3, cold prefill 27.5s) | +47% |
| code     | 64.1 | 91.1 | **+42%** |
Splash reproduced its own published 95 t/s on Flash-Next (we measured 96).
- Splash loaded PLE **resident 27.4 GiB (IQ4_NL)** vs our 95 GiB BF16 (disk-streamed);
  experts 73 GiB resident. verify-rows all (reproducible greedy).
- tool-call SSE: name on first delta only, then args — **no null/empty-name** deltas
  (clean OpenAI pattern → does NOT trigger our toolcall-guard crash).
Caveats: different engine AND different GGUF build (stock UD-Q4_K_XL vs our fast-pack;
our file won't load in Splash). TTFT compare contaminated by ds4's warm prefix cache.
=> The 3 ds4 levers we found "unavailable/locked" (better drafter, fused row-invariant
verify, IQ4_NL PLE) are exactly what Splash ships. It wins on decode by ~1.45×.

## MATCHED 512k A/B (20261023, same harness, greedy, verify-rows all, YaRN2× both)
context regime is the whole story — the +45% short win does NOT hold at long ctx:
| regime | ds4 (Q4 fast-pack, PLE-on-disk) | Splash (UD-Q4_K_XL, PLE-resident) | winner |
|---|---|---|---|
| short decode | 66.4 | 95.2 | Splash +43% |
| code decode | 64.1 | 104.3 | Splash +63% |
| **300k decode (live KV)** | **57.6** | 45.3 | **ds4 +27%** |
| 300k warm TTFT | 10.4 s | 0.31 s | Splash ~33× |

Splash KV PLAN: PLE 27.4 GiB + experts 73.5 GiB RESIDENT → only **10488 MiB**
left for a request's live KV → 300k tokens (~12.9 GiB) EXCEEDS it → **Splash spills
live KV to SSD** (hot-block cache 390 MiB re-reads blocks per step) → decode drops
57.6→45.3. ds4 put the PLE on disk (BF16) instead, freeing RAM → 300k KV fully
resident → faster long decode.

## The PLE/BF16 answer (the user's question)
- PLE BF16 95 GiB vs IQ4_NL 25 GiB = **3.8× table**; per gathered row 320B vs 90B =
  3.6× traffic. Decode PLE traffic ~5 KiB/tok (16 rows). ds4-measured pread cost
  = **~1.5% of decode, context-independent** → the SSD penalty is real but tiny.
- We PAY ~1.5% to keep PLE lossless BF16, and SPEND the freed ~70 GiB RAM on resident
  KV. That is precisely why ds4 BEATS Splash at 300k (57.6 vs 45): our quality-first
  PLE-on-disk bet = the long-context resident-KV win. Not odd — a deliberate,
  opposite allocation (Splash keeps PLE resident, spills KV; we keep PLE on disk,
  keep KV resident).
- So: don't quantize our PLE for speed (would buy ≤1.5% decode, cost fidelity, and
  lose the RAM that wins long ctx). The only clean PLE win is the ~1.5% + faster
  load, not worth the quality.

## Warm-TTFT investigation — it's CONFIG, not a build (20261023)
Measured warm replay of the 302860-tok xlong: TTFT 9.7s, decomposed as
  load 294912-tok anchor from disk (chain=30, 1914 ms)
+ **re-prefill 7948-token tail** (294912..302860) @ ~1015 t/s ≈ 7.8s   <-- the cost
The tail exists because continued stores snap to a **grid step** (default
`DS4_KVSTORE_DEFAULT_ANCHOR_STEP=8192`, doubling to 16384 above small-dense). A
frontier at 302860 is off-grid; only the 294912 rung was stored → resume re-does
the 7948 gap. Even a shutdown end-anchor (303060) doesn't help a replay: its text
key includes the generated tokens, so a fresh prompt can't match it.

### Speedup (disk-RESUME path only)
Tail ~ uniform[0, grid): grid 16384 → mean ~8192 → **mean ~8.1s, worst ~16.1s**
of warm TTFT. Kill it (finer grid / frontier anchor) → resume TTFT 9.7s → ~1.9s
≈ **5× on resume**. NOT per-turn: a live in-RAM session append already re-prefills
~0; the cost is only on cold RESUME (restart, slot rotation, fan-out subagents
sharing a long prefix, idle past retire-grace).

### Levers (CLI flags, no rebuild): --kv-cache-anchor-step / --kv-cache-small-dense / --kv-cache-tail-anchors
Defaults: anchor_step 8192, small_dense 16384, tail_anchors 2, mid_spacing 131072.
Our launch sets `--kv-cache-small-dense 49152` (raises the always-keep floor but
pushes the 302k region onto the coarser 16384 grid → 8k tails).
CATCH (documented war-story, ds4_kvstore.c:2623): finer grid / frontier stores
cause **write amplification + eviction pressure** — one session wrote 19.5 GiB vs
5.8 GiB with an intact ladder. Current KV dir already **255 GB / 546 files**. So
this is a TTFT-vs-writes tradeoff to TUNE & MEASURE, not a free win.

### STATUS: DECLINED by operator (2026-10-23) — do not re-open
Decision: **not pursued.** KV anchoring is already tuned to our real workloads
(retire-grace 3600s, batched-session 2, small-dense 49152); the warm-resume path is
not hot enough in our traffic to justify re-tuning the grid, and finer grids raise
blade write amplification (the ds4_kvstore.c:2623 war-story; KV dir already 255 GB /
546 files). This is a tuned-config tradeoff, not a defect.

Already-monitored overlap — do NOT treat my re-findings as new opens:
- `QSA-ATTRIBUTION-20261004.md` §4.5: "decode-side residual / per-row split ladder in
  the T=2 decode dispatch (~0.5 ms/cycle)" = the same warm-residue thread.
- Same doc title: **"C3 tested-and-declined"** — wide/depth-3 verify already evaluated.
  My independent "forced depth-3 ≈ 5% slower" (DECODE-CONCURRENCY baseline) CORROBORATES
  that decline; it does not reopen it.

Net on decode levers (all measured this session, all closed): PLE read ~1.5% (off the
table); multi-slot = throughput not single-stream; MTP depth = declined (chained single
head + wide-T gated on B2); warm-TTFT anchor tuning = declined (config tuned). The only
genuinely open ds4 fronts are prefill-side — **MoE (biggest bucket) + the GDN chunked
scan** (attn-NAX already merged Oct-8 shrank attn) — read `HANDOVER-20261023.md` first,
and VERIFY MoE-NAX-Q4 status before spending a window. Splash's wins are the drafter/fused-verify
kernels, which ds4 can't take as a knob (external qwen4 drafts rejected `ds4.c:73009`).
