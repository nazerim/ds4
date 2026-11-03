# MILESTONE-2-HANDOFF-20261103 (rev2, code-review folded) — trunk replay knowledge + plan
Cold-start read order: THIS FILE -> DSPARK-STATE-20261102.md (live state/
ledger/gotchas) -> PLAN-TRAINER-20261101.md (milestone-1 result + sessions
1/2) -> CAPTURE-SCHEMA.md -> this file's rev1 was independently reviewed
(ses_e607ce649); every P0/P1 below is post-correction and EYE-VERIFIED.

## 0. MISSION
Chain: 3a paper (DONE: s1 sealed 65,536 cycles @81,952B + s3 live ~96k and
growing - RECOUNT AT USE TIME, per-process cap 200k each) -> 3b mirror =
milestone-1 DONE (conf-gate replay, 6/18,424 = .033% violations) +
**milestone-2 = trunk replay (THIS)** -> 3c train head through mirror
(Path A markov+conf, Path B h-read) -> Stage 4 repack/swap.
BAR: cos32clean2 = **top1 .4529 / top3 .5950** (324,081 clean pairs, day-3
holdout, TRAIN_V1_cos32clean2.json). Never compare across pool definitions.

## 1. ARCHITECTURE (CORRECTED - the two rev1 errors killed by review)
- Model: DeepSeek-V4-Flash-Vision-Exp; support GGUF
  general.architecture = **"deepseek4-dspark"** (NOT deepseek41-dspark).
  => the ds4.c ~8844 block (128 experts/top-3/layers 37-39) is **V4.1-ONLY
  and INACTIVE here**. Runtime shape = MAIN model:
  **256 routed experts, top-6, +1 shared expert** (ds4.c:617-619 FLASH
  defaults; npz confirms mtp.N.ffn_gate_inp [256,4096], exp_probs_b [1,256];
  extract_report native_kept shows expert stacks dims [4096,2048,256] for
  ALL THREE stages).
- Trunk = **THREE full transformer stages** mtp.0/mtp.1/mtp.2, each =
  HC mixers + MLA attention + MoE FFN. Engine loops stages 0..n_stages-1
  through the batch forward (ds4.c 35914/36020; binding loop 8858).
  mtp.2 additionally carries the head pieces: hc_head_{fn (4,16384),
  base (1,4), scale (1,1)} -- ONLY on mtp.2 -- markov_w1/w2 (129280,256),
  confidence_head.proj (1,4352), norm (1,4096).
- Conf rows = metal_graph_batch_ffn_cur (ds4.c 36923) = stage-2 FFN-current
  buffer, PRE final shared norm. Logits rows = spec_logits per stage slot.
- Per-cycle batch (fill kernel ds4.c 35000-35032; stage0 setup 35036-35095):
  6 rows x hc_dim(=n_hc 4 x 4096 = 16384); n_hc=4 from ds4.c:626 shape table
  (extract_report does NOT answer it);
  row0 = rms_norm(main_proj(taps12288)) replicated x4 streams;
  rows1..5 = token_embd[ids] replicated x4, ids = [seed=t[pos], noise x4],
  noise_token_id = 128799 (support meta CONFIRMED);
  positions = [pos-1, pos..pos+4] (rope ladder 35055-58); mtp attn has NO
  rotary tensors in npz -- check whether rope reuses base config or is
  absent (attn_sinks (1,64) exists per stage).
  stage_input_hc = row0 ++ rows1..5 contiguous copies.
