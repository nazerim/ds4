# PLAN-TRAINER-20261101 — Stage-3 drafter-retraining state after night run #1

Anchor for cold resumption of the retraining workstream. Produced by the
autonomous night run 2026-10-31 23:45 -> 11-01 (engine untouched: PID 96888
soaked `active/` all night; collection still live at morning).

## What the night run established (all verified, not assumed)

### A. Head architecture — REVERSE-ENGINEERING CLOSED
`gguf/DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf` (81 tensors, ds4 GGUF
dialect) fully decoded and cross-validated three ways (inventory extraction
== engine bind list ds4.c:8740-8810 == layout validator ds4.c:6296+):

- 3 stages (block_size 5, markov_rank 256, target taps layers 40/41/42,
  noise_token 128799, revision e46e16bf..., variant vision-exp)
- per stage: HC mixing (hc_attn/hc_ffn: base[24], fn[24, 4096x4streams],
  scale[3]) -> MLA-ish attn (q_a 4096->1024 + norm, q_b 1024->32768,
  kv 4096->512 + norm, 64 head sinks, output low-rank 8192) -> MoE (router
  [256,4096] + bias, shared expert 2048, 256 routed experts)
- specials: stage0 `main_proj [4096, 12288]` (3-tap concat -> hidden) +
  `main_norm`; final stage `norm`, hc_head (4-stream), `markov_w1/w2
  [129280, 256]`, `confidence_proj [1, 4352]` (4096 hidden + 256 markov ctx)
- dtypes: f32 norms, f16 hc fn, q8_0 projections/attention/markov,
  q2_k (down) + iq2_xxs (gate/up) expert stacks = 5.44 GB native
- extraction: 72 non-expert tensors -> **f32 2.07 GB** at
  `/Volumes/FireCuda520/ds4-train/tensors-20261101/mtp_nonexpert_f32.npz`
  (+ extract_report.json with per-tensor stats). q8_0/f16/f32 dequant
  verified by magnitude sanity (main_proj absmax .25, norms ~.2, markov
  std .22).

### B. Trainer scaffold — `trainer/` (uv project, torch 2.15 MPS)
- `dspark/data_contract.py` — single source for the 12,320 B record format
  (header `<IiBBBB5i`, fp8-e4m3 feature decode in numpy, control ids)
- `dspark/records.py` — streaming headers + mmap feature reader
- `dspark/dataset.py` — segment assembly: flags==1 primary, pos-break split
  fallback (catches day-1's 2 unhooked adoptions -> 44 segments not 42);
  shared drop rule n_label==0; day-level split_manifest()
- `dspark/model.py` — torch mirror, `load_npz()` populates all 72 tensors via
  the explicit GGUF->module map; TRAINABLE set (grad-enabled) deliberately
  small: main_norm, final norm, markov_w1/w2, confidence_proj. forward()
  NotImplemented pending engine-semantics anchoring (below).
- tests: **9/9 green**, encoding known corpus defects as assertions
  (day-1 counts 27,371/153/44; day-2 82,536/97/97; chain + labels0==seed
  global; fp8 decode magnitude gates)
- NOTE: 117,421-banked + overnight active — rerun `python3
  tools/td_validate.py /Volumes/FireCuda520/ds4-train/active` at banking;
  expected fresh segment counts shift test_dataset numbers if day-boundaries
  move — tests pin banked dirs only, safe.

### C. Baseline proxies — `trainer/BASELINE.json`
Per depth-band from banked corpus (pos-gap proxies, skip-merged):
```
shallow (0-16k):  p_ge1 ~0.81-0.83  p_ge3 ~0.39-0.43  tok/cyc ~3.8-4.0
16-64k:           p_ge1 ~0.85-0.92  p_ge3 ~0.41-0.64  tok/cyc ~4.0-4.3
64-128k:          p_ge1 ~1.00       p_ge3 ~0.89-0.92  tok/cyc ~4.8-4.9
128k+  (day2):    p_ge1 0.997       p_ge3 0.879       tok/cyc 4.73
128k+  (active overnight THINKING-heavy): p_ge1 0.864 p_ge3 0.482
engagement density ~0.224 prepared cycles/token
```
CAVEAT (written into BASELINE.json): selection bias — the scheduler
prepares cycles it expects to succeed, so p_ge inflates in bands where
bypass dominates. Deep bands' ~1.0 is "prepared cycles succeed", NOT
"drafting everywhere". Retrain target framing: raise p_ge3/p_ge1 at the
SHALLOW bands and hold deep; measure honestly via engine batteries.

### D. Repack substrate — `tools/gguf_write.py`
ds4-dialect typed GGUF writer; **byte-identical round-trip of the official
6.0 GB support file** (sha 0807a67fd9ce5874..., 12 kvs, 81 infos, alignment
32). Stage-4 writer = this + f32/q8_0 quantizers for replaced tensors.

## Decisions taken autonomously (segways)
1. Experts FROZEN for v1 fine-tune (q2_k/iq2_xxs dequant deferred; 5.44 GB;
   forward either needs a dequant kernel or per-expert f32 materialization —
   day-work, codebook tables live in ds4.c:1169-1250 (iq2xs grid + ksigns)).
2. Trainable set = markov + confidence + norms (see model.py). Rationale:
   fine-tune targets = exactly the plan's objectives (stage-CE via markov
   path, confidence BCE @0.45); attn/MoE bodies are the 5.99 GB risk of
   overfit on ~200k cycles. Expand later if gates stall.
