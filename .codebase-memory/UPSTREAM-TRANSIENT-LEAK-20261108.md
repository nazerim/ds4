# Metal SSD-streaming decode slows linearly with session length — transient-buffer reap defects (V4.1 lane)

**Machine/model:** M5 Max 128 GB, macOS 15; `ds41f-q2` = DeepSeek-V4.1-Flash-1017-IQ2_XXS-Q2_K-imatrix, `--ssd-streaming`, ctx 262144, single slot; engine build = nazerim/ds4 fork (upstream through `0accf8b` + fork pool commit `fc94f2e`).
**Symptom:** generation starts 16-23 t/s after boot and decays monotonically to 2-3 t/s over 1-3 h of agentic use. Restart "fixes" it. Page cache and expert-cache *policy* are innocent (purge test null; hotness-decay/decode-only/preload A/Bs all null).

## Attribution: two distinct defects, only one is upstream-relevant

**Defect 1 (fork integration seam — the 129k-entry leak we hit).**
Fork commit `fc94f2e` reworked `g_transient_buffers` from a raw buffer list (cleared wholesale by `removeAllObjects` at every drain) into an owner-scoped pool: wrapper `DS4GpuTransient` entries stamped with `pthread_self()`, reaped by thread-filtered scan (`ds4_gpu_release_transient(NO)`). Upstream V4.1 work (#1073) merged *after* that contract change still contains three **raw `addObject:` insertions** on the V4.1 decode path, which are legal under upstream's pool but dead under the fork's reaper: `release_transient` performs wrapper-ivar reads (`e->buffer`, `e->owner`) on bare `id<MTLBuffer>` objects — owner reads 0, matches no thread, never reaped (and the raw inserts bypass `g_gpu_retain_mu`: data race vs the locked reaper). Three per-token sites, ~2 entries/layer-batch: `ds4_gpu_dsv41_hc_block_input_rows` (key `v41_block_input`), `ds4_gpu_v41_deferred_counter` (key `v41_deferred_hc`), `ds4_gpu_v41_collapse_slots` (key `v41_collapse_slots`). Owner histogram proved every leaked entry had owner `0x0`. Fix in fork: route through `ds4_gpu_retain_transient()`.

**Defect 2 (genuine upstream asymmetry — smaller blast radius).**
Both shared-event drain helpers — `ds4_gpu_signal_batch_and_wait_event()` and `ds4_gpu_commit_and_wait_selected_readback()` — `return 1` on the success path WITHOUT clearing `g_transient_buffers`, while all three error branches clear it. Upstream's wholesale `removeAllObjects` reap limits the damage (the next non-event drain clears the backlog), so it only fully degrades loops that are *entirely* event-drained: notably `commit_and_wait_selected_readback` is on the default path for ssd-streaming IQ2/MXFP4 shared-overlap decode (and forced in ROCm builds) — `signal_batch_and_wait_event` is opt-in via `DS4_METAL_Q4_SELECTED_SHARED_EVENT`. Upstream-shaped fix: add the clear on the success return of both helpers.

Consequence chain for our symptom: leaked entries are never removed -> each batch end re-scans the full list -> per-token wall grows **linearly in cumulative tokens (~20 us/token)**, monotonic, never saturating, surviving conversation switches, cleared only by process restart. DSpark partially masks it (verify batches drain per 5 tokens), which initially read as "speculation helps on real traffic."

## Speed regression pair (CONTRIBUTING.md ds4-bench protocol)

Machine: M5 Max 128 GB / Metal / `ds4flash.gguf` = DeepSeek-V4.1-Flash-1017-IQ2_XXS-Q2_K-imatrix / `--ssd-streaming`, sweep 2048..65536 step 2048, gen-tokens 128, greedy, back-to-back same thermal state. "Bugstate" = final tree with only the 5 fix hunks reverted.

| ctx frontier | bug gen_tps | fixed gen_tps | delta |
|---|---|---|---|
| 2048 | 19.29 | 20.50 | +6% |
| 8192 | 19.87 | 21.71 | +9% |
| 16384 | 16.41 | 21.46 | +31% |
| 32768 | 12.28 | 20.70 | +69% |
| 49152 | 9.72 | 23.39 | +141% |
| 65536 | 6.58 | 23.76 | **+261%** |

Bug curve decays 19.3 -> 6.6 t/s across the sweep itself (late/early = 0.34) — the degradation is visible within a single 64k-token bench run; the fixed curve is flat-to-rising (mean 12.70 -> 22.24). First-token latency at 65k: 326 ms -> 201 ms.

## Instrumentation used

- `DS4_METAL_CB_TIMES=1` (pre-existing): prints list length at every batch end. Measured: **3 -> 129,440 entries in ~2 minutes** of decode; all leaked entries owner `0x0`.
- `DS4_V41_STEP_TIMING=1` / `DS4_V41_LAYER_TIMING=1` (#1298 applied locally): per-token `moe` bracket 38 ms (fresh) -> 343 ms (stale); `pre`/`post`/`engram`/`logits` flat — cost is the release-scan over the unbounded list inside the MoE encode bracket's per-layer drain.
- Deterministic 24-turn soak (mixed prose/code/JSON/CJK appends + thinking decode): before fix, turn-22 blended 1.5 t/s; after fixing the raw-insert sites, probe decode 13.6 -> 19.5 -> **20.7 t/s rising**, list max 3 (wrappers were always reaped by the normal `end_commands` drain; only raw entries persisted). Speculation (DSpark) additionally amortizes drains over 5 tokens, softening (never removing) the curve on that lane.

## Fix — two artifacts

**Fork branch (`fix/metal-transient-reap`, 5 hunks, ds4_metal.m):**
1-3. Route the three V4.1 raw insertions through `ds4_gpu_retain_transient(...)` (wrapper + owner stamp + mutex) — kills the active leak.
4-5. Add `ds4_gpu_release_transient(NO)` to the success path of both event-drain helpers — reap symmetry under the fork's owner-scoped contract.

**Upstream PR (re-scoped; 2 lines + asks):** add `[g_transient_buffers removeAllObjects];` to the success return of both shared-event drain helpers (`ds4_gpu_signal_batch_and_wait_event`, `ds4_gpu_commit_and_wait_selected_readback`) — their error branches already clear; note `commit_and_wait_selected_readback` sits on the DEFAULT ssd-streaming IQ2/MXFP4 shared-overlap decode path and ROCm, so the omission is not merely opt-in. The three raw V4.1 insert sites are correct under upstream's raw-pool contract and do NOT need changing upstream.

## Same-pattern sites (fork-pool lanes; candidates for the type-check ask)

Remaining raw `addObject:` insertions into the fork's owner-scoped pool (all non-V4.1 lanes, accepted out of scope for this PR but they will leak the same way if exercised): `ds4_gpu_matmul_q8_0_legacy_tensor` (q8 legacy), and the Qwen4 Metal SSD sites (`qwen4_moe_mm_mid/down_tensor`, `qwen4_stream_mid_before_read`, `qwen4_stream_missing_gate_up_ready`, `qwen4_moe_stream_tensor`). GLM uses the wrapper correctly (no raw sites).

## Upstream asks (for the PR body)

- Reap symmetry on all drain paths: clear `g_transient_buffers` on the success return of the two shared-event helpers (this PR's 2 lines).
- Guardrail: a comment/`assert` or helper function so V4.1-era code keeps inserts and reaps on the same pool contract if the list's element type ever changes (the class of bug that bit our fork when we adopted owner-scoped reaping).
- Optional perf: per-thread entry lists instead of one global scanned list.

**Fork-side note:** under the fork's wrapper pool (`fc94f2e`), add a type check (`isKindOfClass:[DS4GpuTransient class]`) in `ds4_gpu_release_transient` so future raw merges can never silently un-reap.

---
*Data/patches authored by the nazerim/ds4 fork session 2026-11-08; soak harness + owner-histogram debug available on request.*
