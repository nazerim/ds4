# Signature verification: pre-existing account state, not a regression

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
