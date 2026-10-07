# PLAN-GATED-SCAN — chunked GDN prefill scan (Phase 1)

Mission: replace the serial per-token GDN scan for PREFILL rows with the
UT-transform chunked form (fla-style), attacking the scan arm of the gdn
bucket: 9.73 ms/layer-chunk @T=8192, ~26% of gdn, ~5.4% of prefill wall.
Target: 2-3x scan speed => +3-6% prefill wall. Decode/verify untouched (see
D4). Est. 4-7 windows total (Phases 1-3).

## Established facts (do not re-verify)
- Phase 0 PROBE PASSED, banked: chunked form is algebraically exact (fp64 vs
  serial anchor: 1.4e-17); fp32 chunked drift == fp32 serial floor (7-12e-9
  out / 2-6e-8 state). A chunked GPU kernel is the SAME 1-ULP golden-decision
  class as the ST flip — not new semantics. Commit a32ce83; log
  tests/spec_economics/results/20261005_gdn_chunkprobe.log.
  Probe: tests/spec_economics/gdn_chunk_scan_probe.py (gen_head/serial/
  maxabs are reused by the UT reference).
- Anchors: bench arms "gdn prefill T=8192: qkv proj / ab-gemv+conv+prep /
  scan r4" = 9.09 / 4.23 / 9.73 ms per layer-chunk (test_qwen4_kernels
  QWEN4_BENCH=1, model-free, coexists with running engine).
- Recurrence (gdn_reference, tests/test_qwen4_kernels.c): per token
  S*=g; u=beta(v-(S k)); S+=outer(u,k); o=S q. State [dv][dk], D=128,
  Hv=48 heads (Hk=16 shared), g=exp(a*softplus(alpha+dt)) with a in (-8,-.1)
  => per-step g can be ~1e-14; ANY formulation dividing by gamma_t must use
  pairwise exp(gc_t-gc_s) ratios, never k/gamma.
- Live production: main @ nazerim (pushed through 45c5cb4), umbrella exact
  tree (QWEN_EXACT_VERIFY=1). Bucket shares confirmed live (moe 36/attn 24/
  gdn 24/hc 15/ple 1%). Arm-selection alternatives CLOSED (see QSA-
  ATTRIBUTION-20261004.md final sections) — scan is the last kernel-class
  candidate that is not a golden re-capture project on its own.

## Phase 1 stories
- P1a DONE 2026-10-05, PASS (log results/20261005_utref.log):
  ut_f64 vs serial anchor = 1.2-3.3e-16 -> the REALIZABLE UT formulation
  (pairwise exp(gc_t-gc_s) ratios, strictly-lower forward substitution) is
  algebraically exact; ut_f32 = 0.8-1.9e-7 outputs / <=2.1e-7 state — ~10x
  the serial fp32 floor but the same 1e-7 ST-flip decision class. Also
  re-verified against gdn_reference (tests/test_qwen4_kernels.c:688): g and
  beta are PER-HEAD SCALARS (ssm_a[j], ab scalar) -> scalar cumsum gc is
  faithful, no per-dim ratio tensor needed; q,k shared per kh=j%Hk (3
  v-heads : 1 k-head) -> K1 grid is Hv=48, k/q tiles amortize 3x. np.where
  evaluates both branches (exp overflow warning) — masked away, kernel
  materializes lower triangle only.
  (Fixed boot bugs: probe __main__ guarded; stray cast/dead line removed.)
- P1b DESIGN LOCKED 2026-10-05 (linearized UT — U is AFFINE in S_prev so the
  per-chunk solve splits into two S-free parts):
  per head j (kh=j%Hk), per chunk C=64, S=[dv][dk] state in:
    gc_t = fp64 cumsum of log g (g from ga[]); EG_t = exp(gc_t);
    R[t,s] = exp(gc_t - gc_s) — ALWAYS a single exp of the difference,
      never exp(a)*exp(-b) (k/gamma overflow trap; fp32 cumsum would put
      1e-4 exponent error on deep-decay chunks — fp64 gc is mandatory);
    Tm = tril_strict(R o (K K^T) o beta-row);  KQ = tril_incl(R o (Q K^T));
    solve (I+Tm) U0 = beta*V            [C][dv]   (forward sub, 64 steps)
    solve (I+Tm) L   = diag(beta*EG) K  [C][dk]
    A_c = (diag(last) U0)^T K   [dv][dk]   last_s = exp(gc_C - gc_s)
    B_c = (diag(last) L  )^T K  [dk][dk]
    O0_c = KQ U0  [C][dv];  G_c = KQ L  [C][dk]
  K2 state pass (sequential over chunks, per head):
    S_c = EG_C * S_{c-1} + A_c - S_{c-1} B_c;  stash S_{c-1} for K3;
    final S -> state[] with the same layout/spos semantics as r4.
  K3 output (parallel): o[t] = O0_c[t] + (EG_t * q_t - G_c[t]) @ S_{c-1}^T.
  Dispatch: K1 grid (Cn, Hv) [dv-slabs: z dim 0/1 halves columns if the
  32KB threadgroup budget needs it — Tm 16KB + U0/L tiles overflow at full
  width], K2 grid (Hv, 4) [32 dv rows each, B/A streamed per step], K3 grid
  (Cn, Hv). Scratch at T=2048 live chunk: A(=SIn reflow)+B ~200MB at
  T=8192 bench scale, ~50MB each per live chunk + O0/G ~25MB — fine.
  Numerics: everything else fp32 (matches ut_f32 1e-7 class); K2's S@B is
  plain 128-dot SIMD work (no simdgroup matmul required for v1).
  P1b SCOPE: kernels + name-table entries + ds4_gpu_qwen4_gdn_scan_chunked_
  tensor host fn taking explicit scratch tensors + harness correctness test
  (vs serial scan, target max|do| <= 5e-7) + bench arms. NO production
  dispatch wiring (that is P1c behind the knob; D4 keeps T<=8 serial).
