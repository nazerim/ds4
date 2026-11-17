# Post: comment on antirez/ds4 PR #1020

Target: https://github.com/antirez/ds4/pull/1020 — *kvstore: stamp checkpoints with a
behavioral tokenizer fingerprint*. Ours, open since 2026-09-10, `MERGEABLE`, last
substantive activity emilianbold's `COMMENTED` review 2026-09-29 + our reply 2026-10-01.
Verified structure notes: `origin/main` has exactly four `DS4_KVSTORE_EXT_*` bits; #1343
takes `1u << 4` for an image section counted inside the header's `payload_bytes`; this PR
takes `1u << 5` for a trailer section that skips with `ftell` + `payload_bytes`; both edit
`ds4_kvstore_try_load_text` at hunk base `@@ -1277`.

---

@emilianbold three short things, then I'll leave this quiet until one of you has time.

**1. Which representation did you mean?** Your review said *"we even have a way to
represent it"*, and I read that as the `ext_flags` bitmask in `ds4_kvstore.h` (four bits
in use at `main`: `TOOL_MAP`, `RESPONSES_VISIBLE`, `THINKING_VISIBLE`, `SESSION_TITLE`).
This PR builds on exactly that: `DS4_KVSTORE_EXT_TOKFP (1u << 5)`, marking the first
trailer section, which the loader can reject before it touches the payload — no new header
field, no key-kind change. If you meant a different mechanism, name it and I'll move it.

**2. One decision is still open from my Oct 1 reply:** keep weight-identity invalidation
out of this PR (the probe detects bytes-to-token-id changes only, and I'd rather not let
the field imply it can see a weight swap it cannot), or split weight identity into a
second change. I'd rather be narrowly wrong here than widen this one.

**3. I checked the trailer traffic before re-pinging.** #1343 puts its image section
inside the header's payload span right after the stored text, so code that skips a payload
to reach the trailer needs no knowledge of it; this PR's fingerprint is a trailer section
behind that, magic-and-length framed with a skip path. So the bits don't collide and the
sections don't compete for a slot. What does collide: both of us edit the same stretch of
`ds4_kvstore_try_load_text` around line 1277, and this PR anchors its skip with `ftell`
where #1343 reads. Whichever lands second has to reconcile the two reads rather than
assume the position — happy to rebase #1020 onto #1343 and verify the skip lands right. Say
the word and I'll do it; no review time needed from you either way.

Not asking for bandwidth now — #1340/#1342/#1323/#1343 are all live in this file and look
more urgent than this.
