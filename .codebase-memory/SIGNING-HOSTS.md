# Commit signing: which hosts, which keys, how to verify

Two hosts sign commits for `nazerim@gmail.com`. Both keys are registered on GitHub as
**signing** keys, so commits from either show `Verified`. Both hosts trust both keys locally,
so `git log --show-signature` works cross-host.

| host | key fingerprint | `allowed_signers` | notes |
|---|---|---|---|
| Mac (this repo's main box) | `SHA256:Oo2y6bRT693jrm5EYHR9Rm866osBiKku51s8UAM2QYk` | `~/.config/git/allowed_signers` (2 lines) | key `~/.ssh/github_signing`, `namespaces="git"` since 2026-11-19 |
| `naz@nazryzen.lan` (Ubuntu 26.04.1) | `SHA256:NHUhqR2opA+Kl/EeQNjmkYRCXztn4BWC71EVFpwM754` | `~/.config/git/allowed_signers` (2 lines) | generated 2026-11-19, ed25519, **no passphrase**, mode 0600 |

## Config that must be present on a signing host (global)

```
gpg.format                 ssh
user.signingkey            ~/.ssh/github_signing.pub   # the .pub, not the private key
commit.gpgsign             true
gpg.ssh.allowedSignersFile ~/.config/git/allowed_signers
user.email                 nazerim@gmail.com           # must be a verified email on the account
```

`allowed_signers` is one line per key: `<principal> [namespaces="git"] <type> <base64> [comment]`.
Principal must equal the commit author email or verification fails.

## The correct verification probe

```sh
gh api "repos/nazerim/ds4/commits?sha=<sha>&per_page=1" \
  --jq '.[] | "\(.commit.verification.verified) \(.commit.verification.reason)"'
# control (probe must return false here, or it proves nothing):
gh api "repos/antirez/ds4/commits?per_page=3" --jq '.[] | .commit.verification.verified'
```

- `.commit.verification.{verified,reason}` **is** the signature field. `commits[].status` is
  **CI check-runs** and is legitimately empty — reading it is what produced the false
  "key not registered, operator action needed" claim for two months
  (`20261117-kv/SIGNATURE-NOTE.md` keeps that trail under a correction banner).
- A 404 from `gh api user/keys` / `user/ssh_signing_keys` demanding `admin:public_key` /
  `admin:ssh_signing_key` proves only that the token cannot **list** keys. Both current tokens
  have `gist, read:org, repo` (+`workflow` on nazryzen) and cannot register keys.
- Registration of a new key is a **browser** step: `github.com/settings/ssh/new`, key type
  **Signing** (default is Authentication, which never verifies commits).

## Adding another host

1. `ssh-keygen -t ed25519 -C "nazerim-github-signing@<host>" -f ~/.ssh/github_signing -N ""`
   — generate fresh, never copy a private key between hosts.
2. The five config keys above.
3. Register the **public** key on GitHub as type *Signing* (browser, see probe section).
4. Append that public key to `allowed_signers` on **every** signing host, **with
   `namespaces="git"`** — an option-less line silently trusts the key in every namespace, not
   just git's. Cross-host `git verify-commit` otherwise fails with "Can't check signature: No
   public key" while GitHub still shows Verified.
5. Prove it end-to-end: sign a commit on the new host, `git bundle create b.bundle HEAD`,
   copy the bundle, `git fetch <bundle> HEAD && git verify-commit FETCH_HEAD`, delete both.
   Do **not** push a probe commit to `main` to test signing; a branch is fine but is
   unnecessary — the bundle path tests the same thing with no remote footprint.

## Host-specific gotchas (nazryzen.lan)

- `gh` is at `~/.local/bin/gh` and is on **login** shells only (`bash -lc`); a bare `ssh host cmd`
  gets `command not found`. The credential helper in `~/.gitconfig` uses the *other* copy,
  `/mnt/scratch/naz/.local/bin/gh` — both paths exist, so pushes work either way.
- Push auth is a **URL-scoped** helper: `credential "https://github.com".helper = !…gh auth
  git-credential`. `git config --global --get credential.helper` returns UNSET — the generic key
  really is empty. Query `git config --list --show-origin | grep credential` instead, or you will
  wrongly conclude pushes are unauthenticated.
- `ssh-keygen -lf <pubkey>` is the only reliable validity check. Do not trust a regex like
  `^[A-Za-z0-9+/]+=$` on the base64: ed25519 keys are 68 chars with **no** `=` padding.
- GitHub rejects a pasted key with "must supply a key in OpenSSH public key format" when the
  paste contains a soft-wrap newline. Pipe it: `ssh naz@nazryzen.lan cat ~/.ssh/github_signing.pub | pbcopy`,
  then check `pbpaste | wc -l` is `1` before submitting.

## Namespace restriction (resolved 2026-11-19)

All four principals lines (2 hosts × 2 keys) carry `namespaces="git"`.

The Mac's original line had no `namespaces` option. That does **not** mean "git-only by default":
it means the key is accepted in *any* namespace a tool asks about. Measured, not assumed — signing
a plain file with the key and verifying it against `allowed_signers` **succeeded** before the edit,
i.e. the commit-signing key was usable as a general-purpose identity assertion. After the edit that
verification is **rejected**, while `git verify-commit HEAD` and a nazryzen-signed commit still
pass. Those PASSes are also the empirical proof that git asks namespace `git` for its own
signatures, so nothing in the commit/tag/push-cert paths depends on a different namespace.

Re-run this trio after any edit to a trust file; the only correct result is PASS, PASS, REJECT:

```sh
git verify-commit HEAD                    # namespace git, own key
# + a bundle from the other host, fetched into a THROWAWAY repo: git verify-commit FETCH_HEAD
printf hi > /tmp/m
ssh-keygen -q -Y sign -n files -f ~/.ssh/github_signing -s /tmp/m.sig /tmp/m
ssh-keygen -q -Y verify -f ~/.config/git/allowed_signers \
  -I nazerim@gmail.com -n files -s /tmp/m.sig < /tmp/m && echo "ACCEPT <- too broad" || echo "REJECT <- restricted"
```

Undo: `cp ~/.config/git/allowed_signers.bak-ns-20261119 ~/.config/git/allowed_signers`.

Unrelated gotcha surfaced by this test: `git for-each-ref --format='%(objecttype)' refs/tags/x`
returning `tag` means **annotated**, not **signed**. This repo's only tag object
(`pre-upstream-merge-2026-08-31`) carries zero signature bytes, so `git verify-tag` fails on it
identically before and after the edit and proves nothing. Check
`git cat-file tag <t> | grep -c 'BEGIN\|signature'` before calling a `verify-tag` failure a regression.
