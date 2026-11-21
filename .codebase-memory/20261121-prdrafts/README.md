# Prepared-but-unposted PR drafts

Continuation artefacts from the 2026-11-20/21 session. Index and gate conditions:
`../20261121-HANDOVER.md` §3. **Nothing in this folder has been posted.** Posting to
`antirez/ds4` requires explicit owner go, and items A and D additionally require deploy/verification or
a code change first — read the gates before sending anything.

| file | what it is | usable as-is? |
|---|---|---|
| `w1_1020_replies.md` | Full review response package for PR #1020: three inline reply bodies, the exact comment ids and `gh api … /replies` commands, a recommendation **against** a top-level comment, and an evidence table with controls | **No** — reply 1 and reply 2 assert the guard is fail-closed. True only once `a1f3bd2` is deployed *and* verified. Read `../20261120-TOKFP-RESTAMP.md` first |
| `reply1.md` `reply2.md` `reply3.md` | The same three bodies as standalone files, ready to `cat` into the posting command | same caveat |
| `w2_1163.md` | #1163 rewrite-vs-withdraw analysis, symbol audit, port steps, proposed PR body | **No** — its `rewrite` verdict was overturned by independent review (the fork's production call site passes `NULL` for the re-parse outputs, so that branch never executes; "strict superset" is false). Treated as source material for a *withdraw* comment, not as a plan |
| `w4_785_1022.md` | #785 rebase plan + ping text; #1022 comment draft + recommendation | #785: rebase is **done and pushed** (`107fc9d`); the 58-word ping is good to send on owner go. #1022: recommendation is **do not post** — fix the default first |
| `fix_tokfp.md` | The fix author's cumulative report: chain verification, four review rounds, RED/GREEN transcripts, mutation checks, deploy checklist | Reference/evidence, not for posting |

Drafted by subagents on `ds4-qwen`, `qwen-subscription`, `claude-agent/haiku` and reviewed on
`openai/gpt-6.1-terra#low` and `claude-agent/sonnet#low`; every load-bearing claim was re-checked by the
coordinator, and the claims that failed re-checking are marked above rather than quietly deleted.

## Update 2026-11-21 after independent review
`pr1020-FINAL-staged.md` supersedes `w1_1020_replies.md` for posting: threads 1 and 2 are revised because
the shipped fix is NOT in PR #1020 (head `9cbf7e59` is not an ancestor of `a1f3bd2`), so the old wording
claimed fail-closed behaviour the PR does not have. Thread 3 (`reply3.md`) is unchanged and approved.
The #785 ping in `w4_785_1022.md` is **withdrawn on review** - its "decodes to `max_tokens`" claim is only
the worst case, and the rebase push already surfaced the PR. #1022 is now a code task, not a comment task.

## Update: #1022 implemented
`w4_785_1022.md`'s #1022 recommendation is executed on fork `main` (`82b57ff`) and live-verified
(17 images accepted, 257 rejected with "at most 256"); a matching commit `1ddb892` sits on local branch
`wt1022` **awaiting owner go to push to the PR**. Do not post a #1022 comment: the push is the
notification. The #785 ping in that file is withdrawn (see HANDOVER §6).

## Update 2026-11-21 (second reversal): #1163 is NOT obsolete
`w2_1163.md`'s original **rewrite** verdict stands. The later **withdraw** verdict rested on a claim that the
fork's production parse passes NULL for the re-parse outputs, so the recovery branch never runs — that is
**false** (`ds4_server.c:17253` passes all out-params plus `&recovered_tool_parse_failure`), and upstream
`b3f50180` still sets `finish = "error"` for an unclosed envelope, which is what disables its own recovery.
Keep the PR open. Its own "fork is a strict superset" argument is *still* retracted for a different reason
(see `../20261120-TOKFP-RESTAMP.md`), and the fork's independent fix `d097801` is corroboration, not a
substitute. Full evidence: `../20261121-HANDOVER.md` §12.

`pr1163-DECISION.md` is the current word on #1163: keep as filed, one evidence comment staged (UNPOSTED),
rebase on trigger only. Supersedes both the 'withdraw' idea and 'republish the thorough fork fix'.

## Hazard fixed 2026-11-21: the posting-ready files had gone stale
`reply1.md`/`reply2.md` were the **pre-review** bodies — they still asserted "fail-closed" and "one
re-prefill, no cascade" / "next prefill rewrites a fresh stamp", the two claims independent review found
false about PR #1020's actual head. `README.md` meanwhile advertised them as "ready to `cat` into the
posting command", so the obvious action (cat reply2.md) would have posted a known falsehood to a public
thread. Both files are now **regenerated from the reviewed blocks in `pr1020-FINAL-staged.md`** and verified
by grep gate (no posting-ready file contains `fail-closed`, `next prefill rewrites`, or `<｜User｜>`).

`reply3.md` is unchanged — thread 3 was approved as-is. `w1_1020_replies.md` remains the historical draft
package: **do not post from it**; it is the source of the retracted `<｜User｜>` probe claim.

Also recorded because it nearly caused a silent bad post: the script that regenerated these files first
treated a *blank* line as the end of an indented block and therefore extracted only paragraph 1 of thread 2
— dropping the sentence that discloses the store-side gap. An assert on expected content caught it. When
extracting text to post, assert the load-bearing sentences are present; do not trust a clean exit code.

## FINAL (2026-11-21): post ONE reply only
`reply2-final.md` is the sole posting candidate (byte-locked to `reply2-approved.txt`); post with
`POST_IT=1 post-1020.sh`, which dry-runs by default and verifies the posted text afterwards.
`reply1.md` and `reply3.md` are **DROPPED** (kept for the record): not duplicative, but low-value-to-action
against the cost of adding ~1.2k chars to the 4,257 we already have on a thread where the reviewer wrote 393
and we promised to "leave this quiet". Do not post `pr1020-FINAL-staged.md`'s earlier "Final texts".

## DONE 2026-11-21 00:50Z
`reply2-final.md` posted as review-comment reply **4649423282** on thread `4131745125`
(https://github.com/antirez/ds4/pull/1020#discussion_r4649423282); live body verified byte-identical and
single. `reply1.md`/`reply3.md` remain dropped; do not post a top-level comment. #1163 remains **no post**.
