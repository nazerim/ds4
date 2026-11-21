# #1020 port prep — the store-side fingerprint gate (PREP ONLY, nothing committed to it)

Rev 2, 2026-11-21. Rev 1 carried two wrong load-bearing claims, both refuted by an independent
`claude-agent/sonnet#low` pass and re-verified by me before adoption. Corrections are marked **[rev 2]**.
**No branch, no commit, no push, no public statement of intent** — that requires owner go.

## 1. Lineage — our own code (authorship, not just content)

| fact | evidence |
|---|---|
| Fork TOKFP originates at `81a526c` | `git log --reverse -S'DS4_KVSTORE_EXT_TOKFP'` |
| Our PR **#983** (closed 2026-09-09) = that work, `+177/−3`, 5 files | head `e692449` |
| PR **#1020** opened **2026-09-10**, also `+177/−3`, same 5 files | API set-compare: 177/177 added lines identical, 0 unique either side |
| **Both authored by `nazerim`** | **[rev 2]** this is what licenses "our own resubmission" — identical content alone would only prove same-patch, not same-submission |
| `9cbf7e5` is a **merge commit** (origin/main merged into the PR branch, 2026-11-06) | so PR head ≠ #983 head: 12 lines differ in `ds4_kvstore.c`/`.h` |

Lineage is **not load-bearing** for §3–§6, which stand or fall on the code at `9cbf7e5`.

## 2. On-disk format agreement — v1 ONLY **[rev 2]**

Both trees use `TKF\1`, len 8, 8-byte LE fingerprint, and the same seek expression
(`payload_start = ftell(fp)` then `fseek(payload_start + payload_bytes)`) — because `read_header` leaves `fp`
positioned after the text.

**Qualifier, and it matters:** the PR defines `KV_CACHE_VERSION 1u` (`ds4_kvstore.c:28`) and its `read_header`
rejects any other version (`h[3] != KV_CACHE_VERSION → return false`). It has **no v2/v3 headers, no delta, no
parent chains**, and `kv_cache_rewrite_trailer` hard-codes `FIXED_HEADER + 4 + text_bytes + payload_bytes`.
So:
- Agreement holds for **full v1 files only**.
- Fork-written v2/v3 files are **rejected outright** by a PR-head build — fail-closed (a cold prefill), not a
  misparse. Good news, and it means a port onto the PR cannot corrupt our cache; but any *shared* cache
  directory between the two builds silently loses all hits.
- The fork additionally runs the stamp check on the **parent link** (`kv_file_tokfp_read(ph)` at `:2878`,
  `:4036`) — a concept with no counterpart at PR head.
- Delta placement is read from code, **not measured on a real delta file**.

## 3. The delta, sized by arithmetic

`a1f3bd2` = **+1133/−31**, of which **425 lines are the deploy verifier `.sh`** (stays fork-local, must not be
proposed upstream) and **419 lines in `ds4_server.c` are entirely tests** (hunks +412 at `:27542`, +7
registration at `:30162`). Production code is therefore `ds4_kvstore.c +232/−31` and `ds4_kvstore.h +57`
= **+289**, or **~174 non-comment/non-blank added lines**.

**[rev 2]** Rev 1 said "~60–90 production lines". That number was derived from nothing — I pattern-matched
"server.c is mostly tests" onto the whole commit. Correct planning figure: **~100–170 lines**, smaller than
174 only to the extent we decline to mirror fork-only API.

Not portable to the PR (no counterpart there): chained-parent handling (`kv_store_pick_parent`, the `:4036`
hunk) and the test-only exports (`_for_tokfp`, `_for_engine`).

## 4. What the port actually has to change

| piece | fork source | requirement |
|---|---|---|
| tri-state verdict | `ds4_kvstamp_verdict` (`ds4_kvstore.h:423-429`), `kv_file_tokfp_read` (`ds4_kvstore.c:2741-2799`) | `MATCH`/`MISMATCH`/`UNREADABLE`. A boolean cannot distinguish "stamp equals current" from "unreadable so nothing contradicts us" — the exact confusion being fixed |
| reuse gate | PR `kv_cache_existing_compatible` **`ds4_kvstore.c:849-871`**, unlinks at **`:863`** inside `if (!compatible)` | see ordering below |
| store-abort rule | `ds4_kvstore_stamp_store_aborts` (`:3104-3111`) | abort **only** on `UNREADABLE && cur_fp == UNKNOWN`; repair via the next atomic store, never in place |
| call sites | compat `:1003`; rewrite call `:1006` (inside that `if` body); engine param is **`:931`** (`:930` is the function name); loader mismatch-return block **`:1307-1319`** | **[rev 2]** all anchors are in **`ds4_kvstore.c`** |

**Two call sites, not one.** Rev 1 missed the second: at `:1307` the loader's `have_fp` test **falls through
when the stamp is unreadable**. That is the fork's "second hole" and must take the verdict too.

**[rev 2] Plumbing IS required.** Rev 1 claimed "engine already in scope, no plumbing needed" — wrong. The
callee's signature is `(path, sha, text, text_len, model_id, quant_bits, ctx_size)`: it receives **neither
engine nor fingerprint**. The caller at `:1003` has `engine`; the callee must gain a parameter.

