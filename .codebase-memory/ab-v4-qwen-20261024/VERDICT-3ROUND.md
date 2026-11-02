# AB-V4-vs-QWEN 3-ROUND VERDICT — 2026-10-24 (judge: full-quality Qwen3.8 session model, ground-truth anchored)

Method: 10 tasks (math/CRT/induction/planted-bug/format/JSON-extract/comprehension/tool-choice/rope-riddle/vision-OCR),
temp-0, engine-default thinking ON, same runner both engines. r1 caps 500-900, r2+r3 caps 2000.
Prod Qwen3.8 (:8002, exact stack) vs resident V4-Flash-Vision-Exp (:8001). Files: ab{,2,3}_{qwen,v4}.jsonl.

## Scores (corrected — my r2 message said 17-15; column sums are 15-13)
| round | caps | Qwen | V4 | note |
|---|---|---|---|---|
| r2 | 2000 | 13 | 15 | first fair round |
| r3 | 2000 | 13 | 15 | **byte-identical outputs to r2 for BOTH engines on 10/10 and 9/10 tasks** — deterministic |

Per-task stable outcome (r2+r3): t1-t5,t8 tie (both perfect). **Deciders: t7 comprehension:
V4 answers (652 tok), Qwen loops >2000 tok empty (3/3). t10 vision: V4 reads "896x896" right
(3/3, sips-verified), Qwen confidently says "800x800" (3/3 — deterministic mmproj OCR flaw).
t6 extract: Qwen gets all 3 pairs (2/2 at 2k); V4 misses the $200/Mar-31 late-fee pair (2/2 —
deterministic). t9 rope riddle: BOTH loop to empty at 2000 (3/3 each).**

## Determinism findings (independent of model quality)
- Both servers under temp-0 are near-perfectly reproducible across boots/reps (8-9/10 byte-identical;
  diffs only where r1 caps truncated). Engine health: confirmed.
- "Failures" here are SYSTEMATIC model behaviors, not sampling noise: Qwen t7/t9 thinking-loops,
  Qwen t10 misread, V4 t6 omission.

## Practical implications for this box
1. Agent harnesses on EITHER model: budget >= 2k output tokens AND handle empty-content
   (t9-class open riddles produce silence at 2k on both).
2. Resident V4-Exp quality = competitive, slightly ahead (15-13) on this microset via vision OCR +
   terse comprehension; behind on strict-extraction completeness. Not a daily-driver switch case
   given ~1.3x wall (Qwen battery ~115 s vs V4 ~152 s; decode 69.4 vs 44.6 t/s).
3. Qwen mmproj "800x800"-class confident misreads are real and deterministic — treat small-text
   OCR from the Qwen vision path as low-trust; V4's vision tower read the same label 3/3 correct.
4. Ten tasks is a micro-sample. For a decision-grade comparison run the banked `benchmark/`
   SWE-bench smoke from the M1 Air.
