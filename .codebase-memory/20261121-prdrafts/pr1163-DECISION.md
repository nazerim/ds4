# #1163 — decision: rebase, do NOT withdraw-and-republish. Comment first, rebase later.

Staged, **not posted**. Owner go required for anything on `antirez/ds4`.

## Why "publish the more thorough fix upstream" is the wrong move

We checked *why* the fork's fix (`d097801`, 2026-09-29) is 464 lines when #1163 is 61. It is not a more
thorough fix of the same bug — it is **two things stapled together**:

| production lines | what | upstream-relevant? |
|---|---|---|
| 64 (`-3645`) | `log_structural_content_markers()` — counts content that quotes protocol syntax, called from `parse_chat_request` / `parse_anthropic_request` / `parse_responses_request` (5 refs: 3840, 4678, 4946, 6018, 26280) | **No.** Separate "backtick" workstream; explicitly *measurement only*, because Option B (neutralising content at render time) was deliberately **not** implemented — it "costs a one-time cache rebuild per affected conversation and must exempt replayed tool spans or it re-orphans every checkpoint" |
| 71 (`-6953`) | `degrade_unterminated_tool_call()` — strip partial markup, map finish via `tool_parse_failure_recovery_finish`, clear `err` | **Yes**, and this is the same policy #1163 triggers |
| 30 (six hunks of 1–4 lines) | parser wiring for the logger + decode-loop callsites | mostly fork plumbing |
| 170 | tests | useful |

So ≈40 % of the fork's "thoroughness" is an observability feature from another investigation. Landing that
upstream as part of a tool-call bug fix would be textbook scope creep, and it is coupled to fork features
(tool memory, the checkpoint frontier) that upstream does not have.

**Equally important: #1163 is not a half fix.** We verified upstream's parse-failure recovery produces the
same user-visible outcome the fork's helper hand-rolls — `finish` mapped through
`tool_parse_failure_recovery_finish`, raw text kept as assistant content, no executable calls. The PR just
lets the existing mechanism run by not pre-setting `finish = "error"`. Minimal ≠ incomplete.

## Withdraw-and-republish: strictly worse
Withdrawing resets a PR the API reports `mergeable=true state=clean` and replaces it with a bigger one, in a
repo where `antirez` has written **0 comments across all 10 of our PRs** and merged nothing since 11-03. A
re-submission is a cold start with *more* to review, and GitHub's "closed" state reads as abandoned.

## Just rebase? Not yet — the evidence comment first
`mergeable=true clean` means the rebase removes **no** friction; it is pure feed-bump. That is exactly the
reason we withdrew the #785 ping — but #785 at least had 3 reviewer comments to stay relevant to, and #1163
has **zero engagement of any kind since 2026-10-01**. A no-information rebase on a zero-engagement PR is
noise we told ourselves to avoid.

The one action that adds information without asking for anything:

```zsh
D=$(mktemp -d /tmp/pr1163.XXXXXX) && cd "$D" || exit 1
cat > note.md <<'EOF'
Running an equivalent fix in production since 2026-09-29, so the diagnosis is corroborated independently of this PR — including the part that is easy to miss: on the Anthropic surface `anthropic_stop_reason` maps `error` to `end_turn`, so today the truncated markup reaches the client as ordinary assistant text with no indication anything went wrong. That path is silently wrong rather than visibly wrong, which is why I'd keep this open even though it looks cosmetic next to the OpenAI `finish_reason: "error"` case.

No action needed from you; happy to rebase if it helps later.
EOF
wc -c note.md
gh api repos/antirez/ds4/issues/1163/comments -F body=@note.md --jq '{id, html_url}'
```

Then re-read the posted body via the API (project rule: verify what landed, not what was sent).

**Rebase trigger, written down so it isn't a standing chore:** only when (a) upstream touches the
unterminated-envelope region in `ds4_server.c` and `mergeable_state` stops being `clean`, or (b) the
maintainer replies. Otherwise leave it.

## Also true, and worth saying out loud
Our fork is **immune**, so this bug generates no pain on our side: `ds4_server.c` has 0 occurrences of the
`unterminated tool call (%s…` snprintf, and 724 live checkpoints have not surfaced it. That is precisely why
I earlier mistook local irrelevance for the PR being obsolete. Upstream value is the only value here, and
#1163 carries it.

## Not verified
Whether `emilianbold` or anyone else considers the Anthropic invisibility argument decisive; whether a
comment bumps the PR usefully or buryingly in the maintainer's inbox; whether the fork's Option B (render-time
neutralisation) is eventually worth proposing on its own merits.
