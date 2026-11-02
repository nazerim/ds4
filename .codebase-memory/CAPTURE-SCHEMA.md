# CAPTURE-SCHEMA — Stage 0 artifact (2026-10-29)
Derived from measured dims in gguf/DeepSeek-V4-Flash-Vision-Exp-DSpark-support.gguf
+ confirmed tap sites in ds4.c. This is the trainer/collector contract.

## Engine constants (measured this session)
| quantity | value | source |
|---|---|---|
| DS4_N_EMBD (hidden width) | 4096 | attn_norm [4096], main_norm [4096] |
| target tap layers | 40,41,42 (count=3) | support GGUF dspark.target_layer_ids |
| in_dim (feature width) | 12288 = 3×4096 | main_proj [12288, 4096] |
| block_size | 5 | dspark.block_size |
| stages | 3 | stage_count |
| vocab | 129280 | markov_w1 [256,129280] |
| markov_rank | 256 | metadata + markov_w2 [256,129280] |
| confidence in_dim | 4352 = 4096+256 | confidence_head.proj [4352,1] |
| n_experts | 256, top-? (gate [4096,256]) | ffn_gate_inp |
| hc streams | 4 (hc_fn [16384,24]; 16384=4×4096) | hc_attn_fn |
| attention | MLA-ish: q_a 1024, q_b→32768 (32h×1024), kv_lora 512, 64 sinks | attn_* |

## Feature dtype at rest
Runtime packs in_dim×f32 (dspark_stage0_packed). On disk we store **fp8_e4m3**
(1 byte/elem) => 12288 B/pos. fp8 e4m3 range ±448, rel err ~2-6% — acceptable
for a distillation *input* (labels stay exact). Loss measured in Stage 3 wk1
gate (retrain-loss delta fp8 vs f32 must be <1%).

## RECORD FORMAT (ACTUAL, as written by ds4_td module; supersedes the
## Stage-0 design draft which was never implemented — do NOT code a loader
## from anything older than this section)
Files: <DS4_TRAIN_DUMP>/train_pid<pid>.recs  (fixed-size records, appended,
flushed in batches) + train_pid<pid>.idx.jsonl (one line per FLUSH BATCH).
Record = 32-byte header + 12288 fp8 bytes = 12,320 B, little-endian:
```
struct ds4_td_diskrec {
  uint32 pos;        # frontier index at the prepare cycle (= checkpoint.len)
  int32  seed;       # token fed at pos (== checkpoint.v[pos] at capture time)
  uint8  streak;     # NEXT record pos - this pos - 1 (tokens committed
                     # between prepared cycles; scheduler-skip gaps merge
                     # into it; NOT draft-acceptance count; 254 = clamped
                     # >=255; 255 = OPEN (segment tail or unresolved))
  uint8  n_label;    # resolved lookahead count (0..5); labels gated by this
  uint8  n_feat;     # 3 = all taps captured (fp8 e4m3 satfinite,RZ)
  uint8  flags;      # bit0 = segment start (only bit in use)
  int32  labels[5];  # checkpoint.v[pos..pos+4]: labels[0] == seed by
                     # construction; unused slots ZERO-filled (readers must
                     # mask by n_label — NO 0xFFFFFFFF padding exists)
  fp8    feat[12288] # h[pos-1] concatenated taps layers 40|41|42 x 4096
}
```
No accept_bits, no conf_gate, no logits in the record (Stage-0 draft fields
were dropped at implementation: acceptance structure is derivable from
pos-gaps + streak, per-cycle confidence is not comparable across regimes).
Drop rule (single predicate, shared by tools/td_validate.py,
tools/td_manifest.py, tools/td_classify.py): EXCLUDE n_label==0.
Streak==255 alone does NOT disqualify a record (labels may be valid; v1.2
final-flushes produced labeled open-streak tails).
Sizing: 12,320 B/pos measured. 20M pos = 246 GB.
FireCuda520 free: ~1.0 TB with kv ladders sharing the volume.

## Sidecar .idx.jsonl (ACTUAL — one line per flush batch, NOT per record)
{"conv":<u32 per-session segment counter>,"pid":<server pid>,"flushed":<recs
written>,"kept":<0|1 kept in ring>,"ck_len":<checkpoint len at flush>,
"final":<0|1>, "rec_b":12320}
Batch spans reconstruct conv grouping (records are appended in flush order;
one batch = one session-ring segment, one lineage — under v1.3+, v1.2-era
adoption bypass could mix two lineages in one batch: see inbreaks NOTE).
Provenance for engine config (conf 0.45 / window 12 / model_fp
11038508403456853112) lives in MANIFEST.json["engine"], not per-record.

## TAP CONTRACT (Stage 1 implementation targets — verified line numbers)
1. FEATURE: ds4.c:34765 `metal_graph_pack_dspark_target_hidden_batch(g, dw,
   packed, row0+n_tokens)` — GPU holds [n_tokens × 12288] f32. Tap = async
   GPU->CPU copy of the rows region into a double-buffer ring (only when
   DS4_TRAIN_DUMP set). Ring drained by a writer thread (no hot-path blocking).
2. LABELS: tokens p+1..p+5 are the block the verifier just accepted; available
   from the `accepted[]`/`drafts[]` arrays at the accept sites (83417 greedy,
   83974 stochastic). After a divergence the *target-sampled* continuation is
   what labels next_tok (so next_tok = committed tokens, not drafts).
3. ACCEPT_BITS: same sites — per-position accept/reject already computed for
   stats; reuse verbatim.
4. FLAGS/context: session think_mode, DSML boundary (from tool-call tokenizer
   state), gen_ctx_len = s->checkpoint.len at capture.

