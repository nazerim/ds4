# oMLX v0.7.0 FINAL vs rc1 — Qwen3.8 delta and ds4-qwen reconciliation

Successor to `.codebase-memory/omlx-v070rc1-perf.md` (read that first; its §2–§5
verdicts still stand except where noted here). Read-only pass, 2026-10-02.
Sources: release notes `gh api repos/jundot/omlx/releases/tags/v0.7.0` ("notes
below cover changes since 0.7.0rc1" — authoritative delta), fork git log
`v0.7.0rc1..v0.7.0` (138 commits, tag v0.7.0 @ `4d4f5a28`), and
`~/Projects/Scratch/muse-glimmer/HANDOFF-20261002.md` (stack state:
`feat/dflash-auto-revert-070` @ `640654ea` = v0.7.0 + our 3 auto-revert commits;
468 tests + 5/5 smoke green; venv `glimmer-070-venv`, mlx 0.32.2, mlx-lm
`94cdcae`, dflash 0.1.10+omlx.9). No builds, no engine starts, no measurements
taken this pass.

## 1. Genuinely new since the rc1 recon (Qwen3.8-relevant)

| Change | What it is | ds4 relevance |
|---|---|---|
| #4023/#4050 Exact Lightning MTP | verify rows **bit-identical to serial decode** for the whole chain (#4023 + follow-ups #4024/#4038/#4039/#4041); greedy verify picks like the serial sampler, `qmv_fast` K block per bit width (#4050) | ds4's `verify_rows_exact` covers only the T==3 split-attention case (ds4.c:57652, :58289, gate in `qwen4_graph_fused`). #4041 "fused row-exact MTP verify windows for MoE, DeltaNet and attention" is a shipped blueprint for lifting that gate past T<=2; strongest external validation of our exactness-first MTP policy |
| #4038/#4039/#4024/#3912 Decode fusions | fused one-token MoE/DeltaNet/attention; two-launch routed experts (was five); deferred HC writes + one-row MoE combine | Decode-side only. Our "ds4 wins the decode rows" claim (rc1 §4.9, 47–68 t/s vs ~32.9 per-stream) predates all of it — re-baseline before re-citing |
| #3958 Spec decode incl. non-NAX | +3.5k lines (`qwen35_verify_qmm`, `qwen35_verify_sdpa_split`, new `qwen35_gdn_verify_fused`, batch_generator, dflash_drafter); Flash-Next no longer falls back to plain decode at 64K on M3 Ultra; **after a performance park MTP resumes with its full head history, not an empty one** | The park→empty-history recovery is exactly the DSpark auto-revert problem class we solve on the :8005 side (3 commits @ tip). Design reference for ds4 spec-decode lifecycle under memory pressure |
| #3908 Split-GDN exact prefix state | stable system/tool prefixes stop being re-prefilled per request; `omlx/cache/prefix_cache.py` persists exact GDN prefix state in split mode | This is the rc1-recon §"Separate, non-kernel" item (partial-block caching / prefix resume at GDN boundaries, line 80): upgraded from idea to shipped upstream implementation to study |
| a28e5a87 SSD cache tails | tip-lineage pruning only ran on rotating-layer models, so hybrid recurrent layouts (GDN, QSA, GLM-5 linear, DSv4.1) piled **one full-state tail per turn**; now the two-turns-back tail + its GDN sidecar (split mode) are deleted, previous turn kept as edited-turn fallback | Pure retention-policy fix of the same cost class that killed our P2.1 (floor ~112 MiB/store from GDN state — figure corrected 2026-10-03, see FLOOR-20261001 CORRECTION). Audit item: does our V2 ladder carry an analogous per-rung full-state waste on the GDN `[state][hist]` fixed slices? |
| #3933 + #4124 memory guard rebuild | guard could refuse with memory to spare or try without; rebuilt (tiers keep ~20%/8%/2% free); post-eviction requests now refresh the memory sample before final admission | oMLX independently hit-and-fixed our deferral-backoff bug class (7da74ea/dfd9652, 3,157 deferrals/day root cause = churn re-opening rows). Confirms the transition-only-logging + re-check-after-evict design. Our analogue of "refresh after eviction before admission" = the kv_mu re-scan path |
| #3955 Vision feature cache | per-image encode cache on qwen4_exp, partial-miss encode, 1 GiB byte-budgeted LRU; follow-up TTFT 3.32 s → 0.33 s in a 4-turn screenshot conversation | Direct template for the ds4 vision-cache ADR (Sep 3, encoder cache). Companion lesson from #4118: prefix cache must key on content (two clips with same prompt shared a cached transcript). Also live on :8005 now (serves qwen4_exp via VLM fallback) |
| #3975 detokenizer once/tokenizer | ~45 ms/request saved on Qwen3.8 | Cheap ds4-server candidate: audit per-request tokenizer/detokenizer setup cost |
| #3974 GPU keep-warm | keep GPU out of idle power state up to 5 min after last request; −1–1.5 s TTFT (MiMo tests); `server.gpu_keep_warm_interval` | ds4 has nothing equivalent (grep: no keep-warm path). Probe first token after idle on :8002; if it pays the power-state wake, this is a cheap server-side win. Upstream also added a 5-min cap (bdebfa0e stops ticks forever) — copy the bound |
| #4122 GDN norm SiLU arithmetic | fused GDN norm gate didn't match the served path in fp16 and at large negative gates; now uses identical SiLU arithmetic | ds4 exposure looks low: `qwen4_silu` is one shared two-sided FP32 form (metal/qwen4.metal:8–24) and prefill kernels explicitly pin op order (:264–268). Still a named audit: any ds4 fused gate must reproduce the served rounding at large-negative gates / fp16 |
| #3935 MTP + expert offload | MTP head stays resident while backbone experts stream (qwen4_exp) | N/A at ds4's current loadout (full-resident on 128 GB); relevant only if expert-streaming is ever revisited |
| #4087 PLE shard load | n-gram table shards released during load → larger quants (oQ8e) load reliably | Operational note for the oqmlx-side ladder (Scratch `qwen38-oq8e-fp16`), not ds4 code |
| #4037 / #4063 / #4031 | offloaded-expert read/compute overlap; declared threadgroup limits fix wrong native decode attention on M1 Max + causal mask kept in fallback; late-join hands off instead of replaying | #4063 is the relevant one as a bug class: missing `max_total_threads_per_threadgroup` / threadgroup-limit declarations changed *results* silently on older GPUs — see action #5 for ds4's residual exposure (small: we query limits at runtime). |

Not new (already analyzed in rc1 recon §2, confirmed unchanged): #3980/#3981
wide prefill steps, #3982 HC prefill fusions, #4020 QSA on tensor units,
#3934 wide QSA tiles, `ShardedEmbedding` host rewrite. The GLM-5.3/MiMo/NAX
attention stack (~60 commits) is other-model traffic.

## 2. Closed items re-checked against 0.7.0

- **ANE (ANE.md: "strategically marginal", closed)** — verdict stands, plus a
  new measurement caveat: the release confirms rc1 had a **TypeError that
  silently disabled ANE prefill** (#3957, "left no GDN procedures eligible").
  All rc1-era oMLX numbers — including the 2,007/1,716 tok/s reconciled in
  rc1 §1/§4 — were ANE-OFF. 0.7.0 changes the baseline; no new ANE perf claims
  shipped and none of our blockers moved (5.9 GB int8 projection window > 4 GiB
  single-die, serializing indexer/compressor overlap window, private-API
  fragility, 2–4% realistic). Keep closed.
- **INT8 MoE (PLAN-INT8-MOE.md: CLOSED, 2.02x not 3x, +0.9% break-even)** —
  stands, corroborated on both halves: (a) #3952 shows their A8 (int8-activation)
  prefill path **silently fell back to W4A16 in rc1** on dense Qwen 4-bit
  projections, i.e. the 42-TOP/s A8 path was NOT in the rc1 headline numbers
  either; (b) 0.7.0 still ships it **off by default, "can change outputs"** —
  the same quality bar behind our rejection. Our root cause (hardware operand-
  width ratio, `tests/mpp_tensor_int8_bench.m`: half 53.08 vs int8 107.16
  TOP/s issue = 2.02x) is untouched. Do not reopen.
- **rc1 §5 ranked plan**: #1 int8 → closed as measured (above). #3
  re-attribution → DONE 2026-09-29 (MoE 37–39% of chunk, PLAN-INT8-MOE §1).
  #2 token-parallel GDN prefill front (~0.4%) → unchanged; all 0.7.0 GDN work
  was verify/decode-side. #4 QSA/indexer tile widening → still open, untouched.
- **"No action" list from rc1 §5 still holds**: chunk size already 8192; PLE
  host gather N/A to C; ANE closed (above, with the rc1-confounder note).
- **Decode superiority** — the one ds4 talking point 0.7.0 puts back in play
  (#3958/#4041 target long-context spec decode on Flash-Next specifically).
  Re-measure before repeating.

## 3. Benchmark confounder (for all future oMLX comparisons)

rc1 published numbers were produced by an engine with ANE prefill silently
disabled (#3957) AND A8 dense-prefill silently falling back to W4A16 (#3952).
0.7.0 fixes both. Any rc1-vs-ds4 or 0.7.0-vs-ds4 comparison inherited those
bugs invisibly. Pin the exact tag when quoting oMLX tok/s; the rc1 recon's
"not comparable" verdict (warm-vs-cold baseline) is unaffected — if anything
it strengthens it.

### §3.1 MEASURED 2026-10-03: the rc1→0.7.0 delta is real and big

Same-prompt cold battery (original rc1-era `perf_payloads.json`, nonce-
prefixed, model `Qwen3.8-Flash-Next-oQ4e-mtp` oQ4e, glimmer :8005 on
0.7.0; scripts now durable in `~/Projects/Scratch/muse-glimmer/flash-next-perf/
rebaseline_070.py` + `rebaseline_070_2026-10-03.json`):

| size | rc1-era cold | 0.7.0 cold | change |
|---|---|---|---|
| 40k | 992.0 tok/s | **1733.0** | +75% |
| 80k | 560.0 tok/s | **1748.3** | **3.12x — the long-context scaling collapse is GONE** (flat 40k→80k) |
| decode warm single-stream | ~34.3 | 33.7 | unchanged |

Attribution: the rc1..0.7.0 range carried #3980-#4020 (wide prefill steps,
exact-HC fusion, QSA main-attention on tensor units, GDN SiLU fix) plus the
#3957 ANE and #3952 A8 repairs — the earlier "recon already covered" framing
was wrong for the final release: those prefill kernels were NOT in the rc1
build that produced the 992/560 numbers.

**Consequence for our talking points (read this before citing rc1 recon §1):**
- **Prefill lead: REVERSED.** ds4 measured 1,319/1,268 tok/s @40k/80k cold
  (same machine) vs omlx 0.7.0's 1,733/1,748 — omlx is now ~35% faster at
  40k and ~38% at 80k on this comparison. Caveats that cut both ways: quant
  lineage differs (Q4_K gguf vs oQ4e MLX), ctx settings differ (524k vs
  262k), and ds4's numbers predate the QSA tile-widening candidate.
  The rc1 recon §5 ranked plan items #2/#4 were aimed exactly at this class;
  they just moved from "nice" to "required" — do NOT repeat the "ds4 wins
  prefill" sentence without re-measuring first.
- **Decode lead: CONFIRMED vs 0.7.0.** omlx 33.7 single-stream warm; ds4
  measured ~65 t/s greedy-with-MTP tonight (acceptance battery). Safe to
  cite with those qualifiers.

## 4. Suggested next actions (ranked; 1 & 2 RESEARCHED 2026-10-02 — see notes)

1. **Study #3908 + #4023/#4041** against our GDN-state slice format and V2 MTP
   payload (`87e11f3` sliced format): both close gaps named in the rc1 recon,
   and #4041's row-exact verify windows are the blueprint for extending
   `qwen4_graph_fused` beyond T<=2.
   → DONE (lane 2): `.codebase-memory/omlx-v070-mtp-row-exact.md` — most
   tricks already equal-or-stronger in ds4; genuine gaps: HC combine_norm
   T=1-only, snapshot depth (rows-recorded + deferred-commit is the answer),
   two-launch HC; priced sketch for T<=8 behind a per-cycle env gate.
2. **Audit V2 ladder for a full-state-tail-per-rung analog** of a28e5a87 —
   pure policy fix, hits the 112 MiB GDN floor directly; check the
   `[state][hist]` fixed slices and the edited-turn fallback need.
   → DONE (lane 1): `.codebase-memory/omlx-v070-cache-policy.md` — retention
   waste structurally absent (rung grid is 8192/16384, not 512; keep-set is
   already frontier+2); live residue = superseded off-grid frontier stores
   reclaiming only under budget (Scenario L pins the fix) + (D-1)x112.2 MiB
   redundant state RE-READS per depth-D chain resume.
3. **Cheap server wins**: per-request detokenizer/tokenizer setup audit
   (#3975 analog); idle-TTFT probe on :8002 (first token after >5 min idle;
   #3974 analog if it pays the wake).
4. **Re-baseline decode + prefill vs 0.7.0 final** before updating any
   DS4FORK.md citation (rc1 comparisons are double-confounded per §3).
5. **Audit dispatch widths vs per-pipeline limits** (#4063 bug class — silent
   wrong results on older GPUs, not a crash). ds4 queries device-wide
   `threadExecutionWidth`/`maxTotalThreadsPerThreadgroup` at runtime, which
   covers most of it; the residual check is that no dispatch exceeds the
   *pipeline state's* `computeMaxTotalThreadsPerThreadgroup` (register
   pressure can lower it below the device query).

## Could not verify

- Whether 0.7.0's Flash-Next prefill numbers (no new table published beyond
  the rc1 claims) now include ANE+A8 — release body states neither.
- #4041's exact kernel structure beyond its title/stat (not read line-by-line).
- ds4 idle-TTFT behavior (no measurement pass; engine restart/queries not in
  scope this session).
- Whether upstream's split-GDN "sidecar" (#3908) layout resembles our
  fixed-size `[state][hist]` slice — needs a read of `prefix_cache.py`
  `_gdn_sidecar` paths before action #1 is priced.
