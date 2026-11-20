# 2026-11-20 — TOKFP restamp: store-path reuse bypassed the fingerprint guard

> **STATUS 2026-11-21 01:05 — REVIEWED, DEPLOYED AND VERIFIED.** Fix commit `a1f3bd2`; production PID
> 38939 runs it. Evidence: verifier `launder` on the pre-fix binary → **LAUNDERED** (same inode, stamp
> restored over stale rows, i.e. the bug reproduced on real files) and on the fixed binary → **BLOCKED**
> (checkpoint replaced with a fresh store); `parity` cold+warm greedy output identical PRE vs POST; and a
> live replay against the untouched 246 GiB production cache returned the same completion sha256 as the
> pre-fix baseline (`82d85489147d986e…`), logged `kv cache hit … chain=3 load=46.3 ms` with the sentinel
> file's **inode unchanged** (reused, not rewritten) and **zero** `stamp unreadable` lines. That last
> result also closes the review's biggest unproven item: the stamp parser positions correctly on real
> v3 chained checkpoints, not only on the hand-built v2 stubs the unit suite uses. The guard may now be
> described as fail-closed for the reuse path — with the nuance that an unreadable stamp *declines reuse
> and keeps the file*, and repair happens via the next atomic store rather than in place.
**Status: fix implemented 2026-11-20, UNCOMMITTED at this writing** (the working tree carries
it in `ds4_kvstore.c` / `ds4_kvstore.h` / `ds4_server.c` — that modification is the fix agent's,
not doc drift). **Pending independent review + deploy.** Not merged: PR antirez/ds4#1020's
head `9cbf7e59` is still just base + fingerprint feature (its latest commit is a merge of
`origin/main`). Not deployed: the running engine predates every fix build. **Until the fix is
reviewed, deployed and verified, do not describe the tokenizer-fingerprint guard as
fail-closed in any doc, PR reply, or public text.** It was fail-open in two independent ways
until today's patch; the patch closes both but has not yet been executed in production.

