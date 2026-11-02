# ROUND 4 — sampling & thinking-budget arms (2026-10-24, CORRECTED v2). Files: ab4_{qwen,v4}.jsonl

Arms: A temp0/4000 · B temp0/2000+reasoning_effort=low · C temp0/1500+enable_thinking=false ·
D rec-temp 2000 ×2 reps (Qwen 0.6/top_p .95 family-standard; V4 **1.0/.95 = repo-pinned DS4FORK.md:88**).
Full battery A–C; D on t6/t7/t9 only. Judge channel: `content` (agent-facing); `reasoning_content`
(rc) = cause-of-emptiness evidence.

## The empty-content issue: DEFINITIVE mechanism
`finish_reason=length` + empty `content` = **thinking overruns the output budget before the
handoff to the answer channel**. The answer often EXISTS in rc (Qwen t9 @2k solved "30+15=45"
mid-reasoning; @4k still overrun at 14,700c; V4 t7@A solved at 15,630c). Not sampling, not
knowledge — a channel-delivery failure of unbounded deliberation. Temperature does not fix it
(arm D: Qwen t9 2/2 empty, V4 t9 2/2 empty, V4 t7 2/2 empty at temp 1.0).

## Kwarg support asymmetry (CORRECTED — v1 of this doc had the V4 column wrong)
- **Qwen honors BOTH**: `reasoning_effort=low` (arm B differs from A) AND `enable_thinking=false`
  (arm C: rc=0 across the board).
- **V4 IGNORES `reasoning_effort`** — arm B is **byte-identical to arm A on all 10 tasks**
  (610/1625, 286/824, … t9 still empty). ds4_server.c implements the kwargs "as the **Qwen3.8
  model card** documents them" — effort levels are apparently Qwen-path only.
- **V4 DOES honor `enable_thinking=false`** — arm C: rc=0 everywhere, all 10 answered,
  incl. t9 (268 tok).

## Per-arm outcomes
| arm | Qwen | V4 |
|---|---|---|
| A 4k temp0 | t7 FIXED (2171). t9 still empty (14.7k rc) | t7 FIXED (3851). t9 still empty (15.5k rc) |
| B effort=low | **ALL 10 answered** — t7="inverse Gray code", t9 solved, t10 "896x896" CORRECT | **NO EFFECT** (B≡A byte-identical; t9 empty) |
| C enable_thinking=false | ALL answered; **t2 CRT FAILS (0.10)**; t10 terse, label omitted | ALL answered; **t2 correct (0.05)**; t7/t9/t10 correct; t6 FENCED json |
| D rec-temp | t9 2/2 empty; t7 1/2; schema drift | t6 2/2 (late fee still missed); t7 2/2 answered; t9 2/2 empty (12.3k rc) |

## Headline findings (corrected)
1. **Best config is model-specific**: Qwen → `reasoning_effort=low` (fixes t7/t9 handoff,
   fixes t10 vision priming, keeps CRT). V4 → `enable_thinking=false` (fixes t9 handoff AND
   the t6 extraction omission — all 3 pairs incl. $200 late fee appear at no-think; effort
   kwarg is a no-op for it).
2. **Both marquee "flaws" are thinking-mode artifacts, not capability**: Qwen's deterministic
   800x800 vision misread vanishes at low effort; V4's deterministic late-fee omission (4/4
   rounds thinking-ON) vanishes at no-think.
3. No-think costs reasoning quality ON QWEN specifically (t2 0.10 System-1 trap); V4 passed
   CRT no-think. Do not generalize "disable thinking for everything".
4. V4 is ~1.7x more token-hungry when thinking (t7@A 3851 vs Qwen 2171; rc 15.6k vs 8.9k).
5. Recommended temp (1.0/.95 V4-official; 0.6/.95 Qwen-family-standard) adds diversity but
   NOT reliability — keep evals at temp-0; agent traffic unaffected either way.

## Operational guidance adopted
- Agent traffic on Qwen prod: default `chat_template_kwargs: {reasoning_effort: "low"}`;
  upgrade to default effort only for genuinely deep chains; never fully no-think for
  riddle/trap-prone prompts.
- If V4 ever serves agents: `enable_thinking:false` is the ONLY working throttle there.
- Harnesses on either engine: treat `finish_reason=length` + empty `content` as
  "read reasoning_content" fallback (the answer is frequently already in it).

## Ops incident (same night, for the record)
The R4 V4 window's tracked shell lingered past a duplicate-looking completion notification;
cleanup killed it mid-restore (it had stopped Qwen prod). Recovery: stray auth_proxy 10722
stopped via `stop-proxy`, Qwen prod restarted (pid 10926). No data loss: ab4_v4.jsonl stayed
at exactly 36 records (no duplicate-append). Lesson: harness completion notifications can
fire while the tracked tree still runs — audit `ps`/ports before trusting "restored" lines.
