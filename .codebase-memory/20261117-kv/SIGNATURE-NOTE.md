# Signature verification: pre-existing account state, not a regression

> **THE CONCLUSION BELOW IS WRONG — corrected 2026-11-19 by direct measurement.** The signing key
> **is** registered as a signing key and GitHub **does** verify these commits:
> `.commit.verification.verified = true`, `reason = valid`, on 11 commits from 2026-08-31 through
> HEAD, **including `dcf77c4`, the commit this note is about**. Control: upstream
> `antirez/ds4` commits return `verified = false`, so the probe distinguishes the two states.
>
> Two measurement errors produced the wrong conclusion:
> 1. **Wrong field.** `commits[].status` is the commit **status/check-runs** context (CI), not
>    signature verification. It is legitimately empty for a repo with no CI. The signature field is
>    `commits[].commit.verification.{verified,reason}` (or the single-commit form
>    `.commit_verification`). "status empty" was read as "no Verified badge".
> 2. **Absence of evidence.** `gh api user/keys` and `user/ssh_signing_keys` returning HTTP 404
>    demanding `admin:public_key` / `admin:ssh_signing_key` proves only that **this token cannot
>    enumerate keys** — not that no key is on file. The note even stated the 404 as the reason the
>    scopes were "not added", then inferred registration status from it anyway.
>
> Correct probe (no extra scopes needed, `repo` is enough):
> ```sh
> gh api "repos/nazerim/ds4/commits?per_page=5" \
>   --jq '.[] | "\(.sha[0:8]) verified=\(.commit.verification.verified) \(.commit.verification.reason // "-")"'
> # control: same call against repos/antirez/ds4/commits returns verified=false
> ```
> The note's other claim is false too: the head commits of #1020 (9cbf7e59), #1331 (f1a1aa68),
> #1022 (8da90edc) and #1358 (dcf77c4c) all return `verified=true reason=valid` -- so it was never
> "every open PR from this account" either.
> Nothing was ever wrong with the key, the config, or `allowed_signers`. No operator action is
> required. Original text retained below as the trail.

---


The commit on antirez/ds4#1358 (`dcf77c4`) is SSH-signed and verifies locally
(`git log --show-signature`, `%G?` = `G`, key `SHA256:Oo2y6bRT…` bound to
nazerim@gmail.com via `~/.config/git/allowed_signers`). GitHub nevertheless shows **no
Verified badge** (`commits[].status` empty).

This is not new to this PR. Every open PR from this account reports the same empty status
(#1020, #1331, #1022), so the SSH signing key has never been registered on the GitHub
account as a signing key — GitHub only renders signatures it can match to a key on file.

Scope check attempted: `gh api user/ssh_signing_keys` and `user/keys` both return 404
requiring `admin:ssh_signing_key` / `admin:public_key` scopes, which were **not** added —
widening account auth scopes is the operator's action, not the agent's.

Fix, if wanted: add `~/.ssh/github_signing.pub` under Settings → SSH and GPG keys as a
**signing key** (`gh ssh-key add ~/.ssh/github_signing.pub -t signing`, which needs the
scope above). Cosmetic on GitHub — the commits are genuinely signed either way, and
anyone can verify locally with the allowed-signers file.