Reachability was never theoretical: `--kv-disk-dir` is live on the production engine (PID
29866's arg list, §Live state drift below).

## Provenance of line anchors — do not repeat the review's mistake

The defect was found reviewing **PR head `9cbf7e59`** (= upstream `b3f50180` + #1020's ~180
added lines). The original review-revision numbers — `ds4_kvstore.c:849-870`, `889-927`,
`1003-1010`, `1280-1320`, `1290-1307`; `ds4_server.c:11283`, `11316` — belong to **that tree
only**. Fork `main` carries a ~3× larger `ds4_kvstore.c` with renamed functions; quoting the
PR-head numbers against the fork is a category error (a session did exactly that on 2026-11-20
and it was caught before anything shipped — the reason this section exists). The chain was
**verified in both trees**: at PR head the numbers above hold, and the fork-equivalents were
re-located by symbol on fork `main` HEAD `5c55542` (the pre-fix tree, clean). Post-fix
coordinates refer to today's **uncommitted working tree** and will move on commit. Anchor on
the **symbols**.

## The defect, in one paragraph

The load path validates disk-KV checkpoints against a behavioural tokenizer fingerprint
(PR antirez/ds4#1020, ours, open). The **store** path never consulted it: when a same-text
checkpoint file already existed on disk, the store took a compatibility shortcut that judged
reuse from text sha1 + bytes + model/quant/ctx only, then rewrote the trailer in place — and
the trailer rewrite, via the server hooks, **re-stamped the untouched stale payload with the
current fingerprint**. So after a tokenizer-behaviour change, for the same prompt text: load
rejects → re-prefill → store keeps the stale rows, discards the fresh ones, and certifies the
stale payload as valid. Every later load then validates. The bypass happened **once,
permanently**.

## Evidence chain (fork pre-fix `5c55542` numbers primary; PR-head twins in parens)

1. **Reuse judge skipped the fingerprint.** `ds4_kvstore_existing_compatible()`
   (`ds4_kvstore.c:2972-3018`; PR head: `kv_cache_existing_compatible()` `849-870`) compares
   model_id, model_fp (**weight** fingerprint, not tokenizer), quant, ctx and
   `kv_cache_file_text_matches()` — zero tokenizer-fingerprint or `ext_flags` consultation in
   the function. #1020 does not patch it, so the PR-head numbers coincide with the base
   `b3f50180` there.
2. **The store took the shortcut.** `ds4_kvstore.c:3210 → 3217` (PR head `1003-1010`):
   compat true ⇒ `kc->stat_reused++`, `ds4_kvstore_rewrite_trailer()`, `return true` — the
   fresh rows from the just-completed re-prefill were never staged.
3. **The trailer rewrite never touched the payload but rewrote the header.**
   `ds4_kvstore_rewrite_trailer()` (`ds4_kvstore.c:3052+`; PR head `889-927`): seek to
   header+text+payload end, `ftruncate` the old trailer only (`3075`), re-serialize the
   trailer through the server hooks, rewrite the header with
   `hdr.ext_flags | hooks->ext_flag` (`3082`).
4. **The hooks stamped the CURRENT fingerprint, unconditionally.** Hook flag
   `KV_EXT_TOKFP | KV_EXT_TOOL_MAP` (`ds4_server.c:12718-12720`; PR head `11316`); write
   callback `kv_cache_trailer_write_cb` emits the TKF section with `tok_fp =
   ds4_engine_tokenizer_fingerprint(cx->s->engine)` (`ds4_server.c:12633`; PR head `11283`),
   no guard; the size callback always reserves `KV_TOKFP_BYTES`.
5. **The fingerprint was consulted at exactly one decision site.** In the pre-fix tree
   `ds4_engine_tokenizer_fingerprint` (def `ds4.c:65718`, decl `ds4.h:720`) had exactly two
   KV-path calls: the load-side check `kv_file_tokfp_ok()` (`ds4_kvstore.c:2758`) reached via
   the load gate `ds4_kvstore.c:3780` (PR head: check at `~1307`), and the stamping hook at
   4 above. Not one inside the reuse judge — the asymmetry *was* the defect.

Loop: tokenizer-behaviour change → load-side reject (gate `ds4_kvstore.c:3770-3788`,
`retryable=true`, session untouched) → re-prefill → same text sha1 → same path exists →
shortcut per 1-4 → the stale file now carries a valid current stamp. The first rejection was
the last one that ever happened for that file.

## Hole #2 — fail-open on an advertised-but-absent TKF section (shapes differ per tree, semantics the same)

- **Fork `main` (pre-fix)**: the single parser `kv_file_tokfp_ok()` (`ds4_kvstore.c:2739-2762`)
  returns `ok = true` with `*present = false` when the TKF section is missing, truncated or
  unreadable — and the **load site (`ds4_kvstore.c:3780`) passed `NULL` for `present`**, so
  the claim was structurally ignored: a checkpoint whose header advertises the TOKFP bit but
  whose stamp cannot be read **loaded unvalidated**.
- **PR head `9cbf7e59`**: the load check is the inline `if (have_fp && tokfp !=
  ds4_engine_tokenizer_fingerprint(engine))` (`ds4_kvstore.c:1290-1307`) — same fail-open
  (no readable stamp ⇒ no rejection), different code. Do not quote either shape against the
  other tree.

## The fix (implemented 2026-11-20, in the uncommitted working tree)

Design — **one shared verdict** over the existing single parser, consulted at both decision
points so the two can never redefine "what does this file's stamp say" differently:

- `kv_file_tokfp_verdict_ok(ext_flags, match, present)` — `ds4_kvstore.c:2775`: a mismatching
  TKF section rejects **claimed or not**; TOKFP bit set with no readable section rejects
  (fail-closed on the claim); unclaimed legacy files stay trusted. `engine == NULL` means no
  current fingerprint to compare against (unit-test path).
- Exposed two ways: `kv_file_tokfp_gate()` for an already-open `FILE *` (`2783`, load side)
  and `kv_file_path_tokfp_ok()` for a path (`2793`, reuse side — "a file whose stamp cannot be
  read at all is not reusable: the caller replaces it and the full store re-earns the
  envelope").
- **Consulted BEFORE the reuse decision**: `ds4_kvstore.c:3040-3042` inside the compat judge —
  stale-stamp files are replaced, never reused-and-restamped. New engine-aware public variant
  `ds4_kvstore_existing_compatible_for_engine()` (impl `3091`, decl `ds4_kvstore.h:424`) used
  by the production store path (call site `ds4_kvstore.c:3292`); the old signature
  (`ds4_kvstore.h:416`) is retained for engine-less callers.
- Load gate converted to the shared verdict: `ds4_kvstore.c:3864` (`kv_file_tokfp_gate` with
  `hdr.ext_flags`).

Evidence (fix author, 2026-11-20; **not yet re-run independently — that is the pending review**):

- **RED**: `ds4-server tests: 6 failure(s)` with the test present and the fix absent — the 3
  reject cases × 2 asserts (stale stamp; claimed-but-unreadable stamp; mismatching section
  without a claim) around `ds4_server.c:27619-27648` (author cites lines 27621, 27622, 27634,
  27635, 27647, 27648).
- **GREEN**: `ds4-server tests: ok` after patching; `make ds4-server` rc=0.
- Tests: `test_kv_cache_store_compat_rejects_stale_tokfp` (`ds4_server.c:27588`, registered
  `29863`) + stub writer `test_kv_tokfp_stub_file` (`27548`, 5 scenarios:
  claimed+stale / claimed+matching / claimed+absent / unclaimed+absent / unclaimed+mismatch).
- Live-check script: `20261118-kvstorm/verify_tokfp_laundering_fix.sh` — before/after modes;
  proves laundering by XOR-ing the 8 TKF payload bytes of a live checkpoint and asserting the
  file is replaced via temp+rename (**inode change = PASS; same inode with a silently
  restored stamp = LAUNDERED**). `#!/bin/zsh`, clean under `zsh -n`, fails `bash -n`/`sh -n` —
  run it with zsh, do not "fix" it for bash. **Syntax-checked only, NOT yet run; needs an
  engine-stopped window, coordinator-scheduled.**

Bar for re-lifting the fail-closed wording: verdict independently reviewed → committed → in
the deployed binary → `20261118-kvstorm/verify_tokfp_laundering_fix.sh` PASS in after mode on
live cache data → RED repro impossible on the shipped binary.

## Retracted during this investigation — do not re-report

- ~~"the probe exercises the `<｜User｜>` special-token branch"~~ — the probe string at
  `ds4.c:65702` is `"<\xe2\x80\x82User\xe2\x80\x82>…"` — **U+2002 EN SPACE**, not the special
  token. Production vocab rows match U+FF5C (`｜`): 12 lines, all `ds4.c:45775-45987`; U+FF5C
  occurrences beyond line 50k: **0**. The probe never entered the special-token path.
- ~~"the fork's unterminated-tool-call fix is a strict superset of PR #1163"~~ — the
  production call site `ds4_server.c:17187` passes `NULL, NULL, NULL` for
  content/reasoning/calls outputs, so the helper's re-parse branch
  (`degrade_unterminated_tool_call`, def `ds4_server.c:7401`, gate `if (content_out &&
  reasoning_out && calls_out)` at ~`7420-7425`) never executes in production. Production
  behaviour is strip + finish-map + clear-error only — not a superset.
- ~~"`strings ds4-server | grep -c DS4_KVSTORE_EXT_TOKFP` → 0, so the flag is absent from the
  running image"~~ — **proves nothing**: `#define` macro names are not in a binary's string
  table. The same technique IS valid for **literal runtime strings**: `strings ds4-server |
  grep -c disable-kv-cache-cold-stop-parity` → 1 hit, and that is what legitimately proved the
  cold-stop-parity mitigation was in the running image. Rule: strings-probe literals, never
  macro names.

## Collision watch — upstream #1343

antirez/ds4#1343 (fitchmultz, open, base `b3f50180`) "Save image conversations in the disk KV
cache" is a **parallel implementation of vision-in-disk-KV**: 11 files, 7 hunks in
`ds4_kvstore.c`, and it defines `DS4_KVSTORE_EXT_VISION (1u << 4)` — identical macro text to
ours (`ds4_kvstore.h:40`), so no compile-level collision. It will conflict textually wherever
it overlaps the fork, which is everywhere that matters: measured fork divergence vs that base
is `ds4_server.c` +7162/−570, `ds4_kvstore.c` +2942/−199, `ds4.c` +2415/−175 (at `5c55542`).
It also forces a format choice against our fork-local `vision_store` trailer (25 refs in
`ds4_server.c`). Verified by the coordinator: its diff applies cleanly onto pristine
`b3f50180` (rc=0) and fails on our tree (rc=1).

## Live state drift — measured 2026-11-20 late night; re-verify before trusting any number

Yesterday's handover numbers are stale. Measured this minute where marked; re-run the probes
before acting.

- Old engine PID 65953 is gone. New server: **PID 29866**, started Fri Nov 20 **23:39:30**
  (`ps -o lstart`, measured), `listening on http://127.0.0.1:8002` at 23:39:32
  (`log/ds4-qwen.log`, measured).
- The actual shutdown, from the rotated log `log/ds4-qwen.log.20261120-233930` (measured):
  `1120 23:39:28 shutdown requested`, `persisting resident KV cache before shutdown slot=0
  tokens=53788`, `slot=1 tokens=83198`. **The sweep-transcript quote (shutdown 23:05:32,
  `tokens=83676`, `size=1121`) does not match the qwen prod log**: 23:05:32 was ordinary
  traffic and `83676` appears only in the older `log/ds4.log` / `log/ds4-v41.log` — a
  cross-file conflation. Re-verify quoted log lines against `log/ds4-qwen.log*`.
- On-disk `ds4-server` mtime `2026-11-20 23:41:43` — **newer than the running process start**
  (measured `ls -l`) ⇒ the live image is not the newest build on disk.
- KV dir `/Volumes/FireCuda520/ds4-kv-qwen` went **753 → 709** files (709 measured tonight).
  Cause NOT established — candidates include the shutdown resident-persist and eviction
  pressure; do **not** invent one.
- Running args include `--ctx 524288 --tokens 65536 --mixed-prefill-quantum 512
  --kv-cache-retire-grace-seconds 3600 --kv-cache-max-divergence-anchors 8` (flag presence
  verified via `ps -p 29866 -o args`; values per the sweep).

## Re-verification recipe (read-only)

```sh
# PRE-FIX tree only (HEAD 5c55542) — the shortcut had no fingerprint check:
git show 5c55542:ds4_kvstore.c | sed -n '2972,3018p' | grep -c tokenizer_fingerprint   # expect 0
# POST-FIX working tree — the reuse side consults the shared verdict:
grep -n "kv_file_path_tokfp_ok\|kv_file_tokfp_gate\|_for_engine" ds4_kvstore.c ds4_kvstore.h
# the PR-head numbers (849/889/1003...) must NEVER be grep'd against fork main:
git show origin/main:ds4_kvstore.c | grep -n "kv_cache_existing_compatible\|kv_cache_rewrite_trailer"
```

Related: `HANDOFF-20261119-KV-CHUNKSCHEDULE.md` §3 + §6 rows, `DS4FORK.md` divergence entry,
`20261120-PR-SWEEP.md` (live PR states incl. #1020).