## TRAINER LOADER CONTRACT (Stage 3 consumes)
- Yields minibatches of (feat[12288] fp8->f32 -> main_proj is stage0 input,
  next_tok[5], accept_bits[5], conf_gate).
- Mask records where n_next<5 or pos crosses a prefill->decode boundary
  (flags bit0) UNLESS the record is inside one turn.
- Holdout split by conv_id (never split a conversation across train/val).
- Per-position acceptance curve computed from accept_bits == ground-truth
  eval that must match `make dspark-acceptance` offline.

## OPEN QUESTIONS (resolve before Stage 1 code)
- Q1: does async f32->fp8 conversion happen on GPU (preferred) or CPU writer?
    Metal: need an fp8 downconvert kernel; else store f32 (24.6 KB/pos, 2x
    budget) for v1 and revisit. Decide: v1 = f32 (simple, correct), fp8 in v2.
- Q2: prefill-time capture (long n_tokens batches) may exceed stage0_packed
    buffer (code falls back to batch_q/alloc). Tap must handle all three
    sources (packed / batch_q / owned alloc) — read ds4.c:34763 branch.
- Q3: writer thread lifetime vs engine shutdown/retire — flush on SIGTERM or
    lose tail of last conversation; hook existing clean shutdown.

## STATUS
Stage 0: DONE. Stage 1: **v1.3 LIVE (2026-10-29 15:5x, built; active on next
engine restart — collection currently running on the v1.2 image).
v1.3 (D4: disk-KV ADOPTION bypass): log-correlated forensics of the live
termbench corpus (26k recs) found 2 unhooked lineage switches = direct
checkpoint ADOPTION (kv-cache-hit resume after idle: `token_vec_free
(&s->checkpoint); s->checkpoint = new_checkpoint;` at ds4.c x4 sites) — never
rewind/invalidate, so pending records of the old branch mixed into the same
ring batch (pos breaks at recs idx 1661, 17378; avail=0 guard prevented any
wrong-lineage LABELS — tails just lost labels). Fixes: (a) ds4_td_segment_end
hooked BEFORE all 4 adoption swaps (pending flushes with live labels);
(b) close() backstop: lineage_shift = pos reset OR checkpoint.v identity
change -> emit-only flush + conv++ + segment flag (catches any future swap
site); (c) label resolution MOVED from flush-time to per-cycle close()
backfill (grows lookahead as tokens commit; flush is now pure emit,
lineage-impossible-to-corrupt). Suspect-window rule for trainer: around each
in-batch break drop idx (break-127..break] (legacy v1.2 data only).
Earlier: **v1.2 VERIFIED 2026-10-29 (server mode too)**.
Module ds4_td_* in ds4.c (+294 lines, env-gated); ds4-server rebuilt 10-29
and running images have carried it since 13:55 (inert without DS4_TRAIN_DUMP).

v1/v1.1 defects resolved (see git-less history in DSPARK-STATE doc):
  D1 n_feat=0: (a) tap moved PRE-prepare-impl (grab stages features; the
     draft forward invalidates the single-row capture, which holds h[pos-1]
     exactly pre-impl); (b) root cause: ds4_gpu_tensor_read returns
     1=success/0=fail (ds4_metal.m:9494).
  D2 streaks: informational only (pos-gap semantics; 255=open/segment-cut).
  D3 (v1.2, found by SERVER smoke): server reuses ONE session object across
     conversations (slot pool; transitions go through ds4_session_rewind to
     the prefix-reuse point, not a new session). Flush-time label resolution
     then relabeled previous-conv records with the NEXT conv's tokens
     (observed corruption: 164 records with wrong-lineage labels). Fix:
     labels are SNAPSHOT into each RAM record (backfilled at every flush from
     the still-live checkpoint; the segment hook flushes BEFORE invalidate/
     rewind mutate the checkpoint). ds4_td_segment_end() hooked at
     ds4_session_invalidate + ds4_session_rewind + rewind_speculative:
     final-flush + conv_id++ + flag=1 on next record.

v1.2 server smoke (armed, 3 tool-conversations, PID 72369):
  162 recs, nfeat=3 all, 162/162 distinct feature rows, 100% nonzero bytes;
  3 segments at flags {0,115,137} matching idx convs 1/2/3 (final:1 each);
  pos monotone per segment; labels[0]==seed 100%; label-chain (labels[g]==
  next seed for gap g) 80/80. Flush triggers ALL verified live: ring-cap
  (128), conversation boundary (rewind hook), retire->session_free.

Capture semantics (trainer must know):
  - A "conv segment" = one server REQUEST worth of frontier decoding (each
    continuation turn replays via rewind -> new segment). Records within a
    segment are one contiguous committed stream. Segments are independent
    training units; conditioning is exactly how the drafter is used.
  - SERVER MODE SPEC GATING: ds4-server only runs dspark speculation for
    requests WITH tools (ds4_server.c ~15692: plain-chat requests set
    dsml_token_id>=0, killing the spec gate at ~15797). Capture therefore
    records only tool/agent traffic - which IS the target distribution.
    Plain-chat requests produce NO records (no prepare calls at all).
  - Records begin at the first decode frontier (post-prefill pos); prompt-
    interior positions are not captured (correct scope).
  - DS4_N_EMBD is runtime (g_ds4_shape): 3 taps x 4096 = 12288 fp8 bytes,
    ring sized with runtime guard; re-check for wider families.
  - conv_id = process-local counter incremented per segment; join via
    idx.jsonl (flush batches) + flag=1 starts.

Next: Stage 2 collection day = restart-vision-dspark with
DS4_TRAIN_DUMP=/Volumes/FireCuda520/ds4-train (verified path today);
day-1 check per §v1.2 smoke criteria. Optional hygiene: per-record fopen/
fclose at emit (128/flush burst) is fine but could hold an fd.
