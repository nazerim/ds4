# PLAN-CAPTURE-PIPELINE — 2026-10-29
Target-feature capture → drafter retraining (DSpark fine-tune; DFlash2-ready dataset)

Origin: DSPARK-VISION-EVAL-20261028.md (§8 fidelity clean, §9 deep-ctx neutral,
fingerprint rule). This plan claims the remaining measured headroom:
toolcall decision-phase (~+27% structural territory, head is weak there:
accept 63-68%, avg_accept 0.19-0.20), cstruct/C-continuation (bypass band),
fresh prose. Baselines to beat — official Vision-Exp head: per-position
p1=.686 → p3=.417 (0731-era AUDIT numbers; Vision-Exp head measured engaged
87-90% overall but same weak classes), cstruct avg_accept 0.26, docrewrite 0.24.

## Stages

### 0. Schema & sizing (0.5d, no engine)
- Extend tools/gguf_meta.py with --dims; record exact shapes from
  gguf/DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf: main_proj.width
  (=3×hidden of taps 40/41/42), stage block shapes, markov (rank 256),
  confidence proj. block_size=5.
- Record spec (per token position):
  token_id, fp8(hidden40 || hidden41 || hidden42), labels(next5 sampled),
  accept_bits(verifier outcomes), meta{temp, thinking, dsml_boundary, conv_id}
- Sizing: ~12-15KB fp8/token; target corpus 20-30M tokens = 250-450GB on
  FireCuda520 (1.0-1.1TB free; cleanup job scheduled at stage-4 completion).

### 1. Engine dump mode (1-2d C; the only engine hot-path touch)
- Env: DS4_TRAIN_DUMP=<dir>; activates a snapshot->ring-buffer->writer-thread
  tap at the EXISTING per-cycle target-feature snapshot (dspark already
  captures layers 40/41/42 every speculative cycle; stats field `snapshot=`).
- Drop-on-backpressure (never block decode; counter logs drops; gate <1%).
- Prefill-tail and decode-step records both captured; outcome bits come from
  the verifier (accept/reject/truncate already computed per cycle).
- Key property: features captured from OUR mixed-quant GGUF at runtime =>
  quant-aware training by construction (matches inference statistics exactly).
- Output: rotating shards (safetensors or raw bins + jsonl index), 512MB each.

### 2. Corpus collection (1-2 weeks passive, concurrent with normal use)
- Production traffic = the target distribution (opencode agentic: code,
  thinking chains, DSML emission + decision points).
- Top-ups for starved classes: C-continuation corpora, DSML template/decision
  variants (5-10M tok) so class coverage is non-trivial in holdout.
- Entry gate for stage 3: one week of dumps contains all behavior classes;
  feature checksums stable across restarts; drop-rate <1%; PII stays
  blade-local (NEVER commit dumps; treat dir as secret-grade).

### 3. Trainer (2-3 weeks; the big piece)
- PyTorch mirror of the runtime graph. Ground truth = ds4.c binding code
  (dspark_weights_bind_optional) + PLAN-DSPARK-PERF architecture notes.
  Components: 3 stage-blocks (attn w/ q_sinks + routed/shared MoE + HC
  attn/ffn mix), main_proj/main_norm, hc_head_{base,fn,scale},
  markov_w1/w2, confidence_head.proj.
- FINE-TUNE FROM OFFICIAL WEIGHTS (dequantize the 5.6GB support GGUF -> torch),
  small LR, hours/run (0.6B-class head deltas). Objectives:
  a) stages: teacher-forced CE vs target's next-5 sampled tokens (pos-decayed)
  b) markov head: transition prediction on captured sequences
  c) confidence head: BCE on recorded accept/reject bits, calibrated so
     P(accept|thr=0.6) matches the 0.6 operating point (and sweepable).
- HOLDOUT GATE: per-position acceptance on held-out traffic must reach
  p1 >= .75, p3 >= .55-.60 (vs official .686/.417). IF NOT: architecture is
  the wall, not the data -> same dataset pivots to DFlash2-lattice trainer
  (block-diffusion body + 2M path selector + two-tap convs; data unchanged).

### 4. Repackage & evaluate (2-3d)
- Writer: torch tensors -> support-GGUF (schema known: mtp.* names, align=32,
  Q8_0/Q4_K/F32 dtypes, 12 metadata keys incl. variant/revision — for OUR
  fine-tune, set general.source.revision to a new training-tag; check
  support_model_checkpoint_compatible() still passes (pin is on TARGET
  revision match via checkpoint_variant... verify whether support revision
  string itself is compared: ds4.c pins VISION_EXP_REVISION constant —
  a custom support file will need either matching revision string or a
  DS4_ALLOW_CUSTOM_SUPPORT escape. Resolve in stage-4 day-1.)
- Evaluation = the existing suite: make dspark-acceptance (guard thresholds
  rebaselined), tests/dspark_traffic_probe.sh, tests/dspark_temp_battery.sh,
  phase2.5 replay. Ship gate: net tg gain >= +3% on mixed traffic with
  fidelity >= official head (byte-identity rate re-check at temp 1.0).
- Fingerprint note: model_fp keys on engine CONFIG, not drafter weights
  (hypothesis from 2026-10-28 toggling; confirm at swap: fine-tune on same
  flags should NOT trigger ladder-rebuild tax).

### 5. Reuse (why the data is architecture-agnostic)
Same (token, feature, outcome) records train: DSpark fine-tunes, DFlash2
lattice+selector+conv, and future heads. Capture once, iterate drafter
families indefinitely. This is the compounding argument for doing stage 0-1
even if stage 3 pivots.

## Cost
Stage 0: 0.5d | 1: 1-2d | 2: 1-2wk passive | 3: 2-3wk | 4: 2-3d => ~3.5-4 wk
total focused effort, one engine-hot-path touch, no new kernels unless pivoting
to DFlash2 body (+1-2wk kernels, dataset already banked).

## Risks / open questions
- fp8 capture vs runtime bf16 feature fidelity: verify loss delta stage-3 wk1.
- Whether markov/confidence signals need full logits vs accept bits: start
  with accept bits + CE on stage logits (cheap), add logits capture only if
  calibration underperforms.
- Server concurrency (batched interleaves): capture must tag conv_id per
  record (already in spec) and prefill-chunk boundaries must not corrupt the
  next-5 label windows (mask records near turn ends).
- Blade write budget: dumps + two kv ladders (403GiB) share the volume;
  monitor; cleanup job mandatory at stage 4.