**Ordering that avoids restructuring** (verified achievable): compute `compatible` from the existing checks,
read the verdict **only when `compatible` is true**, then
- `UNREADABLE` → `return false` **before** the `if (!compatible)` block, so the unlink at `:863` never runs;
- `MISMATCH` → set `compatible = false`, falling into the existing unlink-and-replace route (correct: verified
  stale);
- `MATCH` → proceed as today.

Getting this wrong trades a correctness bug for an availability one: unlinking an unverifiable-but-possibly-valid
checkpoint means a cold prefill on every store until one succeeds.

## 5. Tests **[rev 2 — rev 1 was wrong]**

**Rev 1 claimed upstream never compiles the in-tree server tests. REFUTED.**
`tests/ds4_test.c` **self-defines** the macro and textually includes the server:

```c
#define DS4_SERVER_TEST            // tests/ds4_test.c:1
#define DS4_SERVER_TEST_NO_MAIN    // :2
#include "../ds4_server.c"         // :4
...
    ds4_server_unit_tests_run();   // :7314, reached by {"--server", ...} at :7347
```

Same two defines exist on upstream `origin/main`. So a test written inside `#ifdef DS4_SERVER_TEST` in
`ds4_server.c` **does compile and run upstream** via `./ds4_test --server` — which requires a model. The
fork's separate model-free `ds4_server_test` target is a convenience, not a prerequisite, and **no new test
target needs to be proposed**. Rev 1's "a bigger ask than the gate itself" is withdrawn.

**Probe-hygiene lesson:** "`DS4_SERVER_TEST` appears zero times in the Makefile" is a true observation that
proves nothing, because the macro is defined in the *test source*. Absence in the file you chose to grep is
not absence in the build.

Unresolved, needs checking separately: our own `AGENTS.md` says `ds4_test --server` "does NOT cover server
code (it links `ds4.c`)". That is contradicted at PR head and upstream. Either it describes a fork-specific
link difference or it is stale — and AGENTS.md is injected into every session, so a wrong line there keeps
re-seeding this error.

## 6. Bit 4 vs #1343 — real merge-time incompatibility **[rev 2, reversal]**

`#1343` (`fffc283`) `ds4_kvstore.c:889-893`:
> *"Image identity section, present with `DS4_KVSTORE_EXT_VISION`: a u32 image count, then token start, row
> count and fingerprint of each image… It sits **in front of the engine payload and is counted in the header's
> payload bytes**…"* — written by `kv_vision_write` (`:901`), read by `kv_vision_read` (`:923`).

So it lives **in `ds4_kvstore.c`**, **inside `payload_bytes`**, and carries **no magic** (bare u32 count). Our
vision section is the opposite: `KVI…` magic (`ds4_server.c:11826-11828`), in the **trailer**, read by
`ds4_server.c`.

Therefore a #1343-written file read by our reader is **not** "skipped on a magic mismatch" — our code would
hand those bytes to `ds4_session_load_payload` and **misparse the payload**, with no magic check to catch it.
**Rev 1's "the §23 migration trigger is probably overstated" is retracted: §23's trigger stands.** Rev 1's
grep for `KVI` correctly found nothing; the inference "so it lives in the server hook" was the error.

Bit 5 remains consistent by construction (§1). The real conflict is exactly one bit, and it is a **payload
layout** conflict, not a flag-byte cosmetic.

## 7. Shape of the change

```
ds4_kvstore.c
  + typedef enum { … } ds4_kvstamp_verdict
  + static ds4_kvstamp_verdict kv_file_path_tokfp_read(const char *path, uint64_t cur_fp)
  ~ kv_cache_existing_compatible(path, sha, text, text_len, model_id, quant_bits, ctx_size,
  +                            uint64_t cur_fp /* NEW: caller has engine at :1003 */)
      … after existing checks, only if compatible: verdict = read(path, cur_fp)
        UNREADABLE -> return false            (before the :863 unlink)
        MISMATCH   -> compatible = false      (existing unlink/replace)
  ~ loader :1307 — also branch on the verdict instead of falling through when unreadable
```

Verification pair when executed: `verify_tokfp_laundering_fix.sh launder` must report **LAUNDERED** for a
`9cbf7e5` build and **BLOCKED** for the ported build, on the same fixtures. Reuse the existing selftest
fixtures; do not invent new ones.

## 8. Honest state

- §1 authorship/diffstat: verified via API; the line-level byte-compare was rev 1's, **not re-run** by rev 2.
- §2 v1-only: verified from `read_header` and `KV_CACHE_VERSION 1u`; **not** measured on a real delta file.
- §4 line numbers: `grep -n` at `9cbf7e5`, in `ds4_kvstore.c`.
- §5: verified at `9cbf7e5` and `b3f5018` by reading the first lines of `tests/ds4_test.c`.
- §6: verified by quoting `fffc283:ds4_kvstore.c:889-893`.
- Not verified: whether the ported gate passes the laundering control (never executed); whether a shared
  cache dir between builds is a scenario anyone actually hits.

**Nothing here obligates the port.** Trigger remains `emilianbold` asking.