3. fp8 capture question: BASELINE shows labels/chain flawless in fp8 regime;
   f16 capture switch (ds4.c edit, 1-line feat width + +50% storage) queued
   for next engine window as a *measurement*, not a blocker: first training
   passes can proceed on fp8 with jitter augmentation if loss plateaus.
4. `trainer/.venv` + npz cached tensors on blade; scratch copies under
   /private/var .../T/mtp are transient.
5. Revision-pin escape (Stage-4 blocker list): support_model_checkpoint_
   compatible() pins e46e16bf... — custom repacks must rewrite
   `general.source.revision` + `checkpoint_variant` to pass or use env
   escape (design: DS4_DSPARK_ALLOW_CUSTOM_SUPPORT, engine edit, window).

## Next actions (ordered)
0. CAPTURE v1.3b (11-01 00:3x window): CLI re-verify found the v-pointer
   backstop FALSE-SPLITTING checkpoint reallocations (3 segments per 1 CLI
   conv). Clause removed; boundary = pos-reset only (server adoption sites
   are hooked directly; missed swaps still trip validate's labels0==seed
   canary). Re-tested: 1 conv -> 1 segment, 30 recs, chain 100%. Live
   server 2559 runs the fixed image; day-4 (v1.3) unaffected — server
   segments matched requests exactly (234/234).
1. Morning: bank `active/` (overnight size unknown — validate first) ->
   day-4 archive; re-run baseline with it included.
2. Anchor forward() semantics: read ds4.c dspark draft path
   (prepare_impl ~79700-79990; stage forwards + hc mixing math) -> mirror
   forward in torch -> PARITY TEST vs engine: needs ONE future window —
   DS4_TD_DEBUG-style dump of per-stage intermediate for a fixed input, or
   simpler: parity at the OUTPUT level (confidence p + draft token ids) via
   CLI --dspark run vs torch mirror on the same captured record.
3. Training loop v0: stage-CE (predict labels[1..4] via markov-conditioned
   head), confidence BCE calibrated to commit-rate at 0.45; small-batch on
   day1+2 train split (MPS). Loss curves vs BASELINE proxies.
4. iq2_xxs/q2_k numpy dequant port (codebooks already located) + expert
   f32 materialization decision (6.4 GB RAM ok on MBP? else kernel dequant).