- Markov+conf chain: ALREADY mirrored (trainer/dspark/mirror.py; formula
  verified vs CPU refs dspark_apply_markov_greedy_probe 36850,
  dspark_markov_q8_0_argmax 36634: score = logits[row] + w_row . state;
  conf = proj . (hrow ++ w1[prev]); sigmoid_stable >= thr gate. Metal
  falls through to CPU proposal path (37070) -- proposals CPU-reproducible.

## 2. CRITICAL OPEN ISSUE #1 — **RESOLVED Nov-03 ~17:00 (code evidence below)**
The stage cache is WINDOWED, cycle-granular, per stage (ds4.c read in full):
- merge per draft cycle = ONE row, at feature_pos (36002: "Draft rows remain
  temporary; only the committed target feature joins the history"). Ring
  maintain on scheduler-skip also adds just h[pos] (36010+).
- logical window <= DS4_N_SWA-1 = 127 rows by position order, ring of
  dspark_cache_cap (=raw_cap 4352 rows, pos%cap); crop+cap via
  cache_target_prefix (29691+), merge_target_range (29671+).
- PREFILL SEED (35352): only <=128 trailing prefill rows projected+written
  ("earlier rows would be hidden"), per stage, at batch eval when
  captured_batch_end == pos (first cycle). Every later cycle merge evicts
  one seeded row -> after ~127 cycles the window is PURE CYCLE FEATURES.
=> REPLAY PLAN (no engine change): (i) EXACT mode on cycles >=128 of convs
   >=128 cycles (long soak convs: day8/9/12 have 2k-5k-record convs; check
   per-conv cycle counts in MANIFESTs / idx); window rows = the SAME conv's
   previous records' taps in .recs (fp8->f32 dequant = same designed noise
   as row0). (ii) EARLY cycles: run mirror seedless vs parity conf rows ->
   the delta IS the quantified seed contribution (this replaces the old
   ambiguous 'probe'); grade early cycles by RELATIVE gates only.
   (iii) Optional later (window+tiny): dump <=128 prefill h-rows per conv
   (~1.5MB) if byte-exact early-cycle replay is ever required.
>>> AMENDED SAME DAY by GATE-A findings (see MILESTONE2-GATEA-RESULTS-
   20261103.md; authoritative): the window is POSITION-ADJACENCY managed
   (merge_target_range keeps old rows only when start<=old_end; multi-token
   accepts break adjacency -> crop resets). DECODE-TIME WINDOW IS ALMOST
   ALWAYS EMPTY; dense history exists only in cycle-1 post-prefill. Mirror
   default = MIRROR_EMPTY_RING=1. GATE A CONDITIONAL PASS: slot0 cos_mean
   0.9898 on pid53508 exact-era (19 cycles), residuals attributed to
   scheduler-skip support rows (unrecordable), activation-quant diffs,
   depth compounding. CRITICAL ERA FACT: pre-EXACT data ran M5 seed-batch
   mode (real-token draft rows) -> gate comparisons ONLY on pid>=53508.
   Noise probe proves trunk smooth (2% extra input noise -> self-cos
   >=0.9996): 0.99-class is the honest bar, 0.9995 would need per-position
   h capture extension (deferred).
REMAINING TO READ for the forward (S2.1): attention context assembly over
window rows + the 6 batch rows (metal_graph_eval_dspark_stage_block 35891+,
noncausal probe 35900s, rope positions for cached rows, raw_cache layout
cap x N_HEAD_DIM x f32 - confirm N_HEAD_DIM=512 = MLA compressed kv?),
hc mixer math in batch encoder (35440-36100), per-stage q_a/q_b/kv projections
+ attn_sinks, stage0 batch eval (35384), final_hidden path.

## 3. MATERIALS (verified paths + the run book)
/volumes paths on blade; trainer = uv project (python3.14, numpy/torch/
pyarrow/pytest; venv exists):
  cd /Users/naz/Projects/ds4/trainer && uv run pytest -q   # 21 tests green
- mtp weights: tensors-20261101/mtp_nonexpert_f32.npz (72 tensors f32;
  names mtp.{0,1,2}.*; model.py GGUF_TO_MODULE maps 'mtp.N.suffix' ->
  'stages.N.mod'; load_npz populates all 72, pinned by test; model.forward()
  = NotImplementedError = THE GAP this milestone fills).
- embeddings: tensors-20261101/token_embd_f16.npz (129280x4096 f16,
  row-major rows of 4096; extraction REPRODUCIBLE via
  tools/extract_gguf_tensor.py --list/--name/--rows, byte-verified vs npz).
- lm head (NEW, gate B material): output.weight is UNTIED q8_0 [4096,129280]
  at gguf off 97,023,770,688 (4352 B/row = q8_0 34B/32el blocks per the
  ENGINE enum ds4.c:2330 ([8]=q8_0 - DS4 uses its own type numbering, NOT
  ggml std). tools/extract_output_head.py -> BANKED
  tensors-20261101/output_head_f16.npz (1.06GB, f16 rows of 4096, NaN-free,
  f32-verified std 0.175 / absmax 3.9). Gate B argmax agreement is the
  correctness backstop for head scale.
- experts: live in the **SUPPORT gguf** (NOT base): mtp.{0,1,2}.ffn_gate_
  exps/ffn_up_exps = **iq2_xxs** (engine dt 16), ffn_down_exps = **q2_k**
  (dt 10); dims [4096,2048,256] stacks; offsets via extract_gguf_tensor
  --list exps on the support file. gguf_dq.expert_slice_f32 already keys
  on dt 10/16 -> on-demand per-expert dequant works TODAY. Full f32
  materialization stays ~77 GB (25.8/stage) - on-demand preferred.
  ENGINE TYPE ENUM WARNING: ds4.c:2323 gguf_types[] is DS4's OWN numbering
  ([8]=q8_0, [10]=q2_k, [16]=iq2_xxs - NOT ggml-standard; row-stride math
  is the ground-truth check, 4352B/4096-row = q8_0). tools/
  extract_gguf_tensor.py DT_NAMES now matches. Shared experts (ffn_*_
  shexp) already f32 in the npz; routed bias exp_probs_b per stage;
  swiglu gate clamp +-10 confirmed for SHARED (glu.metal:33,58) - pinned
  routed-vs-shared clamp usage = impl checklist item.
- parity: s1 SEALED 65,536 cycles (+ backup ~/Backups/.../parity-s1/);
  s3: pid41277 16,234 cycles (day-11 era), **pid43176 1,726 cycles** (dev-
  capture era - the scripted first ~202 belong to the 5 known convs; filter
  by pos ranges from dev_capture), pid47769/48230 live+growing. Format:
  stride 81952 = <IiBBH(pos,seed,dlen,stages,rsvd) + 5xi32 props (>=dlen are
  ZEROS) + 5x4096 f32 conf-hidden rows (>=dlen are STALE - never compare).
  pos == checkpoint.len == recs pos. Banner is stderr only (no in-file).
- soak .recs: <IiBBBB5i + 3x4096 fp8 e4m3 taps (stride 12320, 12,288B);
  labels[1]=next token; decode via data_contract.decode_e4m3; day dirs:
  **20261029-day{1,2,3}**, 20261101-day{4..7}, 20261102-day{8,9},
  20261103-day{10,11}, active/ (day-12 ~78k+).
- dev prompts PERSISTED: .codebase-memory/baseline-thinking-20261102/
  dev_capture_20261103.py (the tmp original may vanish).
- quarantine: trainer/LOOP-QUARANTINE-20261102.json v3 + dspark/loops.py
  (segment depth>15; spans [lo,hi,kind,pid]: TRIP unconditional, BUDGET
  depth>=4; gen-geometry; pid-per-line; version-3 hard guard).
  Mirror grading MUST mask loop-era cycles: file_exclusions on the .recs,
  join (pid,pos) to parity records, report match stats split kept/excluded.

## 4. EXECUTION PLAN (rev2)
S2.0 Resolve §2 (read seeding/cache code; run the 6-row probe on pid43176
     dev cycles; decide (a)/(b)/(c)) -- DO NOT write the full forward first.
S2.1 Kernel reads (all in ds4.c -- ds4_metal.m grep 'dspark' = 0 hits;
     generic kernels live there as ds4_gpu_hc_*/embed_tokens_hc/repeat_hc):
     batch stage encode ~35440-36100 (ffn_batch router uses
     DS4_N_EXPERT/USED = 256/6; call site 35827), markov/conf 36530-37210,
     prepare_impl 80066-80660, layout kinds 6113, metadata validator
     **6199** (presence/range/order only - no 128/37-39 enforcement).
     HC mixer parameterization: hc_{attn,ffn}_fn (24,16384) + base(1,24) +
     scale(1,3) per stage -- read their use sites in the batch encoder to
     get the mixing math (24 = sinkhorn/params structure, n_hc_lowrank/
     sinkhorn_iter fields ds4.c:567-577).
S2.2 mirror_trunk.py: tensor load (reuse model.py), per-cycle input from
     (.recs taps fp8->f32, parity props/seed, emb npz, noise id, positions),
     3-stage forward, output 5x4096 conf rows + logits rows;
     validation vs s1/s3 parity: gates = conf-row cosine + max-err profiled
     against fp8-input-noise floor (establish empirically, don't chase 1.0);
     proposals = markov chain on replayed logits rows -> match rate vs
     props[:dlen] (CPU-path, ~97-99% expected).
S2.3 Scale to batches + stratify (§3 quarantine rule); write
     MILESTONE2-RESULTS doc; commit.
S2.4 Hand to 3c: same graph differentiable in torch = training substrate.
Rules: engine-free; do NOT touch ds4.c/ds4_server.c (except IF §2(b) capture
extension approved - that IS an engine change needing a window); s3 dirs are
APPEND-ONLY while live (pid 48230 serving now, parity capped per-process
200k); dumps never committed; ./ds4_test needs engine STOPPED; API key via
~/.config/ds4/desktop.key, never echo; strings-check binaries before
claiming features live.

## 5. LIVE STATE SNAPSHOT (Nov-03 ~12:0x)
PID 48230: escalation binary, budget 16384 + TRIPWIRE=1 (script defaults
ds4-server.sh:132/142), soak active/ + parity s3 armed. auth_proxy :8100.
Trip stats so far: 26 trips (periods 127/311/356/447), 4 budget rides
(2 measured legit 1.8-2.5x), escalation 0 (cross-request by design).
Termbench hazard noted: legit periodic CONTENT gets chopped - evals should
send thinking_tripwire:false. git: HEAD 2290b5d pushed to nazerim/main;
origin = antirez upstream (ahead 340, never push). Uncommitted AFTER this
rev2 lands: doc bundle only (see commit below) - trainer/tools code all in.

## 6. REV1 ERRORS KILLED (so they don't resurrect)
- "128 experts/top-3 hard-guarded" -> 256/top-6+shared (guard is V4.1-only).
- "2 MTP layers + stage machinery" -> 3 full stages.
- "~26 GB expert materialization" -> 77 GB f32 full / on-demand path.
- "first cycle => cache empty, history=6 rows" -> prefill-seeded (§2).
- validate_metadata "~61999" -> 6199.
- anchor chop lines 16286/16295 -> those are room-warning edges; real:
  ds4_server.c 16325/16333 (TRIP, with inline gen=), 16349 (budget).
- hc_head (24,16384) -> (4,16384) and ONLY on mtp.2.
- "324k-pair anchor .4529/.5956" -> .4529/**.5950**.
- pid43176 parity "202 cycles" -> 1,726 (dev subset = first scripted convs;
  filter by pos). "props stale beyond dlen" -> props are ZEROS, conf ROWS
  are stale.
- corpus glob 2026110* -> day1/2/3 are 20261029.

## 7. S2.1 FORWARD MAP (Nov-03, read from ds4.c in-session; line refs current)
Stage block per cycle (metal_graph_eval_dspark_stage_block ds4.c:35468):
- feature_pos = pos-1; 6 rows (target ++ 5 drafts); visible = window(<=127)
  + 6, contiguous ring slots [raw_start .. append_pos+6), cap=raw_cap=4352,
  wrap via %cap. Chain invariant: cache ends_at(feature_pos) (35899).
- shared front (all 6 rows): rms_norm(stage_input_hc) -> hc_attn_fn
  (24,16384) -> 24 mix coefs = [2*n_hc | n_hc^2] -> sinkhorn(4x4, 20 iter,
  hc_eps) + base(1,24)+scale(1,3) -> weighted stream sum -> attn_norm.
  TARGET row's attn_norm = dspark_main_x (copied, computed once at stage0 -
  "shared by all support stages", 35686).
- q path (DRAFT rows only): attn_q_a (4096->1024) -> q_a_norm -> attn_q_b
  (1024->64x512) -> per-head rmsnorm -> rope_tail(start pos, per-row +1).
- kv path (6 rows): attn_kv (4096->512) -> attn_kv_a_norm -> rope_tail row0
  @feature_pos / drafts @pos -> dsv4_fp8_kv_quantize (512-dim, N_ROT=64!)
  -> store ring at append_pos. CACHED ROWS ARE FP8-QUANT K/V MLA ROWS -
  mirror must replicate the quantizer exactly (kernel: dsv4_fp8_kv_quantize
  in ds4_metal.m; also seed path 35287-35341 projects prefill rows the same
  way -> first-cycle seed replay NEEDS prefill h-taps (absent from .recs);
  cycles >=128 are seed-free => EXACT subset exists today).
- attention: NONCAUSAL over visible_rows, 64 heads x 512 raw kv + per-head
  attn_sinks(64 scalars); output rope_tail(inverse=true) to un-rope.
- out proj: attn_output_a/b grouped lora (8x1024, q8_0 kernels), hc_expand_
  split (44934 kernel) -> after_attn_hc streams (residual-mixed).
- FFN (33078): hc_ffn mixer (same 24-coef sinkhorn) -> ffn_norm -> router:
  ffn_gate_inp (256x4096) logits + ffn_exp_probs_b bias (1,256) ->
  router_select kernel ds4_metal.m:40388 top-6, weights x
  DS4_EXPERT_WEIGHT_SCALE=1.5 (shape .expert_weight_scale line 630) ->
  experts gate/up/down (n_ff_exp 2048; q2_k/iq2_xxs in base gguf - dequant
  via gguf_dq on-demand, 6/stage/cycle) + SHARED expert ALWAYS (gate/
  up/down shexp q8_0, swiglu) -> sum -> next stage (5 draft rows).
- final (after stage2, 36369): rms_norm -> hc_head_fn (4,16384) -> 4 coefs
  -> sinkhorn/weights -> mix streams -> batch_ffn_cur = CONF ROWS (5x4096,
  PRE-norm = milestone-1 binding confirmed) -> rms(final->norm) ->
  spec_logits SOURCE STILL TO READ (grep spec_logits in 36xxx/80xxx).
FLASH SHAPE CONSTANTS (ds4.c:601-640): embd 4096, head 64/1kv, head_dim
512, rot 64, out_group 8, lora_q/o 1024, experts 256/6+1shared, ffexp
2048, swa 128, hc 4 sinkhorn 20, eps fields rms_eps/hc_eps in table.
OPEN KERNEL READS (next session, all in ds4_metal.m): sinkhorn+mix exact
formula (ds4_gpu_hc_split_* 44934+), router_select softmax-vs-sigmoid +
renorm (40388), dsv4_fp8_kv_quantize layout (which dims quantized: kv
512-with-rot-64?), attention scaling (1/sqrt? sinks in softmax denom),
rope_tail inverse semantics + yarn params (freq_base/betas from shape),
hc_expand_split residual formula, spec_logits head path.
>>> ALL CLOSED by §8b + §9 below (kernel reads complete; lora group map =
contiguous 8-head chunks verified via heads_bytes=n_tok*8*4096@ds4_metal.m).

## 9. GATE A HARNESS FACTS (mirror_trunk.py run_gate_a; first light 0.94)
JOIN RULE (empirically verified pid43176 probe): parity hdr.seed = props[0]
= FIRST PROPOSAL, NOT the seed token. recs r[1] = labels[0] = actual t[pos].
Join = (pos, recs.labels[1]) == (parity.pos, parity.seed) -- works on
accepted-first-proposal cycles (~all dlen>=1 mostly); rejected-first cycles
are skipped (mild selection bias, revisit later). pos COLLIDES across convs
of one pid -- never join on pos alone.
ELIGIBILITY: window exact requires cycles >= WINDOW_ROWS+8 (135) past every
turn boundary (pos jump > 32 == new prompt == engine RE-SEEDS window from
prefill taps (absent from .recs) each turn, not only conv start) AND conv
deep enough. Dev pid43176 convs are all too shallow (max 152 recs) -- use
day-12 pid48230 (auto-selects 5230-rec conv, 6 bounds, targets exist).
FIRST RESULT: cos ~0.886-0.949 maxerr ~0.65-0.78 (slots 0-2, one cycle).
That is far ABOVE the ~3% fp8-input-noise floor (cos ~0.9995 expected) =>
systematic convention bug remains, NOT a noise ceiling. Diagnostics in
flight (MIRROR_HC_STABLE=1 sigmoid form; MIRROR_EMPTY_RING=1 window blame
split). Conventions verified already: group-slice lora, ids [seed,noise x4],
position ladder (target@pos-1, drafts@pos..pos+4), kv row order [window|
target|drafts], per-stage ring append/evict math (seed eviction after 127
appends -> off-by-one verified), eps values (rms 1e-20 vision-exp override,
hc 1e-6), norm weight direct-scale (means ~0.2 not 1+w), router scale
1/sqrt512, sinks as logit in denom.
NEXT IF STABLE-FLIP FAILS: suspect activation quantization in fused matmul
kernels (q8 pair kernels may quantize activations: attention_output 'q8'
weight-only vs 'low_q8_rows' path), MoE gate/up/down accumulation order
(f16 vs f32), rope pow-vs-exp2 path, sinkhorn fused-norm variant differences.

## 8. KERNEL MATH BANKED (Nov-03 late session)
ROUTER (batch, our model; ds4_metal.m 34330-34470 + dsv4_misc.metal
kernel_dsv4_router_weights_batch @4838): probs = SQRT(SOFTPLUS(logits))
(kernel_dsv4_softplus_sqrt_f32_4); scores = probs + exp_probs_b (bias
affects SELECTION ONLY - ds4_gpu_indexer_topk over 256); weights[i] =
probs_sel[i] / max(sum(probs_sel), 1/16384=6.1035e-5) * 1.5, and the
denominator sum has a FIXED 6-lane reduction topology (do not reorder).
NOT sigmoid-softmax, NOT plain softmax - this was the biggest mirror risk.
HC MIXER (dsv4_hc.metal kernel_dsv4_hc_split_sinkhorn @113 +
split_weighted_sum @288): mix row 24f = [pre4 | post4 | comb16]; per part
scale_k = hc_*_scale[k] (3 floats) and base (1,24): pre = sigmoid(mix*scale
+ base) + eps; post = 2*sigmoid(...); comb rows: softmax over row max-exp
(eps added AFTER normalize: r*(1/sum) + eps), then sinkhorn 20 iters:
rownorm 1/(sum+eps) then colnorm 1/(sum_r+eps). Collapse: cur = SUM_i
pre_i * stream_i (verified at 288-kernel tail). Expand/residual formula
(comb x streams + post) lives in kernel_dsv4_hc_expand_split - READ at
impl. SIGMOID FORM MATTERS: production boot log has drift flag
hc_stable=ON -> tanh(z/2) form; fused decode sites historically use naive
1/(1+exp(-z)) - mirror must match whichever the actual kernel compiles to
(check DS4_METAL_HC_STABLE at build; cos-level probably immune, bit-level
not).
STILL OPEN kernel reads: hc_expand_split, dsv4_fp8_kv_quantize (dsv4_kv.
metal), noncausal_raw_batch_heads scaling + sinks placement in softmax
denom (flash_attn.metal), rope_tail inverse + yarn consts (dsv4_rope.metal),
spec_logits head path (ds4.c 36xxx/80xxx), indexer_topk tie behavior.

## 8b. TAIL FINDINGS (same session)
- EXPAND4 (dsv4_hc.metal:652): out_j = block_v*post_j + SUM_i comb[j][i]*r_i
  (block_v = attn_out (+block_add if has_add); post = 2*sigmoid part of split,
  comb = sinkhorned matrix, r_i = residual streams). CONFIRMED full mixer
  algebra: collapse uses pre, expand uses post+comb.
- FP8 KV (dsv4_kv.metal:111): FAKE-quant E4M3 on the FIRST 448 dims in
  64-blocks: amax floored 1e-4, scale=2^ceil(log2(amax/448)), clamp(v/scale,
  +-448) -> e4m3 dequant * scale, written back f32; LAST 64 (rope) dims
  pass f32 EXACT. MLA 512 = 448 nope + 64 rope layout. Fully reproducible.
- SPEC_LOGITS (ds4.c 36200-36350): final rows (conf, pre-norm) ->
  rms(final->norm) -> matmul with **BASE MODEL OUTPUT HEAD**
  (base_weights->output, q8_0 [4096,129280]) -> spec_logits 5 rows.
  *** NEW MATERIAL REQUIRED (S2.2): extract output head from base gguf via
  tools/extract_gguf_tensor.py (--name, q8_0 -> dequant rows via gguf_dq)
  to blade npz; ~1 GB f32 or keep f16/q8 + on-demand. conf rows DON'T need
  it (milestone-2 gate A unaffected); proposal gate B does.
- ATTENTION (flash_attn.metal shared ext kernel): q*k*scale, noncausal over
  [window|target|drafts] raw rows, sinks = extra key at logit position
  sinks[head] added to softmax denom; output /S. Exact scale value + rope
  inverse convention + indexer_topk tie-break: VERIFY AT IMPL against
  parity rows (cos gate catches), don't block on more reading.
- STAGE FLOW recap for impl: per stage s: flat=rms(stage_input_hc rows6) ->
  mix=fn_attn@flat (24) -> split: pre=sig+eps, post=2sig, comb=sinkhorn ->
  attn_cur=SUM pre_i stream_i -> rms attn_norm (row0 := main_x shared) ->
  q path (5 drafts): q_a(1024)->norm->q_b(64x512)->head-rms->rope(pos+row) ;
  kv path (6): kv(512)->kv_a_norm->rope(row0 feature_pos, drafts pos+) ->
  fakequant448 -> store ring append_pos ; heads: noncausal attn over
  window+6, sinks, unrope(inverse) -> out_a/b grouped-lora(8x1024) ->
  expand4 vs draft residuals -> ffn: flat2=rms -> mix_ffn/sinkhorn ->
  ffn_cur=collapse(pre) -> rms ffn_norm -> router(sqrt-softplus+bias top6,
  weights renorm*1.5) -> 6 routed experts (gate,up swiglu, down) + shared
  (always) -> SUM -> expand4 again -> next stage. After stage2: conf rows =
  collapse(hc_head mix, PRE final norm); logits rows = norm-mix @ base head.
