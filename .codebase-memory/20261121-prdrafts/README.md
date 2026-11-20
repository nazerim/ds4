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
