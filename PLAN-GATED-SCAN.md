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
- P1a (0.5 window, numpy only): run the UT reference
  tests/spec_economics/gdn_chunk_ut_reference.py and validate the
  derivation: expect ut_f64 ~1e-14 class vs serial anchor (if not, the
  algebra is wrong — fix before anything else), ut_f32 <= ~1e-7. KNOWN
  BOOT BUG: it `from gdn_chunk_scan_probe import ...` but that probe's
  __main__ block is unguarded (import executes the whole probe). Guard the
  probe's bottom block first. The chunk_ut() f64 path has a leftover
  `o = np.asarray(o, dtype)` inside the chunk loop — remove; it is also
  only a sketch: check shapes (Sk orientation, KQ include-diagonal) against
  the derivation in its docstring.
- P1b (1-2 windows, Metal): kernel design, three dispatches per layer:
  K1 chunk-local UT (grid heads x chunks): build KK/KQ/R (64x64 from
  128-dot rows), forward-substitution solve U, intra-chunk output part,
  write per-chunk (S_in contribution excluded) o_partial + the chunk feed
  matrix F = (U * exp(gc_C-gc))^T K (128x128).
  K2 state pass (grid heads, sequential over T/C chunks, reads F_c):
  S_c = exp(gc_C) S_{c-1} + F_c; stash S_{c-1} per chunk for K3.
  K3 output combine (grid heads x chunks): o += exp(gc_t) (S_{c-1} q_t);
  final state store at g->spos semantics (see current scan kernel for the
  recurrent-state in/out layout + spos rotation).
  Sizes: chunk C=64 first; threadgroup memory per head-chunk ~64KB
  (K,Q,U,R tiles) exceeds 32 KB on M5-class => tile the solve (process q/
  v rows in 2 halves) or C=32. Decide by bench, not by argument.
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
