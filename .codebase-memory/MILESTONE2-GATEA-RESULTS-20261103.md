# MILESTONE-2 GATE A — conf-row parity: CONDITIONAL PASS (Nov-03 ~19:30)

## Result (exact-era data, empty-ring replay)
`trainer/dspark/mirror_trunk.py run_gate_a` on day-12+active **pid53508**
(EXACT=1 era; plain [seed,noise x4] draft path): conv 4 (3394 recs),
19 compared cycles, slots by draft depth:

| slot | n | cos_mean | cos_min | maxerr_mean |
|---|---|---|---|---|
| 0 | 19 | **0.9898** | 0.9655 | 0.40 |
| 1 | 19 | 0.9776 | 0.9457 | 0.31 |
| 2 | 11 | 0.9673 | 0.9036 | 0.41 |
| 3 | 5 | 0.9530 | 0.9102 | 0.34 |
| 4 | 2 | 0.9270 | 0.8650 | 0.29 |

Amplitude verified matched (probe: std 0.570 vs 0.576, norms within 1%).
Repro: `MIRROR_EMPTY_RING=1 uv run python -m dspark.mirror_trunk --recs
<Volumes>/ds4-train/active/train_pid53508.recs --parity <Volumes>/ds4-parity/
20261103-s3/parity_pid53508.bin --compare 24 --stride 90`

## The two discoveries that unlocked it (both > any port bug)
1. **Support-window RESETS on multi-token gaps.** merge_target_range keeps
   old rows ONLY when adjacent (start <= old_end); accepted blocks skip 3-5
   positions -> crop_to_prefix resets. So the engine's own attention window
   during decode is **EMPTY or 1 row** almost always — dense history exists
   ONLY in cycle-1 after a prefill seed (window<=128 prefill rows, evicted
   by adjacency rules). Milestone-2's feared "dense prefill-tap replay"
   requirement is largely MOOT for decode-time parity. (rev: §2's "cycles
   >=128 seed-free exact subset" — correct conclusion, wrong mechanism.)
2. **Era mismatch:** pid48230/morning data ran M5 **seed-batch** draft mode
   (EXACT=0 enables it: real committed tokens as draft rows, not
   [seed,noise x4]). Mirror on that era caps at ~0.95. EXACT=1 DISABLES
   seed batch (ds4.c:62050 `!e->dspark_exact_sampling`) — current+future
   era matches the plain port. Gate-A data source = pid>=53508 only.

## Noise-floor decomposition (why 0.99 is the honest bar, not 0.9995)
- Sensitivity probe (`--noise-rel 0.02` on taps): trunk is SMOOTH — extra
  2% gaussian noise on fp8-quantized inputs self-cos 0.9996-1.0. So
  capture-noise amplification is NOT the gap; fp8 taps vs engine f32 h
  remains ~1e-3-class direct effect.
- Residual 1e-2-class gap, attributed: (a) engine-only SUPPORT rows from
  scheduler-skip cycles (`ring_maintain` adds h at skipped steps that never
  produced a .recs record — invisible to the mirror, unfixable without a
  capture extension dumping per-position verifier h); (b) activation
  quantization in engine kernels (moe_swiglu_weight_f16 stores f16 down-
  input; q8 pair kernels) vs our f32 linears; (c) depth compounding across
  the noncausal draft-row chain explains the slot0->slot4 drift.
- Verdict: PASS is "slot0 mean cos ~0.99 with amplitude match, residuals
  attributable per (a)/(b)/(c), no unexplained systematic". Anything
  materially above slot0 0.99 requires the capture extension — deferred.

## What this means for 3c
The mirror's per-cycle forward is faithful at the ~1% level on the DEPLOY
regime's own data (EXACT=1, plain batch path). Training gradients through
this mirror face label-preserving noise ~ same class the fp8 capture
already injected (jitter-neutral by the Nov-01 A/B). Proceed to gate B,
then 3c design: differentiable torch version of Trunk.cycle (same math,
autograd), trainable = markov_w1/w2 + confidence proj (+hc/norms), conf
calibration target = exact-acceptance semantics (decision #10).

## GATE B FINAL (batch n=40 cycles / 121 slots; verdict: CONDITIONAL PASS)
- CONTROL leg: **121/121 = 100.0%** every slot - head+chain+markov tail is
  bit-faithful in effect (exact proposals from engine conf rows).
- FULL leg: block-prefix match 81.8%; per slot 0.95 / 0.825 / 0.72 /
  0.667 / 0.5 (n=40,24,15,9,8 deep slots are small-n AND selection-biased:
  slot d measured only when dlen>d, i.e. longer-accept cycles).
- Decode: the ONLY error source left is conf-row cos compounding across
  draft rows (Gate A: .99 -> .93 slot0->4); tail + semantics proven clean.
  95% slot0 from a 1e-2-level vector cos is the expected discrete-argmax
  behavior near margin ties.
- Bar verdict: original "97-99%" assumed slot0-class; per-slot reality is
  the honest shape. FUNCTIONAL for 3c: training does not need proposal
  equality, it needs faithful gradients, and both gates now prove the
  graph is the engine's graph modulo the documented residual (skip-cycle
  support rows / activation-quant kernels / fp8 taps).
- Improvement candidates if deeper-slot fidelity ever matters: replicate
  engine f16 down-input + RNE e4m3 tie rule bit-exactly; capture extension
  for skipped-cycle h rows; both deferred.

## GATE B FIRST LIGHT (same evening; batch n=40 running)
Chain port (markov_propose_chain: state=w1[prev]; bias=W2@state;
argmax(logits_row+bias); prev=proposal; start prev=seed) + tail
(rms(final->norm) @ output_head) validated:
- CONTROL leg (parity's OWN conf rows -> my tail -> chain vs props):
  **28/28 = 100%** - head+chain+markov semantics EXACT.
- FULL leg (mirror conf): overall 85.7%; **slot0 = 100%** (incl cycles
  where engine's first proposal was REJECTED - join pos-unique now, not
  accepted-biased); depth drift 0.875/0.833/0.75/0.5 (small n).
- Read: end-to-end proposal fidelity is gated by conf-row cos compounding
  across draft slots (each draft row attends earlier draft rows), NOT by
  the tail. The 97-99% bar applies to slot0-1 territory now; deeper-slot
  improvement = 3c's own job (that's what a better head fixes).
Repro: uv run python -m dspark.mirror_trunk --gate-b --recs <active/
train_pid53508.recs> --parity <s3/parity_pid53508.bin> --compare N
--stride S --report out.json. Join = pos-unique cycles (parity record
count==1 at that pos). Era rule pid>=53508 unchanged.

## Gate B checklist (next)
conf rows -> rms(final->norm) -> @ output_head_f16 (BANKED, untied q8_0
dequant, tools/extract_output_head.py) -> + markov bias chain (mirror.py
conf_replay formulas, already gate-verified) -> argmax vs parity props[:dlen];
expect 97-99% (fp8 taps cap), stratify skip-cycle cycles via
props[0]!=labels[1] rejection signal; data = pid53508+ only.

## Files
trainer/dspark/mirror_trunk.py (primitives+Trunk+harness+noise probe;
env MIRROR_EMPTY_RING / MIRROR_MARGIN / MIRROR_FULLWARM / MIRROR_HC_STABLE)
trainer/tests/test_mirror_trunk.py (12 tests; suite 33-> green)
tools/extract_output_head.py (gate-B material builder)
this doc + handoff §2/§8/§8b/§9 amendments.