5. Window queue UPDATE 11-01 03:0x (granted night window): revision-pin
   escape DONE+TESTED (ds4.c support_model_checkpoint_compatible:
   DS4_DSPARK_ALLOW_CUSTOM_SUPPORT=1 bypasses ONLY the vision-exp variant/
   revision match; family guards intact; default rejects patched-revision
   clone, escape loads it missing=0 invalid=0 and generates). Stage-4
   repack path now has a working install story.
   STILL OPEN: parity dumps; deep-ctx-with-tools bounding re-verify (script
   patched tests/dspark_bounding_pass.py -> now sends tools[] payload; run
   deferred until GPU sweep chain finishes to keep timing clean);
   fp8-input jitter experiment.
6. f16-capture decision -> DEFER fp8->f16 switch. Evidence: fp8 features
   supported top1 .401 + conf AUC .785; no quality crisis exists. Revisit
   only if the 2x-data scaling curve flattens unexpectedly early.

## Hygiene
- ALL night-run files are NEW (trainer/, tools/gguf_write.py, this doc);
  zero edits to ds4.c/ds4-server.sh this night; nothing committed (per rule).
- Collection contract docs (CAPTURE-SCHEMA.md) unchanged — verified accurate.
- Corpus at 11-01 morning: 202,186 banked + overnight active (~90k+ expected
  at last check 23:59). Blade free ~857 GB, capture pace ~1 GB/h heavy-use.

## Milestone-2 kickoff notes (Nov-03, mirror TRUNK replay)
Architecture facts pinned from engine + tensor inventory:
- Draft trunk = 2 full MTP transformer layers (mtp.0/mtp.1: MLA attn w/ sinks,
  MoE FFN 128 routed experts top-3 + shared expert) + mtp.2 final stage
  (hc heads -> the 5x4096 conf rows; markov w1/w2; confidence_proj; final_head).
- Engine dims hard-guarded (ds4.c ~8844): n_experts=128 used=3, stages=3,
  block=5, markov_rank=256, target_layer_count=3.
- KEY: mtp main_proj is 4096x12288 = 3 taps of target layers 40,41,42 =
  EXACTLY the capture's 12,288B feature row. Cycle input lives in .recs (fp8);
  parity dump's conf rows are the post-trunk intermediate (f32).
- Expectation setting: fp8 jitter (measured acceptance-neutral) perturbs the
  trunk input -> mirror-vs-engine PROPOSAL match will asymptote ~97-99%, not
  100%; conf-ROW match (f32 vs f32) is the byte-tight check for the trunk math,
  proposal argmax the functional one. Define both gates before iterating.
- Expert materialization: 2 layers x 128 experts f32 ~= 26 GB (blade fits;
  q2_k/iq2_xxs dequant ports already bit-exact-tested).
Remaining inputs to locate (next work): batch-kernel per-slot INPUT assembly
how embed(prev-proposal) mixes with the h taps per stage (read spec-batch
input build ~ds4.c 27800-28070 + mtp weights->input path), rope/pos handling
across the 5 slots, and final RMSNorm placement (logits head vs conf rows).
Milestone-1 (conf-gate parity) PASSED Nov-02: 0.033% violations on s1.

## Milestone-2 session 1 findings (Nov-03 01:4x, code-read, engine-free)
Trunk mechanics pinned from ds4.c 34832-35000 + 80140-80240:
- Per cycle the GPU runs ONE batched MTP forward over rows = block+1 = 6:
  row0 = target (h[pos-1] taps, position pos-1); rows1..5 = draft slots with
  ids = [seed_token(t[pos]), noise_token_id x4] (DSpark is parallel-MTP, NOT
  autoregressive inside the batch; the sequential token conditioning lives
  entirely in the markov+conf chain we ALREADY mirrored in milestone 1).
- position_ids per row: [pos-1, pos, pos+1, ..., pos+4] (rope across the 5
  slots is a fixed offset ladder - simple to reproduce).
- hc plumbing: stage_input_hc rows are DS4_N_HC(4) x 4096 = 16384/row;
  hc_attn_fn/hc_ffn_fn (24,16384) = hyper-connection mixers per mtp layer;
  conf rows = the batch FFN current buffer (hc_head outputs, pre-final-norm).
- Mirror inputs per cycle are ALL available offline: h-taps (recs 12288 fp8),
  seed token + pos (parity header), positions derived, noise id (dw meta).
  MISSING dependency: base token_embd rows (129280x4096 in the MAIN gguf,
  not in mtp_nonexpert_f32.npz) - need extraction of embedding rows (or full
  table 2.1 GB f32) as the next material step.
