# PR #1020 — reviewed reply package, STAGED, NOT POSTED

Review verdict (`claude-agent/sonnet#low`, 2026-11-21): **thread 3 POST AS-IS**, **threads 1 and 2 REVISE**.
The revised texts below are the review's, adopted.

## The finding that changed the verdict
The shipped fix (`a1f3bd2`, deployed and verified) is **not in this PR**. The load-bearing check is
`git merge-base --is-ancestor a1f3bd2 9cbf7e5` -> **rc=1**, i.e. our fix commit is not an ancestor of the PR
head. (An earlier revision of this file asserted the reverse direction, "PR head is not an ancestor of
`a1f3bd2`"; that is also true but it is the wrong fact to cite — it would equally hold for any unrelated
commit and proves nothing about inclusion.) At PR head `ds4_kvstore.c:1003-1010` still
takes the reuse shortcut with no stamp check and then rewrites the trailer — i.e. the laundering defect
is live in the PR. So the draft's "mismatch → miss, payload untouched, next prefill rewrites a fresh
stamp" and the flat "fail-closed" were **false about the code that thread defends**. Line-number
citations were all re-checked at the PR head and none had drifted; the defects were in the claims.

Two further corrections adopted:
- The probe at `ds4.c:65014` is `<` + U+2002 EN SPACE + `User` …, a near-miss string, **not** the real
  `<｜User｜>` token. No probe contains a real special token, so the probes cannot detect id
  renumbering — and renumbering would surface in the vocab loop anyway, since `vocab_lookup` derives ids
  from the hashed index→string table. The claim "ids are never hashed; the probe is the only signal"
  was wrong on both counts, and `ds4.c:45712` is a `think_end_id` lookup, a weak citation for it.
- `gh api -f body=@file` is wrong: `-f` sends a literal string, so it would post the text `@file`.
  `-F body=@file` reads the file. `-f body="$(cat file)"` also works.

## Decision recorded
Disclosure of the store-side gap stays in reply 2 — the thread is defending a design whose store path
has a known hole, and the review would not sign it otherwise. The offer is limited to "say if you want
it added here": **we have not committed to porting `a1f3bd2` onto the PR branch**, and nobody has
checked whether it ports cleanly (the two `ds4_kvstore.c` layouts differ ~3x).

## Final texts

### Thread 3 — `4131755372` (unchanged, see `reply3.md`)

### Thread 1 — `4131726056`

    Partly right. The vocab loop (`ds4.c:65040-65045`) and merge loop (`ds4.c:65053-65059`) hash tokenizer *data*, so neither moves if `tokenize_rendered_chat_vocab` (`ds4.c:45939`) changes its pre-tokenizer behaviour over a byte-identical GGUF vocab. The probe loop (`ds4.c:65060-65067`) runs that same function, the one every request goes through (`ds4_kvstore.c:701` keys the suffix with it), so it is the only part that sees such a change. The probe list (`ds4.c:65007`) is hand-written; add an entry when you change tokenization behaviour that the existing probes don't exercise. That moves every stamp once.

### Thread 2 — `4131745125`

    Correct that it's rigid, and I'd keep it: the hash covers all `n_vocab` tokens (`ds4.c:65040`) and every used merge slot (`ds4.c:65053`), so an added token changes the stamp even if it never fires. That's deliberate: a new entry can win a merge, existing text re-splits, and the stored rows then describe different ids. On mismatch the load returns a miss and leaves the file in place (`ds4_kvstore.c:1317`). If additive vocab is a supported workflow, the right fix is a separate author-maintained generation field, not a looser hash.

    One gap in this PR that I found after your review: the store side still reuses an existing same-text file without checking the stamp (`ds4_kvstore.c:1003`), so after a mismatch the next store can keep the stale payload and re-stamp it. I have a fix that gates reuse on the stamp; say if you want it added here.

## Posting commands (owner go required — never run these without it)

```zsh
D=$(mktemp -d /tmp/pr1020.XXXXXX) && cd "$D" || exit 1
cp /Users/naz/Projects/ds4/.codebase-memory/20261121-prdrafts/reply3.md r3.md
# write r1.md and r2.md from the two blocks above, then:
wc -c r1.md r2.md r3.md            # all nonzero, before any network call
gh api repos/antirez/ds4/pulls/1020/comments/4131755372/replies -F body=@r3.md --jq '{id,html_url}'
# STOP. Open the returned html_url and confirm backticks and non-ASCII survived. Then:
gh api repos/antirez/ds4/pulls/1020/comments/4131726056/replies -F body=@r1.md --jq '{id,html_url}'
gh api repos/antirez/ds4/pulls/1020/comments/4131745125/replies -F body=@r2.md --jq '{id,html_url}'
```

After posting, re-read the live text of all three via the API and compare to these bodies — the project
rule is to verify what actually landed, not what was sent.

## Not verified
Whether `-F body=@file` succeeds in practice; whether GitHub preserves the backticks and non-ASCII
exactly; whether additive vocab is a supported upstream workflow; whether `a1f3bd2` can port onto the PR
branch cleanly; and the earlier draft's two claims that "older binaries ignore bit 5" and "bit 4 is free
at base", which the reviewer did not re-run.


## Second review 2026-11-21 (`openai/gpt-6.2-sol#low`): all three REVISED again, still UNPOSTED

The bodies in `reply1.md`/`reply2.md`/`reply3.md` are now the reviewer's replacements, not the earlier ones
described above. What changed and why:

- **Thread 1**: the reviewer asked three things — whether the probes are necessary, *how they were chosen*,
  and when to update them. The previous draft answered only "when". It now says the list was hand-picked for
  JoyAI cases (apostrophes beside punctuation, digit runs, indentation, newlines, UTF-8) and to **append**
  rather than edit/reorder.
- **Thread 2**: dropped the "author-maintained generation field" prescription (speculative advice the
  reviewer did not ask for) and **dropped the offer to port our fix** ("say if you want it added here"),
  which volunteered work and invited a rebase of someone else's PR. The store-side gap disclosure stays,
  restated as fact with a scope note: `I have a separate fix … it's not part of this PR`.
- **Thread 3**: **my "the comment trails off mid-sentence" was an inference from an ellipsis.** The actual
  body is `It's nice the header allows this extension via \`ext_flags\`...` — a statement, not a cut-off
  question. It no longer asserts anything about truncation, and asks plainly.

Reviewer-verified facts adopted: at PR head, `ds4_kvstore.c:701` tokenises the suffix *after* a cache hit
(it does not "key the suffix", and does not prove every request passes through it); renumbering token ids
*does* move the hash via the ID-ordered table; the loader returns a miss on mismatch without deleting
(`ds4_kvstore.c:1307-1317`); **bit 4 is genuinely unused upstream** (verified independently against
`b3f5018` and `9cbf7e5`), and the old-reader tool-map behaviour is supported by the *base*, not just the PR
head — so it must be scoped to "pre-PR base, otherwise-compatible checkpoint", not offered as an
upgrade/downgrade round-trip guarantee.

## Posting mechanics: the read-back step was broken

`GET /repos/antirez/ds4/pulls/1020/comments/{id}` returns **404** for all three ids even though the list
endpoint returns them (`path=ds4.c line=65007/65040`, `path=ds4_kvstore.c line=1290`). So the previously
planned "re-read the live text via the API" step cannot use that route. Verify with the **list** endpoint
(filtered by id) or by the `html_url` the POST returns.
