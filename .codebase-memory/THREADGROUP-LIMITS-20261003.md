# Threadgroup-limit safety audit — qwen4 & dsv4 Metal dispatches (#4063 bug class)

Lane A of the 2026-10-03 overnight (`.codebase-memory/omlx-v070-final-perf.md`
§4 action #5). Read-only pass by subagent, verified excerpts by main session.
Trigger: oMLX 0.7.0 #4063/#4064 — undeclared threadgroup limits produced
SILENTLY WRONG decode attention on M1 Max, not crashes.

## Headline structural finding

**No pipeline in ds4 declares `maxTotalThreadsPerThreadgroup` at creation.**
Every PSO goes through plain `newComputePipelineStateWithFunction:`
(`ds4_gpu_get_pipeline`, ds4_metal.m:2724-2754; the only descriptor path,
`ds4_gpu_new_mul_mv_tg_multiple_pipeline` :3338-3358, sets only the
threadExecutionWidth-multiple hint). Protection is per-site runtime queries —
dense in the legacy dsv4 path (~40 query sites, last :45714; capability gates
:9841, :10003, :20718, :29594-29596, :34082ff) and **ZERO in the qwen4 path**
(`qwen4_dispatch_resident` :48480-48580 passes caller `tg` straight to
`dispatchThreadgroups:` :48576). 290 dispatch sites total.

Failure-mode nuance vs oMLX: ds4 checks command-buffer status
(`ds4_gpu_wait_command_buffer` :1673-1719) — on validating drivers an over-limit
dispatch fails LOUD per step; the #4063 silent-truncation class needs a
non-validating driver/GPU combo.

## RISK register (severity-ordered; full family tables in the overnight lane output)

| # | Site(s) | Width | Mechanism if per-pipeline limit < width | Fix shape |
|---|---|---|---|---|
| RISK-1 | dsv4 decode split-K reduce `kernel_flash_attn_ext_vec_reduce(_rope)` :28066, :28859, :29439, :29802 | `32*nwg`=1024 (env `DS4_METAL_FLASH_NWG` ≤32) | reduce loop `i += NWG_` leaves head rows unwritten → stale/garbage attention EVERY decode token (silent) | NOT clampable (NWG baked as function constant :3893/:3946); query limit at creation, re-create with smaller nwg or fall back to guarded packed path (:29594 pattern) |
| RISK-2 | qwen4 indexer select `IDX_SELECT(_PRE/_ROWS)` :49313, :49317, :49589 | const 1024 | kernels width-generic (`nth = ntg.x`, metal/qwen4.metal:1737) with 16-deep register prefetch — the profile whose limit lands 512/768 on older GPUs; skip → stale `sel` block indices → wrong KV blocks (silent) | clamp `nth = MIN(1024, pipeline.maxTotalThreadsPerThreadgroup)` /32*32 — kernel already width-adaptive (strided histogram passes) |
| RISK-3 | qwen4 `GDN_FRONT` :50296-50299 | env ≤1024, default 1024 M5 / 256 else | conv+decay state silently not produced | clamp env value to queried limit after PSO materialization |
| RISK-4 | qwen4 MoE decode-mv `MOE_MID/DOWN` :49704/:49747, HC pair :48732 | `32*nsg`, nsg env ≤16; **default already 16 (512) on M3U/M5 for mxfp4/q2k** (:48471-48474) | register-heavy dequant PSOs (:48502) may cap at 256 on M1/M2 → every-layer expert FFN wrong | clamp nsg at PSO insert or dispatch |
| RISK-5 | dsv4 rms-norm family via UNQUERIED `ds4_gpu_rms_norm_threads` :5744 (8 sites :22262-:22869) | ≤1024 (7168-dim) | light kernels, limit<1024 unlikely — inconsistency: queried twin `..._pipeline_threads` :5752 exists and is used at :22925 | switch 8 sites to the queried twin (one-line each) |
| RISK-6 | `kernel_mul_mm_id_map0` :33298 | TG=384 (ne02 experts, model-derived) | unverified 384 ≤ limit; kernel width-generic (metal/moe.metal:7742-7760) | MIN-guard + bail |
| RISK-7 | qwen4 `MOE_BUILD_LISTS` :49864 | const 512 | trivial kernel, 2 KB tgmem — low likelihood, zero-cost fix | clamp in helper |

Theoretical-only (≤256 unqueried, not counted): flash blk (32,8) :28590,
indexed heads8 :30890/:30922, attn_merge_wide :49420/:49646, const-256 utility
sites, parallel gate_up env nsg :10162.

## Verdict

- **Deployed M5 Max: no live #4063-class exposure.** All widths ≤1024 (device
  max), M5 per-pipeline limits highest in family, both width-1024 families are
  the measured paths here; residual exposure only via env overrides
  (`DS4_METAL_FLASH_NWG`, `*_NSG`) — self-inflicted and mostly loud (CB check).