- Still to read next session: stage_input_hc FILL kernel (how 3 taps+embed
  land in the 4 hc streams per draft row; ~35000+ gpu kernel args), the
  ring/lineage shift of hc rows across cycles (mirror needs cross-cycle
  state! check if hc rows are cycle-local recomputed or shifted from prev -
  if shifted, mirror must replay cycle sequences, not isolated cycles), and
  dspark_raw_cache/q/k/v batch attention over the draft-cache rows
  (attention sees a HISTORY cache - initial_cache_rows from capture merge;
  the draft cache = h-history! likely the trunk attends over the conversation
  h-cache, meaning full-context replay needed for byte-exact logits).
IMPLICATION: exact proposal parity may require reconstructing the draft
attention cache state - milestone-2 should START with conf-row parity on
ISOLATED first-cycles (cold-start conversations, cache depth 1) where history
= just h[pos-1], then extend. s2/s3 era captures of SHORT new conversations
are the ideal dev set (controlled C-session tonight gives exactly this).

## Milestone-2 session 2 (Nov-03): MATERIALS COMPLETE
- token_embd extracted from the 97GB base GGUF -> blade
  tensors-20261101/token_embd_f16.npz (129280x4096 f16, 1.06 GB). Plain f16,
  offset 88,794,335,232, alignment 32 (row-major [rows,4096]).
- Support-file metadata confirmed: noise_token_id=128800-1? NO: 128799,
  target_layer_ids=[40,41,42], stages=3, block=5, rank=256.
- Gotcha: raw GGUF header walking needs the STANDARD size table
  (0=u8,1=i8,2=u16,3=i16,4=u32,5=i32,6=f32,7=bool; 8=string{u64 len,bytes};
  9=array{etype,u64 n}); a wrong table desyncs into 97GB of garbage (one
  wild u64 read = MemoryError - guard array counts).
- Input assembly confirmed from ds4.c 35000-35032: row0 = rmsnorm(main_proj
  (taps)) replicated x HC streams; rows1..5 = token_embd[seed/noise...]
  replicated; positions [pos-1, pos..pos+4]; stage0 path applies
  stage0->main_proj+main_norm (ds4.c 35090+).
NEXT (implementation session): read metal kernel semantics for (a) hc mixers
(hc_*_fn (24,16384) parameterization: how 24 coeffs split across stream
in/out/residual weights), (b) attention over stage cache for first cycles,
(c) MoE top-3 routing incl exp_probs_b bias handling; then write
dspark/mirror_trunk.py forward + validate conf rows (f32 exact) on s3
first-cycle dev sample (pid43176 202 cycles + pid48230), join recs<->parity
by (pid,pos). Expert tensors (128/layer x2) materialization = 26 GB f32 or
on-demand dequant via gguf_dq (bit-exact, ports tested).

## CORRECTIONS to session-1/2 notes (Nov-03, independent review ses_e607ce649)
The notes above contain three WRONG facts, corrected here (rev1 handoff killed
the same): (1) trunk = THREE full transformer stages (mtp.2 is complete
attn+MoE + heads), not "2 + machinery"; (2) experts = **256 routed / top-6
+1 shared** for deepseek4-dspark (vision-exp) - the 128/top-3 guard at ds4.c
~8844 is V4.1-only and INACTIVE for this model (npz gate [256,4096] agrees);
(3) full f32 expert materialization ~= 77 GB (25.8/stage x3), not 26 GB -
prefer on-demand top-6 dequant via gguf_dq. hc_head_* exists ONLY on mtp.2
((4,16384)/(1,4)/(1,1)); n_hc=4 from ds4.c:626. Metadata validator is at
ds4.c:6199. NEW CRITICAL: mtp stage cache is PREFILL-SEEDED (80157) - exact
first-cycle replay may need capture extension; see
MILESTONE2-HANDOFF-20261103.md §2 (resolution path a/b/c) before coding.