- P1b DONE 2026-10-05 (v1, naive ALU): CORRECT, NOT FAST. Drift vs serial
  r4 identical-state: max|do| 1.86e-9 / max|dState| 2.56e-9 (at the fp32
  floor; masked-tail T=2000 also clean). But 71.7 ms vs serial 9.7-9.9 ms
  at T=8192 (CK1 37 + CK2 11 + CK3 22; scalar-dot L1 traffic; log
  results/20261005_p1b_bench.log). Roofline: chunked form costs ~50-70 GF
  per layer-chunk vs serial ~38 GF — serial ALU-class v1 can NEVER beat
  serial (12-17 ms at full M5 fp32 ALU); the project only lives via
  TENSOR-UNIT staging (NAX/simdgroup f16, 20-40 TF/s => 2-4 ms feasible).
  F16 MAGNITUDE AUDIT PASSED (8 heads x T=2048, max|.|: U0 3.95, A 0.95,
  L 0.10, Tm 0.12, G 8.5e-4, B ~0) — no f16 overflow risk; expected drift
  moves to the chunked_f16 ~1e-6 class (Phase-0 probe), still ST-decision
  class but a bigger golden commitment than 1e-7.
- P1b.5 DONE 2026-10-05: f16-STAGING AUDIT MEASURED (script
  tests/spec_economics/gdn_chunk_f16_audit.py, log
  results/20261005_f16_audit.log) — the tensor path is numerically
  disqualified at this plan's drift gate, and the fp32 path cannot win on
  speed. Verdict data (T=2048 vs fp64 serial anchor, |o| ~ 0.061):
    f32bti baseline      1.5-2.8e-8 / 1.9-4.5e-8   (sanity: BTI form = ut_f32)
    f16 raw-input stage  2.0-3.5e-5 / 0.6-2.2e-4   (K,Q,V staging ALONE
                                                  dominates the error)
    f16 full CK1 GEMMs   3.7-5.3e-5 / 0.8-2.6e-4   (intermediates ~free)
    f16 full kernel      3.7-5.3e-5 / 0.8-2.6e-4   (CK2/CK3 staging ~free)
  cancellation factors O0/A measured 1.0x (no amplification — the error is
  plain f16 staging, ~7e-4 relative worst-case, compounded through the
  recurrent state over the prefill). Tradeoff table:
    serial r4 (current)   9.7 ms     —
    v1 fp32 chunked      71.7 ms     1.9e-9   (speed fail, measured)
    v2 fp32-ALU tiled  ~6.5-9 ms    ~1e-7    (est.; 2.3x flops vs serial
                                             caps fp32 ALU at ~serial)
    v2 f16-tensor      ~4-7 ms est. 4e-5     (drift fail: ~400x over the
                                             f32 class, recurrent)
  RECOMMENDATION: BANK NEGATIVE. The chunked form carries ~90 GF vs
  serial's 38.6 GF per layer-chunk at T=8192 — only f16 tensor beats
  serial, and that costs a golden commitment far beyond the ST flip for a
  ~3-5% prefill-wall win, plus the P2/P3 tax. OPERATOR CALL (open): (a)
  bank negative (recommended); (b) accept the f16 class (~7e-4 relative,
  recurrent — same order as the tree's existing per-token f16 moe/attn
  staging but compounded) and build f16-tensor v2 next window (speed
  unproven, <=5 ms gate at risk); (c) build fp32-ALU v2 anyway (clean
  drift, likely no win — fallback data point only).
- P1c (0.5-1 window, model-free): host wiring behind env knob
  DS4_QWEN4_GDN_CHUNK (default OFF; =rows threshold later), new bench arms
  beside the existing three; VERDICT GATE: chunked scan <= ~5 ms/layer-chunk
  AND ut-f32-class drift confirmed from P1a => proceed; else bank negative
  and stop (mirror the C2/C3/C4/k32 write-ups).
- P1d: STOP AND ASK the operator before any engine-visible test.

## Non-negotiables
- D4: dispatch split — chunked form ONLY for large prefill row counts;
  T<=8 verify/decode rows KEEP the serial scan. This protects the exact
  verify tree (rcab/identity/verify-exact stay byte-clean by construction)
  and limits the golden question to the prefill stream only, exactly like
  the ST flip did.
- Any prefill stream move => golden decision (operator), never auto-taken;
  decision package = battery divergence sample + delta table before goldens
  re-captured.
- Engine lock rule: model tests need ./ds4-server.sh stop; kernel harness +
  QWEN4_BENCH coexist. Measurement cycles need operator sign-off.
- Persistent tools/logs under tests/spec_economics/ (+ results/ banked,
  tracked); NOTHING in /tmp; docs to .codebase-memory/.
- Keep workstream registration cheap: this file is the plan's home; add an
  AGENTS.md Workstreams line ONLY if one can be removed elsewhere.

## Phase 2/3 (preview, re-plan after P1c verdict)
- P2: per-row state snapshots for checkpoint/MTP across the chunked path
  (prefill checkpoint cadence vs per-chunk states — the hard part; may
  constrain the chunk size or require K3 to emit states at quantum rows).
- P3: validation ladder (harness, battery, rcab unaffected by D4, prefill
  probe, TIMING=2 bucket re-measure), golden protocol, default decision.

## Watch
- Upstream #1154 (PLE overlap) / #1150 (HC writes) touch neighboring
  buckets; #1179 does not touch scan internals. Re-check before merge work.