- **Older GPUs (M1–M4 Max class, i.e. UPSTREAM USERS): 4 real families / 16
  sites** (RISK-1 ×4 and RISK-2 ×3 run every token; RISK-5 ×8 theoretical-
  unqueried; RISK-6 ×1) + 3 env-conditional families (RISK-3/-4/-7). RISK-1 and
  RISK-2 are the true #4063 analogs.

## Fix plan (cheapest systematic) — STATUS 2026-10-03 overnight

1. **qwen4 chokepoint clamp** — DEFERRED as clamping (needs per-kernel
   width-invariance proofs: strided/ntg-generic vs width-baked reductions vs
   function-constant-specialized nsg PSOs at :48502 where clamping the width
   would contradict the baked constant). **SHIPPED instead (`555ee22`): a
   fail-closed width guard** in `qwen4_dispatch_resident` — `tg.width >
   pipeline.maxTotalThreadsPerThreadgroup` ⇒ one loud stderr line per kernel
   + return 0 (callers fail the request; loud beats silently truncated
   threadgroups). Never fires on M5 (limits ≥ widths there); converts
   RISK-2/-3/-4/-7 on older GPUs from maybe-silent to definitely-loud.
2. **RISK-5**: SHIPPED (`555ee22`) — all 6 dsv4 sites (:22262 plain,
   :22390 scale, :22482 weighted, :22546 add, :22632 fused q/kv, :22861
   fused q/kv norm+RoPE incl. the deferred-kv-task copy) now use the
   queried twin `ds4_gpu_rms_norm_pipeline_threads`.
3. **RISK-1** (dsv4 flash reduce @1024): design note only — needs nwg
   re-creation plumbing at :3893/:3946 + fallback; dsv4 not served in this
   window. Upstream-issue candidate (matches #4063's audience). NOT STARTED.
4. **Diagnostic SHIPPED (`555ee22`)**: `DS4_METAL_LOG_TG_LIMITS=1` logs
   `maxTotalThreadsPerThreadgroup`/`threadExecutionWidth` once per pipeline
   at creation in `ds4_gpu_get_pipeline` — answers open question #2 on any
   target GPU (specialized function-constant PSOs are covered at dispatch
   time by the guard's message instead).
5. Optional hardening (declare limits via descriptor for ≤256 kernels):
   NOT STARTED, superseded in value by 1+4.

## Open questions

1. Driver behavior matrix on M1/M2 Max (loud CB error vs silent truncation) —
   decides wrong-output vs dead-engine for RISK-1/-2/-5 on those machines.
   Probe idea: dispatch width 1024 against a deliberately register-bloated
   kernel in a unit test (needs hardware we don't have; can ship the test
   guarded by device query).
2. Actual per-pipeline limits for the RISK kernels on M1–M4: one-shot
   diagnostic dump (`maxTotalThreadsPerThreadgroup` at PSO creation under an
   env flag) converts every "theoretical" row to fact — cheap to add alongside
   fix 1.
3. `DS4_QWEN4_ATTN_SPLIT_KEYS=1` extreme vs `maxThreadgroupsPerDeviceGrid` at
   rows2 max batch — likely fine, unverified.
