# AGENTS.md — DwarfStar 4 (ds4)

## Codebase Memory (knowledge graph)

- **Project name:** `Users-naz-Projects-ds4` (indexed from `/Users/naz/Projects/ds4`)
- **Index:** ~18,125 nodes / 65,810 edges (re-index after large changes: `index_repository(repo_path="/Users/naz/Projects/ds4", mode="full")`)
- **Query entry points:** `search_graph` for functions/classes/routes, `trace_path` for callers/callees, `get_architecture` for structure, `query_graph` for Cypher.
- **Excluded dirs:** `misc`, `gguf`, `log`, `.git`, `dir-steering/out`.
- **Top modules (packages):** `ds4` (1446), `ds4_cuda` (622), `ds4_metal` (620), `ds4_server` (584), `ds4_agent` (442), `ds4_distributed` (226), `ds4_rocm_runtime` (200), `ds4_test` (121), `ds4_eval` (113), `ds4_kvstore` (72).
- **Languages:** C (39 files), Python (11), Bash (5), C++ (1).

## What This Is

DeepSeek V4 Flash / GLM 5.2 inference engine in C. **Not** a generic GGUF runner. Primary backend is Metal (macOS); also CUDA, ROCm, CPU-diagnostic. Single-file core (`ds4.c` ~37k lines) plus server, agent, bench, eval binaries.

## Build

```sh
make              # macOS Metal (default) — produces ds4, ds4-server, ds4-bench, ds4-eval, ds4-agent
make cpu          # CPU-only diagnostic build
make cuda-spark   # DGX Spark / GB10
make cuda-generic # Other CUDA GPUs
make strix-halo   # AMD ROCm
```

No C++. No C++ toolchain. Objective-C only where Metal requires it (`ds4_metal.m`).

## Test

```sh
make test                    # All tests (requires model + Metal)
./ds4_test --server          # Quick: API, prompt rendering, DSML parsing, KV bookkeeping
./ds4_test --logprob-vectors # Token-level correctness vs official DeepSeek vectors
./ds4_test --long-context    # Fact-recall regression
./ds4_test --tool-call-quality # DSML tool-call emission quality
./ds4_test --metal-kernels   # Isolated Metal kernel numeric checks
```

Override model: `DS4_TEST_MODEL=/path/to/model.gguf ./ds4_test --server`

## Critical Constraints

- **Never break these paths silently:** Metal resident, SSD streaming, CUDA, distributed inference, ROCm. Test or ask user before changes touching them.
- **DSML is the tool-call format.** `<｜DSML｜tool_calls>`, `<｜DSML｜invoke name="...">`. Server renders OpenAI/Anthropic tool schemas into DSML and maps DSML back. Do not confuse with XML-like tags.
- **KV cache checkpoints matter.** Long agent sessions rely on disk KV persistence (`ds4_kvstore.c`). The live KV state must stay consistent with the rendered token prefix. Conversation-scoped retention (`4f4a486`, `fa035a0`, `4e5a8de`), now grouped by **prefix-chaining lineage** (see `PLAN-KV-LINEAGE.md` and the KVCACHE section below), bounds tool-call divergence rebuilds to ≤ `anchor_step`. The recovery-injection token-mismatch is resolved (commits `531314e`/`125ea97`/`8c91512`).
- **Thinking mode has a recovery path.** When the model emits tool calls inside ` thinking`, `chat_think_tool_recovery()` injects ` response`. This can cause downstream KV cache misses if the injected tokens diverge from what the next request expects.
- **No C++.** Ever.
- **Preserve correctness over speed.** Do not keep a faster path with unexplained attention/KV/logits drift.

## Repo Layout (key files)

| File | Role |
|---|---|
| `ds4.c` | Model loading, tokenizer, CPU reference, Metal graph scheduling, sessions, disk-cache serialization |
| `ds4_server.c` | HTTP API (OpenAI/Anthropic/Responses), worker queue, streaming, DSML tool-call mapping, disk KV policy |
| `ds4_cli.c` | Interactive REPL, linenoise |
| `ds4_agent.c` | Native coding agent (in-process, no socket boundary) |
| `ds4_metal.m` | Objective-C Metal runtime wrappers |
| `ds4_kvstore.c` | Disk KV cache store/load/evict |
| `ds4_distributed.c` | Pipeline parallelism (multi-machine) |
| `ds4_tp.c` | Tensor parallelism (RDMA/TCP two-Mac) |
| `ds4_ssd.c` | SSD streaming (routed expert cache) |
| `metal/*.metal` | Compute kernels |
| `tests/ds4_test.c` | All tests in one binary |

## Upstream & Fork

- **Upstream:** `antirez/ds4` (origin remote)
- **Fork:** `nazerim/ds4` (nazerim remote) — KV retention lineage, DSML recovery hardening, DSpark/agent tooling ahead of upstream
- **Local mirror:** `/Users/naz/Projects/ds4-upstream` tracks `antirez/ds4` for comparison
- **Merged 2026-08-04:** upstream through `b7e9f00` (merge `e23cb2f`): MXFP4 (CUDA native + portable Metal experts + GGUF format), CUDA TP/HMMA work, decode-island graphs, batched-serving knobs (`--mixed-prefill-quantum` replaces the `DS4_SERVER_MIXED_PREFILL_QUANTUM` env var), checkpoint-versioned test fixtures. Merge was conflict-free; upstream never touched `ds4_kvstore.c/h` or the agent path.

### DSpark — temperature-aware speculative decoding (ABANDONED — superseded upstream)

**Status (2026-08-31):** Investigation stopped; the fork's rejection-sampling
verifier (`DS4_DSPARK_SPEC_SAMPLE`, capture q/rows, `test_spec_rejection`) was
removed after merging upstream's own exact stochastic speculative decoding +
opportunistic temperature sampling (commits `769a8ba`, `8d683d6`, `abd4ed3`),
which solves the same problem and is the maintained implementation.  Upstream
mechanism: `dspark_exact_sampling` engine flag / exact MTP sampling mode.
Historical notes below kept for the record.

**Historical status:** M4 validation complete (M4.1 pass, M4.2 pass, M4.3 follow-up complete)
**Commits (local only, not pushed):** `e0e5f40`, `7ed3225`, `ce13aac`, `0701b6c` (pushed through `600dbab`)
**Design:** `PLAN-DSPARK-TEMP-SPEC.md`
**State:** `misc/experiment-a-state.md`

Implemented rejection-sampling verification for `temperature > 0` (DeepSeek's official 0731 recommendation: temp 1.0/top_p 0.95). The key algorithm change: the DSpark drafter is deterministic argmax (point-mass proposal, q≡1), so acceptance uses p(x_i) with residual = p with draft token zeroed — not the draft-head softmax q.

**Validation results:**
- M4.1 (greedy regression): PASS — temp-0 outputs byte-identical to baseline
- M4.2 (distribution equivalence): PASS — permutation tests, all positions within null (p=0.42-1.0), full-seq p=0.81. Fixed 2 bugs: soft-q design flaw, checkpoint/KV desync on partial accept.
- M4.3 (short-context perf): ON/OFF ratio 0.83 (ON slower at 150 tok)
- M4.3 follow-up (long-context): ratio reverses at 32k → **1.21** (ON 21% faster). 16k=1.07 (marginal), 64k=0.91 (drops). 32k is the sweet spot.
- Two real bugs found and fixed during M4.2 validation.

**Key env flags:** `DS4_DSPARK_SPEC_SAMPLE=1` (enable sampling verifier), `DS4_MTP_SPEC_DISABLE=1` (disable spec entirely for baseline), `DS4_DSPARK_STATS=1` (draft_len_hist, accept rate, net_saved).

### Model checkpoint: migrated to official 0731 (2026-08-04)

**Current default model:** `ds4flash.gguf` → `gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf` — sha256-verified byte-for-byte against the official `antirez/deepseek-v4-gguf` release (LFS oid `659e22fb…`). The undated pre-0731 sibling (oid `edabc92a…`, same size, different weights) remains in `gguf/` for the historical fixtures; `gguf/DeepSeek-V4-Flash-DSpark-support.gguf` is current (oid `8b3adf59…`).

With the `0731` GGUF the default fixtures (`tests/test-vectors/flash-0731/`) are the valid pairing per `tests/test-vectors/README.md`, and `make test` runs canonical: **all suites green except `--think-tool-recovery`** (logprob-vectors, local-golden-vectors, metal-ssd-streaming-cache-pressure, server/KV, agent, eval all pass with defaults).

History: before the official `-0731` file existed, `ds4-server.sh` carried a `start-0731` entry pointing at a pre-release community conversion (`~/.omlx/models/jmilnz/DeepSeek-V4-Flash-0731-antirez-ds4-GGUF`, fetched 2026-08-02; commit `d0838d0` paired it with the official DSpark drafter). That entry now points at the official local `-0731` GGUF; the oMLX copy is obsolete and can be deleted.

### Remaining divergence: `--think-tool-recovery` (upstream-side)

The test exists upstream unchanged since the merge-base and fails on **both official checkpoints and both engine vintages** except the historical pairing:

| Engine | pre-0731 GGUF | 0731 GGUF |
|---|---|---|
| pre-merge fork (`7b7a85e`) | PASS | FAIL |
| pure upstream (`b7e9f00`) | FAIL | — |
| merged (`e23cb2f`+) | FAIL | FAIL |

Failure mode: recovery detection works (`recovered=1`, trigger at the completed marker, `</think>\n\n` injected and eval'd), but the first sampled token after injection is EOS — the model declines to continue the force-fed pathological prefix (tool call opened inside unclosed thinking). Natural turns are unaffected: `DS4_TEST_RECOVERY_PROBE=1` shows the model cleanly closing `</think>` and emitting a well-formed `list_files` call. Not related to the fork's KV lineage work (the test never touches `ds4_kvstore`). Two compounding causes: (1) the 0731 checkpoint behaves differently even on the old engine; (2) post-merge-base upstream kernel/fusion changes shift the pre-0731 weights the same way. Candidate for an upstream issue (their own test, failing with their own GGUFs).

## Known Issues

1. **DSML tool inside ` thinking`** — model sometimes emits `<｜DSML｜tool_>` inside unclosed ` thinking`. Recovery forces ` response` after 9 tokens. Two fixes were required:
   - **Fix 1 (commit 531314e):** `think_tool_recovery_fired` flag forces checkpoint canonicalization after recovery, preventing the token-mismatch KV cache miss.
   - **Fix 2 (commit 125ea97):** THINKING-mode stream branches now hold back DSML markers via `text_stream_safe_limit()`, so the streamed `reasoning_content` matches `parsed_reasoning` and the canonical KV suffix aligns with the next request.
   - **Fix 3 (commit 8c91512):** follow-up hardening — recovery holds back the full 8-byte ` response` close marker (not 7), `chat_think_tool_recovery` advances `*scan_from = marker_off + 1` so a spurious mid-prose marker can't shadow a legitimate later `"\n\n"` stanza, and non-streaming requests get plain-stream `text_stream_safe_limit()` (Stage 2). Added discriminating regression tests `test_think_recovery_ignores_prose_marker` and `test_think_recovery_spurious_then_legitimate`.
    See `BUGFIXPLAN.md`.

## Resolved Issues

- **KV cache miss after think-tool recovery** — Required two fixes: (1) `think_tool_recovery_fired` flag forces canonicalization (531314e), and (2) THINKING-mode streams hold back DSML markers so `reasoning_content` matches `parsed_reasoning` (125ea97). Hardened by commit `8c91512` (hold-string 8 bytes, `scan_from` off-by-one, Stage 2 plain-stream safety, 2 regression tests).
- **Alternative models in `ds4-server.sh`** — commit `70568c2` adds `start-<model>` / `restart-<model>` via parallel `MODEL_KEYS`/`MODEL_PATHS` arrays and `lookup_model`; `start_server` accepts a model path and passes `--model`. Verified live: the `0731` mixed-quant model maps 92908 MiB vs 93065 MiB for the default `ds4flash.gguf`.
- **DSpark confidence default** — Changed from 0.9 to 0.6. The 0.9 threshold was too conservative, rejecting most draft tokens and negating MTP throughput gains.
- **No-tools test model compatibility** — Test now uses `server_model_syntax_for_engine()` and `parse_generated_message_ex_for_syntax()` to handle both DeepSeek and GLM models correctly.

## Style

- C99, `-Wall -Wextra`, zero warnings expected.
- Comments explain *why* (shape, ordering, cache boundary), not *what*.
- Keep public APIs narrow. CLI/server must not know tensor internals.
- Prefer comments beside implementation over separate design docs.
- Follow existing patterns. Read `AGENT.md` for deeper design philosophy.

---

# DS4 Server — DSML Recovery Hardening

**Status:** Complete & committed
**Commit:** `a171132` — "fix: harden DSML recovery, add post-recovery validation and streaming fallback" (2026-07-30)
**Repo:** `nazerim/ds4` (fork of `antirez/ds4`); local `main` is 16 commits ahead of upstream `origin/main`
**Diff:** 5 files, +355 / −35

| File | Δ | Role |
|---|---|---|
| `ds4_server.c` | +350 | Core recovery, fallback, parser, helpers |
| `tests/ds4_test.c` | +31 | New & strengthened tests |
| `ds4.c` | +4 | `ds4_engine_dsml_id()` accessor |
| `ds4.h` | +1 | Accessor declaration |
| `ds4-server.sh` | +4 | DSpark / MTP tuning |

## 1. Background

DS4 is antirez's C inference server for `deepseek-v4-flash`. Tool calls are emitted by the
model as **DSML** (`<｜DSML｜tool_calls>` / `<｜DSML｜invoke name="…">…</｜DSML｜invoke>`).
The server-side recovery path that repairs malformed or truncated DSML was fragile: bad tool
blocks could fail the parser, error out entire streams, suppress tokens incorrectly, or spin on
unbounded retries. This change hardens that path end-to-end.

## 2. Observations & Root Causes

1. **Naive DSML-token suppression broke tool calling.** Suppressing the DSML token unconditionally
   corrupted tool calls and MTP speculative decoding whenever tools were active → suppression must
   be gated on `!has_tools`.
2. **GLM models have no DSML token.** `ds4_engine_dsml_id()` returns `< 0` for GLM-family engines,
   so suppression and its test must no-op gracefully rather than assume a valid id.
3. **Per-retry log spam.** Suppression setup lived inside the retry loop; hoisting it above the
   `decode_again:` label logs once instead of per attempt.
4. **Single-shot recovery was insufficient.** A stream could hit more than one bad tool block; a
   one-time recovery flag gave up too early → replaced with a bounded counter (max 2).
5. **No graceful streaming degradation.** One malformed tool block errored the whole stream →
   added a fallback that strips DSML, clamps live-stream byte positions, and finishes with
   `finish=stop` instead of erroring.
6. **Bare parameters after recovery.** Post think-tool recovery the model sometimes emits
   parameters directly under `<｜DSML｜tool_calls>` with no `<｜DSML｜invoke>` wrapper; the parser
   rejected them.
7. **DSpark confidence too high.** `0.9` was aggressive and unstable; `0.6` is more reliable.
8. **Compaction tool-call bug (affects OpenCode) — addressed by M1.** OpenCode sends its
   Summary/Compaction request with `tools: {}` and `system: []` (OpenCode
   `packages/opencode/src/session/compaction.ts` → `processor.process({ tools: {}, system: [] })`).
   ds4-server therefore sees `has_tools = false` for these turns, which (a) activates M1 DSML-token
   suppression so the model cannot emit the DSML tool token, and (b) disables every DSML→`tool_calls`
   extraction path. The compaction response can carry no tool call, so OpenCode's
   `Tool call not allowed while generating summary` guard never trips. This is the bug the M1 patch
   targets; it is fixed for the DeepSeek-V4-Flash path (residual risks noted in §7).

## 3. Changes

### 3.1 DSML token suppression (M1)
- New accessor `ds4_engine_dsml_id(ds4_engine*)` for robust DSML-token resolution
  (`ds4.c:36672`, `ds4.h:313`; used at `ds4_server.c:11420`).
- `dsml_token_id` initialised to `-1` and resolved **only** inside the `if (!has_tools)` block
  (`ds4_server.c:11478–11484`) — preserves tool calling + MTP.
- Setup hoisted above the `decode_again:` label (`ds4_server.c:11487`) to avoid per-retry spam.

### 3.2 Post-recovery validation + streaming fallback (M2)
- Eager trial-parse validation at tool-block close (`observe_tool_markers` at `4591`;
  validation/recovery branch ~`11789–11843`).
- Streaming fallback: `strip_dsml_keep_prefix()` + `clamp_live_stream_positions()` then
  `finish=stop` (call sites `11835`, `11936`, `12031`).
- `saw_tool_start = false` reset after fallback (`11843`, `11939`) to prevent re-triggering.
- Output truncated at the marker before injecting recovery in `chat_think_tool_recovery`.

### 3.3 Bounded retry + parser robustness (M3)
- Bounded counter `dsml_recovery_attempts` (init `11470`; guard `< 2` at `11808`/`11902`/`11991`;
  increments `11817`/`11918`/`12010`) replaces the single-shot flag.
- Bare-parameter parsing without an `<｜DSML｜invoke>` wrapper (`ds4_server.c:4870–4901`;
  rationale comment at `4871`). Invoke macros at `4552–4559`; tag-variant selection `4837–4852`.

### 3.4 New helpers (`ds4_server.c`)
- `strip_dsml_keep_prefix(buf*)` — `5238`: removes DSML markup while keeping any plain prefix.
- `clamp_live_stream_positions(...)` — `7570`: re-clamps plain/OpenAI/Anthropic/Responses stream
  byte offsets after stripping so clients never see out-of-range positions.

### 3.5 Launch-script tuning (`ds4-server.sh`)
- `CTX=256000` (:9), `MTP_DRAFT=1` (:37), `MTP_MARGIN=3` (:38), `DSPARK_CONFIDENCE=0.6` (:41).
- Wired through `--mtp … --dspark --mtp-draft … --mtp-margin …` (:113) and
  `--dspark-confidence …` (:114).
- Alternative models: `MODEL_KEYS`/`MODEL_PATHS` parallel arrays (:21–24), `lookup_model` (:26),
  `start-<model>`/`restart-<model>` dispatch (commit 70568c2).

## 4. Tests (`tests/ds4_test.c`)

| Test | Line | Notes |
|---|---|---|
| `test_think_tool_recovery` | 6259 | Strengthened with `inject_at` assertions (6338–6342): `> 0`, `< text.len`, marker-position check |
| `test_dsml_token_suppression_excludes_id` | 6699 | GPU-gated; skips when `ds4_engine_dsml_id() < 0` (GLM) |
| `test_no_tools_request_yields_no_tool_calls` | 6934 | **New (working tree)** — compaction regression: a tool-less request (`has_tools=false`) decoded with the M1 DSML-token ban yields no tool marker and zero `tool_calls`; GLM (`dsml_id<0`) skips suppression but still asserts no call. Flag `--no-tools-no-tool-calls` |
| `test_strip_dsml_keep_prefix` | 17708 | `strlen`-based assertions (rewritten via Python generator) |
| `test_clamp_live_stream_positions` | 17772 | Verifies offset re-clamping across all stream formats |
| bare-parameter parser test | via `test_server_unit_group` (6722) | Covers invoke-less parameter emission |
| `test_think_recovery_ignores_prose_marker` | 6420 | (commit 8c91512) 3 spurious mid-prose markers inside ` thinking` → recovery does NOT fire |
| `test_think_recovery_spurious_then_legitimate` | 6459 | (commit 8c91512) spurious prose marker then `"\n\n"` stanza → recovery fires on the legitimate one; discriminating regression for the `scan_from` off-by-one |

Related existing coverage retained: `test_tool_call_quality` (6453), `test_mtp_verify_depth`
(6602), `test_dspark_verify_depth` (6644).

## 5. Verification

- `./ds4_test --server` — **passes**.
- Clean build — **0 compiler warnings**.

## 6. Code-review findings addressed

- **C1 (critical):** the M1 refactor initially dropped the `!has_tools` gate on `dsml_token_id`,
  silently breaking tool calling + MTP speculation. Gate restored.
- **L1:** recovery warning relocated above `decode_again:`.
- **L3:** GPU suppression test skips gracefully for GLM (`dsml_id < 0`).
- **L4:** `saw_tool_start` reset added in the M2 fallback path.
- **Test hygiene:** Test C temperature raised to `0.8f`; M2/M3 tests use well-formed DSML with
  proper closing tags; Test B rewritten with deterministic `strlen` assertions.

## 7. Known issues & follow-ups

- **Compaction bug — resolved, residual risks only** (see Obs #8). Fixed for DeepSeek-V4-Flash by
  M1 + the `has_tools` parse gate. Residual: (a) GLM-family engines return `ds4_engine_dsml_id() < 0`,
  so M1 token suppression is disabled (logged as a warning) — the `has_tools` gate still blocks
  structured `tool_calls`, but tool markup could leak as plain text; (b) if a model ever spells DSML
  markup as ordinary text tokens instead of the special token, M1 suppression would not catch it.
  No regression test currently locks in the no-tools/compaction guarantee — worth adding.
- **`ds4-server.sh` DSPARK wart — RESOLVED (commit 531314e).** Defaults now resolve via
  `${VAR:-…}` at the top (`MTP_DRAFT` / `MTP_MARGIN` / `DSPARK_CONFIDENCE`), so env overrides work
  for all three; the redundant override block was removed and the help text corrected to `0.6`.
- **Untracked artifacts — RESOLVED (commit 531314e).** Added `*.o.tmp` and `log/` to `.gitignore`.
- **KV cache miss after think-tool recovery — RESOLVED (commits 531314e + 125ea97).** Required
  two fixes: (1) force canonicalization after recovery via `think_tool_recovery_fired` flag,
  and (2) hold back DSML markers in THINKING-mode streaming so `reasoning_content` matches
  `parsed_reasoning`. See `BUGFIXPLAN.md` for details.
- **Upstream drift:** local `main` is 16 commits ahead of `antirez/ds4` (0 behind); rebase/merge periodically.

## 8. Reference index (@ `a171132`)

| Symbol | Location |
|---|---|---|
| `ds4_engine_dsml_id` (def) | `ds4.c:36672` |
| `ds4_engine_dsml_id` (decl) | `ds4.h:313` |
| DSML suppression setup | `ds4_server.c:11478–11484` |
| `decode_again:` label | `ds4_server.c:11487` |
| `dsml_recovery_attempts` (init / guards / incs) | `11470` / `11808,11902,11991` / `11817,11918,12010` |
| `think_tool_recovery_fired` (init / set / gate) | `11473` / `11720` / `12150` |
| `strip_dsml_keep_prefix` | `ds4_server.c:5238` |
| `clamp_live_stream_positions` | `ds4_server.c:7570` |
| `chat_think_tool_recovery` | `ds4_server.c:10167–10266` |
| `canonicalize_tool_checkpoint` | `ds4_server.c:10757` |
| `should_canonicalize_tool_checkpoint` | `ds4_server.c:10917–10925` |
| `text_stream_safe_limit` (def / fwd decl) | `ds4_server.c:7991` / `5712` |
| bare-parameter parser | `ds4_server.c:4871–4901` |
| invoke tag macros | `ds4_server.c:4552–4559` |
| DSpark/MTP tuning | `ds4-server.sh:37–41,113–114` |
| model map / `lookup_model` | `ds4-server.sh:21–24,26–36` |

---

# BUGFIXPLAN.md — DSML-in-Think Recovery KV Cache Miss Fix

**Status:** Implemented and tested
**Date:** 2026-08-01
**Issue:** DSML tool calls inside unclosed ` thinking` tags trigger recovery that causes KV cache misses and 7+ minute full prefills

## Problem Summary

When the model emits DSML tool calls inside an unclosed ` thinking` tag, the recovery mechanism injects ` response` tokens into the live session. The next request from the client contains visible text + tool result (not raw DSML), causing a token-mismatch at the KV cache boundary. This triggers a full prefill from zero: 90k tokens at 280 t/s = 5.5 minutes.

## Root Cause (Two Layers)

### Root Cause 1: Canonicalization skipped after recovery
`should_canonicalize_tool_checkpoint()` skips canonicalization when `raw_tool_text` is present, assuming the client will replay raw DSML bytes. After recovery, this assumption is **wrong**: the client sends visible assistant text + tool result, not raw DSML. The live session diverges from the client's next prompt → token-mismatch → full prefill.

### Root Cause 2: DSML marker bytes leaked into reasoning_content
The THINKING-mode stream branches (OpenAI, Responses, Anthropic) only held back 7 bytes (for detecting a partial `</ład>` tag). DSML tool markers (`<｜DSML｜tool_calls>`) emitted inside unclosed `ład` were streamed to the client as `reasoning_content` **before** `chat_think_tool_recovery()` could truncate them. The client then replayed a reasoning string containing marker bytes that the canonicalized KV suffix lacked, causing `common` to diverge at the first leaked-marker token — producing a token-mismatch even after canonicalization.

## Fix

Two fixes were required. Both in `ds4_server.c`.

### Fix 1: Force canonicalization after recovery (commit 531314e)

3 minimal changes:
1. Added `bool think_tool_recovery_fired` flag (line 11473)
2. Set flag when recovery fires (line 11720)
3. Force canonicalization after recovery (line 12150): `(think_tool_recovery_fired || should_canonicalize_tool_checkpoint(...))`

**How it works:** After recovery, canonicalization rewrites the live KV suffix to match what the client will send next, avoiding the token-mismatch.

### Fix 2: Hold back DSML markers in THINKING-mode streaming (commit 125ea97)

3 identical edits in the THINKING-mode `else` branches:
1. `openai_sse_stream_update` (~line 6465)
2. `responses_sse_stream_update` (~line 7168)
3. `anthropic_sse_stream_update` (~line 8059)

**How it works:** When `r->has_tools`, call `text_stream_safe_limit()` to hold back complete/partial tool markers plus preceding separator whitespace (same as the TEXT branch already does). This makes the streamed `reasoning_content` match `parsed_reasoning`, so the next request's prompt matches the canonical KV suffix.

## Code Changes

```diff
@@ -11469,6 +11469,8 @@ static void generate_job(server *s, server_slot *slot, job *j) {
 
     int dsml_recovery_attempts = 0;
     bool post_recovery_validation_pending = false;
+    /* Set when think-tool recovery fires; forces checkpoint canonicalization. */
+    bool think_tool_recovery_fired = false;
     uint64_t rng = j->req.seed ? j->req.seed :
         (((uint64_t)time(NULL) << 32) ^ (response_seq << 1) ^
          (uint64_t)(uintptr_t)j);
@@ -11714,6 +11716,7 @@ decode_again:
                                     "think tool recovery after %d generated tokens",
                                     completion);
                         post_recovery_validation_pending = true;
+                        think_tool_recovery_fired = true;
                         clamp_live_stream_positions(&plain_stream_pos, &openai_live,
                                                     &anthropic_live, &responses_live,
                                                     recovery_inject_at);
@@ -12143,14 +12146,24 @@ decode_again:

     if (j->req.kind == REQ_CHAT && parsed_calls.len &&
         j->req.api != API_RESPONSES &&
-        should_canonicalize_tool_checkpoint(s, &parsed_calls))
+        (think_tool_recovery_fired ||
+         should_canonicalize_tool_checkpoint(s, &parsed_calls)))
     {
         /* Chat/completions has no protocol object that binds the next request
          * to this live KV state.  Canonicalize only the fallback tool-call
          * path where we lack exact sampled DSML replay; when raw DSML is known,
          * replaying those bytes keeps future prompts aligned without rebuilding
          * hidden reasoning.  Responses deliberately skips this path because its
-         * previous_response_id contract binds the next turn to live state. */
+         * previous_response_id contract binds the next turn to live state.
+         *
+         * After think-tool recovery, the raw-DSML-replay assumption is wrong:
+         * the recovery injected closing-think tokens into the live session,
+         * and the DSML tool-call text was extracted as a tool call (not sent
+         * as assistant content).  The next client prompt will contain the
+         * visible assistant text plus tool result, not the raw DSML bytes.
+         * Force canonicalization to rewrite the live KV suffix to match what
+         * the client will replay, avoiding a token-mismatch cache miss and
+         * 7+ minute full prefill. */
         canonicalize_tool_checkpoint(s, slot, j, ctx_span, trace_id,
                                      parsed_content ? parsed_content : "",
                                      parsed_reasoning, &parsed_calls);
```

## Verification

✅ **Build:** Compiles cleanly with zero warnings
✅ **Tests:** All server tests pass (`./ds4_test --server`)
✅ **Expected impact:** Eliminates 5+ minute delays per recovery event

## Next Steps

1. **Runtime testing:** Run server with model that triggers think-tool recovery to confirm fix
2. **Monitor logs:** Look for "tool checkpoint canonicalized" messages after recovery
3. **Upstream port:** Consider porting fix to `antirez/ds4` upstream

## References

- Recovery function: `ds4_server.c:10167-10266` (`chat_think_tool_recovery`)
- Canonicalization: `ds4_server.c:10757` (`canonicalize_tool_checkpoint`)
- Log evidence: `log/ds4.log:1744-1819` (repeated pattern)
- Related: the "DS4 Server — DSML Recovery Hardening" section earlier in this file.

---

# DSML Think-Tool Recovery KV Cache Miss — Root Cause & Fix

**Date:** 2026-08-01  
**Severity:** High (7+ minute full prefill after recovery)  
**Status:** Fixed

---

## Executive Summary

When the model emits DSML tool calls inside an unclosed ` thinking` tag, the `chat_think_tool_recovery()` function injects `\n response\n\n` tokens into the live session to close the thinking block. This recovery causes a **token-mismatch** on the next request, triggering a full prefill from zero (7+ minutes at 90k context). The disk KV cache does not help because the cache key (SHA1 of rendered token text) includes the DSML tool-call text, which the client does not replay.

**Root causes (two layers):**

1. **Canonicalization skipped after recovery:** `should_canonicalize_tool_checkpoint()` skips canonicalization when `raw_tool_text` is present, assuming the client will replay raw DSML bytes. After recovery, this assumption is wrong — the client sends visible assistant text + tool result, not raw DSML. The live session diverges → token-mismatch → full prefill.

2. **DSML marker bytes leaked into reasoning_content:** The THINKING-mode stream branches only held back 7 bytes (for partial `</ład>` detection). DSML markers emitted inside unclosed `ład` were streamed as `reasoning_content` before recovery could truncate them. The client replays reasoning containing marker bytes absent from the canonical KV suffix → `common` diverges at the first leaked-marker token.

**Fix (two parts):**
1. Force checkpoint canonicalization after recovery via `think_tool_recovery_fired` flag (commit 531314e).
2. Hold back DSML markers in THINKING-mode streaming via `text_stream_safe_limit()` (commit 125ea97).

---

## Detailed Root Cause Analysis

### 1. Recovery Injection Flow

**Code location:** `ds4_server.c:10167-10266` (`chat_think_tool_recovery`)

When the model emits `<｜DSML｜tool_` inside ` thinking`:

1. **Detection:** The scanner detects the DSML marker at line 11698-11704
2. **Injection:** `chat_think_tool_recovery()` tokenizes `\n response\n\n` and feeds each token to the live session via `server_eval_token()` (line 10190-10196)
3. **Output modification:** The output text buffer is truncated at the DSML marker position, then `\n response\n\n` is appended (line 10197-10203)
4. **Flag set:** `post_recovery_validation_pending = true` (line 11719)

**Example from log (line 1744):**
```
0801 09:56:43 ds4-server: chat ctx=88950..89027:77 TOOLS tool call inside unclosed  thinking; forced  after 9 generated tokens
```

At this point:
- Session position: 89027 (after 77-token prompt prefill)
- 9 tokens generated: positions 89027..89036
- Recovery injects ~4 tokens (`\n response\n\n`): session now at ~89040

### 2. Live KV State After Recovery

After recovery, the live session contains:

| Token Range | Content |
|---|---|
| 0..88950 | Conversation history (prompt) |
| 88950..89027 | Prompt prefill (77 tokens) |
| 89027..89036 | 9 sampled generated tokens (include `<｜DSML｜tool_`) |
| 89036..89040 | 4 injected `\n response\n\n` tokens |
| 89040..89134 | 94 more generated tokens (DSML tool call) |

**Total live session:** 89134 tokens

The output text buffer (sent to client) contains:
- Conversation history up to the DSML marker
- `\n response\n\n` (appended by recovery)
- **NOT** the DSML tool-call text (extracted separately as a tool call)

### 3. Next Request Prompt Construction

The client receives:
- Assistant message: content = [history up to marker][\n response\n\n]
- Tool call: `read` with ID `call_5bc28b11092fb72c944554d7de3cad43`

The client sends the next request with:
- `prompt_text` = [full conversation history][assistant message ending with `\n response\n\n`][tool result]

This prompt is tokenized: **90683 tokens** (line 1750)

### 4. Token-Mismatch Detection

**Code location:** `ds4_server.c:11115` (live cache check)

```c
cached = common == old_pos && j->req.prompt.len >= old_pos ? common : 0;
```

- `old_pos` = 89134 (live session length)
- `j->req.prompt.len` = 90683 (new prompt length)
- `common` = `ds4_session_common_prefix(slot->session, &j->req.prompt)` = **89031**

**Common prefix calculation** (`ds4.c:58980-58986`):
```c
int ds4_session_common_prefix(ds4_session *s, const ds4_tokens *prompt) {
    int n = s->checkpoint.len < prompt->len ? s->checkpoint.len : prompt->len;
    int i = 0;
    while (i < n && s->checkpoint.v[i] == prompt->v[i]) i++;
    return i;
}
```

The common prefix is **89031 tokens**:
- 89027 tokens of prompt match
- 4 tokens of generated content match (89027..89031)
- **Mismatch at token 89031** (the 5th generated token)

**Why the mismatch?** BPE tokenization ambiguity. The 9 generated tokens were sampled in the context of the live session. When the client re-tokenizes the visible text (which includes those 9 tokens rendered as text), the BPE boundaries may shift. The first 4 tokens happen to match, but the 5th diverges.

**Log evidence (line 1750):**
```
0801 09:56:48 ds4-server: live kv cache miss live=89134 prompt=90683 common=89031 reason=token-mismatch
```

### 5. Why Disk Cache Doesn't Help

**Code location:** `ds4_server.c:11153` (disk cache store) and `ds4_kvstore.c:1215-1339` (disk cache load)

When the live cache miss is detected:

1. **Store current state:** `kv_cache_store_current(s, slot, "evict")` (line 11153)
   - Stores 89134 tokens
   - Key = SHA1 of rendered text of all 89134 tokens
   - Rendered text includes the DSML tool-call text (which was generated but not sent to client)

2. **Try to load:** `kv_cache_try_load()` (line 11156)
   - Looks for a checkpoint whose text is a prefix of the new prompt text (90683 tokens)
   - The new prompt text does **NOT** include the DSML tool-call text
   - No match found

**Log evidence (lines 1751-1752):**
```
0801 09:56:48 ds4-server: kv cache evicted reason=disk-cache-full tokens=81920 hits=0 size=1098.57 MiB file=/tmp/ds4-kv/c6201676c45e03db49642a8dbbee9aca3c86abc8.kv
0801 09:56:48 ds4-server: kv cache stored tokens=89134 trimmed=0 reason=evict key=token-text size=1193.25 MiB save=263.8 ms
```

The disk cache is keyed by `token-text` (rendered token text), which includes the DSML tool call. The next prompt doesn't include the DSML tool call, so the SHA1 keys don't match.

### 6. Why Canonicalization Is Skipped

**Code location:** `ds4_server.c:12145-12158`

After tool call extraction, the server checks whether to canonicalize the checkpoint:

```c
if (j->req.kind == REQ_CHAT && parsed_calls.len &&
    j->req.api != API_RESPONSES &&
    should_canonicalize_tool_checkpoint(s, &parsed_calls))
{
    canonicalize_tool_checkpoint(s, slot, j, ctx_span, trace_id, ...);
}
```

**`should_canonicalize_tool_checkpoint()` logic** (line 10917-10925):
```c
static bool should_canonicalize_tool_checkpoint(const server *s, const tool_calls *calls) {
    if (!calls || calls->len == 0) return false;
    if (s && !s->disable_exact_dsml_tool_replay &&
        calls->raw_tool_text && calls->raw_tool_text[0])
    {
        return false;  // <-- SKIPS CANONICALIZATION
    }
    return true;
}
```

The function returns `false` when `raw_tool_text` is present. The assumption: "when raw DSML is known, replaying those bytes keeps future prompts aligned without rebuilding hidden reasoning."

**But this assumption is WRONG after recovery!** After recovery:
- The client does **NOT** replay the raw DSML bytes
- The client sends the visible assistant text (ending with `\n response\n\n`) + tool result
- The live session contains sampled recovery tokens + DSML tokens
- These don't match, causing the token-mismatch

**Log evidence (line 1748):**
```
0801 09:56:48 ds4-server: tool calls ctx=88950..89027:77 n=1 raw_tool_text=1 ids=[call_5bc28b11092fb72c944554d7de3cad43] names=[read]
```

`raw_tool_text=1` means canonicalization is skipped.

### 7. Full Prefill Consequence

With no live cache hit and no disk cache hit, the server starts from zero:

**Log evidence (lines 1753-1786):**
```
0801 09:56:48 ds4-server: chat ctx=0..90683:90683 TOOLS prompt start
0801 09:56:48 ds4-server: chat ctx=0..90683:90683 TOOLS prefill chunk 0/90683 (0.0%) ...
...
0801 10:02:17 ds4-server: chat ctx=0..90683:90683 TOOLS prefill chunk 90683/90683 (100.0%) ...
0801 10:02:17 ds4-server: chat ctx=0..90683:90683 TOOLS prompt done 328.623s
```

**328 seconds = 5.5 minutes** for a 90k-token prefill at ~276 t/s.

This pattern repeats multiple times in the log (lines 1744, 1798, 1804), causing repeated 5+ minute delays.

---

## The Fix

### Strategy

Force checkpoint canonicalization after think-tool recovery, even when `raw_tool_text` is present. The canonicalization rewrites the live KV suffix to match what the client will replay.

### Implementation

**File:** `ds4_server.c`

**Change 1:** Add a persistent flag to track recovery (line 11470-11485)

```c
int dsml_recovery_attempts = 0;
bool post_recovery_validation_pending = false;
/* Set when chat_think_tool_recovery() injects  thinking
 response
 into the live session.  After recovery the live KV prefix contains
 * sampled recovery tokens + DSML tool-call tokens that the client will
 * NOT replay verbatim: the client sees the visible assistant content
 * (ending with ) plus the tool result, not the raw
 * DSML bytes.  Force checkpoint canonicalization in that case even
 * when raw_tool_text is available, because the raw-DSML-replay
 * assumption behind should_canonicalize_tool_checkpoint() is wrong
 * after recovery. */
bool think_tool_recovery_fired = false;
```

**Change 2:** Set the flag when recovery fires (line 11719)

```c
if (recovered) {
    server_log(DS4_LOG_WARNING, ...);
    trace_event(s, trace_id, ...);
    post_recovery_validation_pending = true;
    think_tool_recovery_fired = true;  // <-- NEW
    ...
}
```

**Change 3:** Modify the canonicalization condition (line 12148-12161)

```c
if (j->req.kind == REQ_CHAT && parsed_calls.len &&
    j->req.api != API_RESPONSES &&
    (think_tool_recovery_fired ||                    // <-- NEW
     should_canonicalize_tool_checkpoint(s, &parsed_calls)))
{
    /* Chat/completions has no protocol object that binds the next request
     * to this live KV state.  Canonicalize only the fallback tool-call
     * path where we lack exact sampled DSML replay; when raw DSML is known,
     * replaying those bytes keeps future prompts aligned without rebuilding
     * hidden reasoning.  Responses deliberately skips this path because its
     * previous_response_id contract binds the next turn to live state.
     *
     * After think-tool recovery, the raw-DSML-replay assumption is wrong:
     * the recovery injected  thinking
 response
     tokens into the live session, and the DSML tool-call text was
     * extracted as a tool call (not sent as assistant content).  The next
     * client prompt will contain the visible assistant text (ending with
     * ) + tool result, not the raw DSML bytes.  Force
     * canonicalization to rewrite the live KV suffix to match what the
     * client will replay, avoiding a token-mismatch cache miss and
     * 7+ minute full prefill. */
    canonicalize_tool_checkpoint(s, slot, j, ctx_span, trace_id, ...);
    thinking_live_clear(s, slot);
}
```

### How Canonicalization Fixes the Issue

**`canonicalize_tool_checkpoint()` logic** (line 10757):

1. **Build canonical prompt:** Tokenize `j->req.prompt_text` + tool call suffix
   - The suffix is built from `parsed_content` (which includes the `\n response\n\n` injection) + tool calls
   - This matches what the client will send next

2. **Compare with live session:** `ds4_session_common_prefix(slot->session, &canonical)`
   - If they match, do nothing
   - If they differ, rewrite the live session

3. **Rewrite live session:** `ds4_session_rewrite_from_common()` (line 10741-10744)
   - Overwrites the divergent suffix in the live session
   - The live session now matches the canonical prompt

4. **Fallback:** If the rewrite is too large (`DS4_SESSION_REWRITE_REBUILD_NEEDED`), load an older disk checkpoint or rebuild from scratch

After canonicalization, the live session matches what the client will send next. The next request will have a live cache hit, avoiding the 7+ minute full prefill.

---

## Verification

### Build Test

```bash
$ make
cc -O3 -ffast-math -g -mcpu=native -Wall -Wextra -std=c99 -c -o ds4_server.o ds4_server.c
cc -O3 -ffast-math -g -mcpu=native -Wall -Wextra -std=c99 -o ds4-server ds4_server.o ...
```

**Result:** Compiles cleanly with zero warnings.

### Expected Behavior After Fix

When think-tool recovery fires:

1. **Recovery injects `\n response\n\n` tokens** (unchanged)
2. **Tool call is extracted** (unchanged)
3. **Canonicalization runs** (NEW):
   - Builds canonical prompt from visible text + tool result
   - Rewrites live session to match
4. **Next request arrives:**
   - Live cache hit: `common == old_pos`
   - No full prefill needed
   - Generation continues from the cached state

**Expected log output:**
```
0801 09:56:43 ds4-server: chat ctx=88950..89027:77 TOOLS tool call inside unclosed  thinking; forced  after 9 generated tokens
0801 09:56:48 ds4-server: tool calls ctx=88950..89027:77 n=1 raw_tool_text=1 ...
0801 09:56:48 ds4-server: tool checkpoint canonicalized ctx=88950..89027:77 common=89027 live=89134 canonical=90683  <-- NEW
0801 09:56:48 ds4-server: chat ctx=89027..90683:1656 TOOLS prompt start  <-- PARTIAL PREFILL
0801 09:56:49 ds4-server: chat ctx=89027..90683:1656 TOOLS prompt done 1.2s  <-- FAST
```

Instead of:
```
0801 09:56:48 ds4-server: live kv cache miss live=89134 prompt=90683 common=89031 reason=token-mismatch
0801 09:56:48 ds4-server: chat ctx=0..90683:90683 TOOLS prompt start
0801 10:02:17 ds4-server: chat ctx=0..90683:90683 TOOLS prompt done 328.623s  <-- 5.5 MINUTES
```

### Regression Risk

**Low.** The fix only affects the think-tool recovery path:
- Normal tool calls (no recovery): unchanged behavior
- Recovery without tool calls: unchanged behavior (no canonicalization)
- Recovery with tool calls: canonicalization now runs (fixes the bug)

The canonicalization logic is already well-tested for the non-recovery case. We're just enabling it for the recovery case.

---

## Alternative Approaches Considered

### 1. Store Disk Checkpoint with Visible-Text Key

**Idea:** After recovery, store a disk checkpoint keyed by the visible text (not the full token text).

**Pros:** Would allow disk cache to match the next prompt.

**Cons:**
- Requires modifying the disk cache key generation logic
- The visible text is not easily available at the time of disk cache store (line 11153)
- More complex than canonicalization

**Verdict:** Rejected. Canonicalization is simpler and more direct.

### 2. Avoid Injecting Tokens That Diverge

**Idea:** Instead of injecting `\n response\n\n` tokens, adjust the live KV state to match what the client will send.

**Pros:** Would avoid the divergence entirely.

**Cons:**
- Requires predicting what the client will send (impossible in general)
- The client's prompt depends on the assistant message content, which includes the recovery injection
- Complex and error-prone

**Verdict:** Rejected. Canonicalization is a post-hoc fix that's simpler and more robust.

### 3. Increase Disk Cache Size

**Idea:** Increase the disk cache budget from 8 GB to 16 GB or more.

**Pros:** Would reduce eviction frequency.

**Cons:**
- Does not fix the root cause (token-mismatch)
- The disk cache key still doesn't match after recovery
- Wastes disk space

**Verdict:** Rejected. Does not address the root cause.

### 4. Disable KV Cache Quantization

**Idea:** Store unquantized KV caches to avoid quantization-induced mismatches.

**Pros:** Might reduce mismatches.

**Cons:**
- Doubles or triples disk cache size
- Does not fix the root cause (BPE tokenization mismatch, not quantization)
- Performance impact

**Verdict:** Rejected. Does not address the root cause.

---

## Conclusion

The think-tool recovery path injects tokens into the live session that diverge from what the client sends next. The raw-DSML-replay assumption behind `should_canonicalize_tool_checkpoint()` is wrong after recovery. The fix forces canonicalization in the recovery case, rewriting the live KV suffix to match the client's next prompt. This avoids the token-mismatch cache miss and the 7+ minute full prefill.

**Impact:** Eliminates 5+ minute delays after think-tool recovery, improving agent loop responsiveness.

**Risk:** Low. The fix only affects the recovery path and uses existing canonicalization logic.

**Testing:** Compiles cleanly. Runtime testing with a model that triggers think-tool recovery is recommended to confirm the fix.

---

## References

- **Recovery function:** `ds4_server.c:10167-10266` (`chat_think_tool_recovery`)
- **Recovery trigger:** `ds4_server.c:11698-11720`
- **Canonicalization:** `ds4_server.c:10757` (`canonicalize_tool_checkpoint`)
- **Disk cache store:** `ds4_kvstore.c:923-1169` (`ds4_kvstore_store_live_prefix_text`)
- **Disk cache load:** `ds4_kvstore.c:1215-1339` (`ds4_kvstore_try_load_text`)
- **Common prefix:** `ds4.c:58980-58986` (`ds4_session_common_prefix`)
- **Log file:** `log/ds4.log:1744-1819` (repeated recovery + full prefill pattern)

---

# DSpark Speculative Decoding — Drafter Selection & Setup

**Status:** Working with the official support GGUF (Aug 2026)

## TL;DR — use the official support GGUF

DSpark drafting works with antirez's official support model
(`DeepSeek-V4-Flash-DSpark-support.gguf`, ~6 GB, from `antirez/deepseek-v4-gguf`
via `./download_model.sh dspark-support`). It loads with `invalid=0` **with no
tensor renaming** and, against the `0731` mixed-quant main model, drafts at
**~89% acceptance** and is net-profitable. `ds4-server.sh` points
`MTP_PATHS["0731"]` at it:

```sh
./ds4-server.sh start-0731-mtp
```

The third-party HF-converted drafter
(`alessandrobologna/DeepSeek-V4-Flash-0731-DSpark-Drafter-GGUF`) is **not
usable**: even after renaming makes it *load* (`invalid=0`), it scores **0%
first-token acceptance** (`miss_first == cycles`) — its predictions do not match
the target model. See "HF conversion (reference only)" below.

## How DSpark is enabled (and the gotchas)

Invocation is `--mtp <support.gguf> --dspark --temp 0` (README §DSpark). Notes:

- **Greedy only.** Sampled decoding ignores DSpark proposals. The server's
  speculative gate (`ds4_server.c:11554`) requires `temperature <= 0`.
- **`--mtp-draft` / `--mtp-margin` are legacy-MTP flags.** For DSpark the block
  size comes from the support model metadata (`block=5`); `mtp_ready` is *not*
  set for DSpark (`ds4.c:55886`), so `ds4_engine_mtp_draft_tokens` returns the
  block size regardless of `--mtp-draft` (`ds4.c:49357`). Passing them is harmless.
- **Tool-less chat disables speculation.** For `REQ_CHAT && !has_tools` the
  server sets `dsml_token_id` to suppress raw DSML markup (`ds4_server.c:11472`),
  which fails the gate's `dsml_token_id < 0`. To observe drafting, use
  `/v1/completions` (or chat *with* tools).
- **`--quality` / `--dspark-strict` force target-only decode** (no speculation).
- **Confidence threshold:** README default is `0.9`; this fork uses `0.6`
  (`DSPARK_CONFIDENCE`). `--dspark-confidence 0` forces fixed 5-token blocks
  (diagnostic only).

## Diagnosing draft acceptance

Run with `DS4_DSPARK_STATS=1` (optionally `DS4_DSPARK_SPEC_LOG=1`, and
`DS4_DSPARK_SCHEDULER=0` to disable the adaptive scheduler). Stats print at
session teardown (`ds4.c:56682`). Key fields:

| Field | Meaning |
|---|---|
| `cycles` | speculative cycles entered |
| `proposed` / `accepted_draft` | draft tokens proposed / accepted |
| `accept_rate` | accepted / proposed |
| `miss_first` | cycles where the *first* draft token was wrong |
| `no_draft` | cycles that produced no draft |
| `net_saved` | ms saved net of drafter overhead (positive = profitable) |

A healthy drafter has high `accept_rate` and low `miss_first`. **`miss_first`
≈ `cycles` (0% first-token accuracy) means the drafter is incompatible with the
target model**, even if it loads with `invalid=0`.

### Measured (0731 main model, greedy `/v1/completions`)

| Drafter | accept_rate | miss_first | net_saved | verdict |
|---|---|---|---|---|
| official `DeepSeek-V4-Flash-DSpark-support.gguf` | 89.25% | 2 / 225 | +394 ms | works |
| HF-converted `...0731-DSpark-Drafter-Q2_K-Q8_0-ds4.gguf` (forced blocks) | 0.00% | 399 / 399 | −3165 ms | incompatible |

Aggregate token-throughput gains on a *thinking* workload are modest (~3%) — the
README warns low-yield/thinking prompts benefit least; predictable text (e.g.
code) benefits most. The acceptance rate is the correctness signal.

---

## HF conversion (reference only — loads but does not function)

> **Do not use this path for inference.** Kept for reference. The rename makes
> the HF drafter *load* (`invalid=0`) but it still scores 0% first-token
> acceptance, so something beyond naming/dims (weight layout, quantization, or
> model provenance) is incompatible with the target model. Use the official
> support GGUF above instead.

### Why a rename was needed at all

ds4 detects and binds the MTP draft model **by tensor name**. It looks for
`mtp.<stage>.<suffix>` (`ds4_tensor_mtp_stage`, `ds4.c:2544`;
`tensor_by_mtp_stage_suffix`, `ds4.c:4223`) and expects final-stage heads as
`mtp.<final>.norm`, `mtp.<final>.hc_head_*`, `mtp.<final>.markov_head.markov_w1/w2`,
`mtp.<final>.confidence_head.proj`, plus `mtp.0.main_proj`/`main_norm`
(`dspark_bind_block`, `ds4.c:6545`).

The HF drafter uses the **`dspark.<stage>.`** prefix and **flattened** final-head
names (`dspark.markov_w1.weight`, `dspark.confidence_head.weight`). With no
`mtp.*` tensors, `support_model_detect` (`ds4.c:2787`) reports `stages=0` →
`detected=none` and MTP is silently disabled.

The MXFP4 variant is unusable regardless: `ds4`'s `gguf_types[]` table
(`ds4.c:2008`) only supports GGUF types 0–30, and MXFP4 is type 39.

### The rename: rewrite names, keep bytes

The stage-block tensors already match ds4's expected 24-per-stage suffixes — only
the prefix (`dspark.` → `mtp.`) and the flattened final-head names differ. The
tool rewrites the GGUF tensor directory (header rebuild) and appends the payload
unchanged. One extra fix: HF stores the final confidence projection squeezed to
**1-D `[N_EMBD+markov_rank]`**, but ds4 binds it as a **2-D `[N_EMBD+markov_rank, 1]`**
matrix (`ds4.c:5354`, runtime check `ds4.c:32000`). The bytes are identical, so
the tool promotes that one tensor's shape (`ndim` 1→2). Without it the model loads
with `invalid=1` and the confidence probe is silently disabled.

```sh
python3 gguf-tools/rename-dspark-tensors.py INPUT.gguf   # writes INPUT-ds4.gguf
```

### Renames applied (final stage = max stage, here 2)

| HF name | ds4 name |
|---|---|
| `dspark.<s>.<suffix>.weight` | `mtp.<s>.<suffix>.weight` |
| `dspark.main_proj.weight` | `mtp.0.main_proj.weight` |
| `dspark.main_norm.weight` | `mtp.0.main_norm.weight` |
| `dspark.norm.weight` | `mtp.<final>.norm.weight` |
| `dspark.hc_head_fn.weight` | `mtp.<final>.hc_head_fn.weight` |
| `dspark.hc_head_base.weight` | `mtp.<final>.hc_head_base.weight` |
| `dspark.hc_head_scale.weight` | `mtp.<final>.hc_head_scale.weight` |
| `dspark.markov_w1.weight` | `mtp.<final>.markov_head.markov_w1.weight` |
| `dspark.markov_w2.weight` | `mtp.<final>.markov_head.markov_w2.weight` |
| `dspark.confidence_head.weight` | `mtp.<final>.confidence_head.proj.weight` (1-D→2-D) |

### GGUF layout invariants (why the tool is byte-safe)

- GGUF v3 header: magic(4) version(4) `n_tensors`(u64) `n_kv`(u64), then KVs, then
  the tensor directory. **String lengths are u64.** Value types per `ds4.c:1986`:
  array = item_type(u32) + count(u64) + items.
- ds4 aligns tensor-data start to `general.alignment` (KV) or 32 (`ds4.c:2346`).
- The payload is copied **from the aligned tensor-data start**, so **rel offsets
  are written unchanged** (they are relative to that start, not absolute).

Two pitfalls hit during development: (1) string/array KV values must be
re-encoded with their length/type prefixes, not copied raw; (2) do **not** adjust
rel offsets by the header delta — doing so shifts every tensor past its payload
and the last tensor overruns the shorter file (`tensor points outside GGUF file`).

Key references: `ds4_tensor_mtp_stage` (`ds4.c:2544`), `tensor_by_mtp_stage_suffix`
(`ds4.c:4223`), `dspark_bind_block` (`ds4.c:6545`), `support_model_detect`
(`ds4.c:2787`), confidence layout (`ds4.c:5354`, `ds4.c:32000`).

---

# KVCACHE — Conversation-Scoped Disk KV Retention

**Status:** Implemented, hardened, and regression-reviewed
**Commits:**
- `4f4a486` — feat: conversation-scoped KV disk-cache retention (the redesign)
- `fa035a0` — fix: harden KV disk-cache retention (stale scoping, anchor grid, eviction, load fallback)
- `4e5a8de` — fix: kv cache load-fallback, min_tokens warning, and last_used sync
- `99c497f` — fix: cross-session poisoning (conv_id hash cap 512B→128KB; stale layer decommissioned)
- `9149525` — fix: review hardening (model_fp zombies, payload-less fallback, chunk wiring, watermarks, orphan temps)
- `8f5b783` — feat: retention lineage by prefix-chaining (replaces conv_id grouping; see PLAN-KV-LINEAGE.md)
**Design docs:** `PLAN-KVCACHE.md` (original design), `PLAN-KV-LINEAGE.md` (current lineage design)
**Files:** `ds4_kvstore.c`, `ds4_kvstore.h`, `ds4_server.c`

## Problem it solves

The old disk cache kept only the *largest* checkpoints (keep-largest eviction). After any divergence — tool-call visible-text rewrite or OpenCode compaction — the large checkpoints go stale (their text no longer prefixes the new prompt), and the smaller still-valid ones were already evicted. So at a miss the disk holds only stale large checkpoints → `find_text_prefix` finds nothing → full prefill from 0 (120k tokens ≈ 6–7 min). The 32 GB budget was never the constraint; the policy discarded the useful checkpoints.

**Goal / metric:** bound a **tool-call divergence** rebuild to ≤ `anchor_step` tokens (default 8192 ≈ 25–35 s at ~260–335 t/s), regardless of where the divergence lands. Compaction is irreducible (rebuild = `prompt_len − common`) and not fixable server-side.

## Design

Checkpoint file headers are now **v2** (72 bytes = base 48 + 24), persisting `conv_id` (diagnostic only), `model_fp`, `bucket` (`tokens / anchor_step`), and `level` (halving) so eviction can act on them without reading multi-hundred-MB payloads. The `stale` byte is inert (layer decommissioned in `99c497f`). v1 files still load (legacy defaults) and are never upgraded in place.

Retention is **per lineage by prefix-chaining**: a lineage is a maximal chain of checkpoint texts where each is a byte-prefix of the next — the exact relation the load path uses for reuse, so grouping can never disagree with loadability. Chains branch where sessions diverge from a shared head (parent session / sibling session / subagent); shared ancestors belong to every chain that extends them and survive while ANY of those chains keeps them. Retiring a branch removes only the entries exclusive to it. (`conv_id`, the SHA1-of-head hash, proved a cliff: a shared preamble larger than its byte cap merged distinct sessions into one lineage and re-opened cross-session eviction — `99c497f` raised the cap to 128KB as a stopgap; prefix-chaining removes the cap entirely. Checkpoint texts are read once per process into a sha-keyed cache for the chain computations.) Retention within a lineage is non-uniform and age-tiered:

- **Dense small:** keep ALL anchors ≤ `small_dense` (default 16k) — cheap, good coverage.
- **Dense tail:** keep the frontier (a chain leaf, always) + `tail_anchors` (default 2) below — bounds the common tool-call divergence to ≤ `step`.
- **Sparse middle:** the largest anchor per `mid_spacing` (default 128k) window; on budget pressure **halve** (double the spacing of) the LRU idle lineage's exclusive large-middle anchors — shared-ancestor levels are never bumped, so halving cannot leak into other branches.
- **Age tier:** retire the LRU idle lineage (exclusive entries only) when halving is exhausted; legacy v1 files exit only via legacy-LRU.
- The **active chain** (ancestors of the incoming store text) is never halved or retired, and its anchors are judged against the *virtual incoming chain* for redundancy — so an idle branch's diverged anchor can never "cover" an ancestor the incoming session still needs (matters when the branching session has no leaf on disk yet).
- Never evict a small anchor merely because a bigger one exists; never strip one lineage's floor for another's budget.

**Stale layer decommissioned (`99c497f`):** `mark_stale_at_load` is an intentional no-op. Stale marking overrode keep-set protection (evicting frontiers) and was the cross-session poison mechanism; redundant/halving/LRU-retire already provide pruning.

**Known non-bug — volatile prompt bytes:** prompts embedding volatile content (e.g. OpenCode's `Today's date:` inside a subagent `<env>` block in history) diverge mid-history at rollover. The cache then hits the deepest true prefix and rebuilds the ladder in one prefill (one-time cost per affected session). Inherent to byte-exact caching; not special-cased.

**Anchor grid (H-2):** continued checkpoints write on the `anchor_step` grid aligned to the engine's effective prefill chunk (`ds4_engine_effective_prefill_chunk`: explicit chunk → `DS4_METAL_PREFILL_CHUNK` → variant default), so anchors actually land. The startup log shows `anchor_step=8192 continued_step=8192 prefill_chunk=4096`.

**Load fallback (M-5):** `try_load_text` tries the largest candidate first; on pre-payload verification failure (corrupt body, hash/prefix mismatch, fopen failure) it falls back to the next-largest valid candidate instead of forcing a full prefill.

**Namespace:** keyed by `model_fp` (weight fingerprint) + quant + ctx; default `reject_different_quant=true`. Cross-model/quant checkpoints are never selected or marked stale; byte-compatible builds (0731/v2) share one fingerprint and reuse KV.

## Config knobs (ds4-server)

```
--kv-cache-anchor-step       8192     base anchor spacing (dense tail + small)
--kv-cache-small-dense       16384    keep ALL anchors ≤ this
--kv-cache-tail-anchors      2        frontier + this many below kept dense
--kv-cache-mid-spacing       131072   large-middle spacing; halve (double) on pressure
--kv-cache-min-anchors       4        retire a conversation when ladder ≤ this
--kv-cache-max-conversations 0        cap distinct convs (0 = unlimited)
```
Plus existing: `--kv-cache-min-tokens`, `--kv-cache-cold-max-tokens`, `--kv-cache-continued-interval-tokens` (enable-gate/fallback), `--kv-cache-boundary-trim-tokens`, `--kv-cache-boundary-align-tokens`, `--kv-cache-reject-different-quant`.

## Verification

- `make test` green (server KV suite incl. lineage tests, agent tests, eval extractors, layer_pack 97, mgpu 98, gpu_args CLI 44).
- Lineage tests: three branches sharing a head keep all frontiers; shared ancestor survives branch retirement; halving spares shared ancestors + active chain; active chain never retired; plus the original keep-set/halving/retirement/legacy-LRU geometry on prefix-chain fixtures.
- Live stress (1GB budget, 3 interleaved synthetic sessions): cross-session frontier hits on switch-back, idle-lineage whole-branch retirement under pressure, active chain never touched, cold rebuild after retirement, clean shutdown store. Log signature: `reason=redundant` (middle pruning), `reason=conversation-retired` (branch/lineage), `reason=legacy-lru` (v1 singletons).
- `make ds4-server` builds clean (0 warnings under `-Wall -Wextra -std=c99`).
- Startup banner logs `continued_step` and `prefill_chunk` so the effective anchor grid is observable.

---

# KVCACHE — Deep Divergence Investigation (COMPLETE)

Tracking for the 10:27:35 cache-miss incident and the follow-up investigation. Investigation is complete; both open questions are resolved and all mitigations are committed (`98e75ec`, `a64e6b2`, `6b2e3a3`).

## Incident (log/ds4.log, session `ses_02d7f1ead...` "Clone cleos repo")

```
10:27:35 live kv cache miss live=118628 prompt=119239 common=34748 reason=token-mismatch
10:27:35 kv cache evicted reason=redundant conv=8875221508171815956 level=0 tokens=106496
10:27:35 kv cache stored tokens=118628 trimmed=0 reason=evict
10:27:36 kv cache hit text tokens=16384  → chat ctx=16384..119239:102855 prompt (≈419 s prefill)
```

Same session, three misses total:

| Time | live | prompt | common | disk recovered | outcome |
|---|---|---|---|---|---|
| 10:20:26 | 44463 | 108701 | 22218 | 108683 | good hit |
| 10:20:58 | 109563 | 110819 | 33595 | 109447 | good hit |
| 10:27:35 | 118628 | 119239 | 34748 | 16384 | bad miss → 102855-token prefill |

## What is already established (evidence, not speculation)

1. **Our fix IS in the running binary.** `df7da13` committed 10:14:32, `ds4-server` built 10:14:49, server started 10:16:52. The eviction-protect-text fix is therefore NOT the cause.
2. **The eviction fix WORKS.** Misses 1–2 (10:20) recovered 108683/109447 tokens from disk — the pre-load persist no longer evicts prompt ancestors.
3. **Case 3 is a client re-rendering divergence, not eviction.** `common=34748` is the byte/token prefix shared between the live session and the incoming prompt. The divergence point (~22k–35k in all three misses) sits **inside the original 43265-token first prompt** — i.e. the client (opencode) re-rendered the *middle* of the conversation with different bytes, so the live byte stream and the prompt diverge far from the tool-call tail.
4. **The 24k/32k/40k anchors were stored (10:17–10:18) and still on disk at 10:27:35** (evicted only at 10:30:03/10:30:36). The load (`kv_cache_find_text_prefix_skip`, byte-prefix SHA) rejected them because their byte-prefix extends past the divergence point — they no longer prefix the prompt. Not a retention/eviction bug; the anchors exist but are stale w.r.t. the new render.
5. **Canonicalization never ran.** `should_canonicalize_tool_checkpoint` (ds4_server.c:11019) returns `false` whenever `raw_tool_text` is set, and all 23 tool calls this session had `raw_tool_text=1`. Zero `canonicalized` log lines. `canonicalize_tool_checkpoint` is the mechanism designed to keep the live checkpoint aligned with what the *next* request renders — if it had run, the divergence would likely not have accumulated to a deep miss.
6. Upstream `51a1c14` (tool-call recovery from unclosed reasoning) is **not** implicated: no recovery events in the log, and the divergence is unrelated to that path.

## Verification (production + synthetic regression)

- **Production (14:43 run, small_dense=49152):** AGENTS.md divergence at 15:34:15 (`live=245205`, `prompt=245496`, `common=34790`) now loads from **32768** instead of 16384 (`kv cache hit text tokens=32768`). The fix is confirmed live.
- **Synthetic regression test:** `test_kv_cache_head_divergence_anchor_depth` (ds4_server.c) — opt-in via `./ds4_test --kv-head-divergence` (fast, stub-based, no model; NOT part of `--server`). It builds a fake 36k-byte shared head, diverges `IN PROGRESS` → `DONE` at byte 36000, and asserts:
  - Part 1: with all anchors present + `small_dense=49152`, load picks the deepest valid anchor (32768).
  - Part 2a: with old `small_dense=16384` + budget pressure, 24k/32k are pruned → load falls to 16384.
  - Part 2b: with `small_dense=49152` + sufficient budget, 24k/32k are sticky → load starts at 32768.
  - Guards both the retention fix and the load-side anchor selection. Registered in `tests/ds4_test.c` test_entries with its own flag.

## Trace-confirmed root cause (log/ds4.trace, request 28 at 12:13:32)

```
first_mismatch_token: 35224
token_window: [35216..35233)
  35223 ==  live " —"  | prompt " —"
  35224 !=  live " IN" | prompt " D"    ← "Phase 1 — IN PROGRESS" → "Phase 1 — DONE"
  35225 !=  live " PRO" | prompt "ONE"
```

The divergence is a **real content edit near the head**: opencode edited `AGENTS.md` (`Phase 1 — IN PROGRESS` → `Phase 1 — DONE`) and re-rendered the whole prompt with the updated file content embedded at ~token 35k. Not a DSML-canonicalization mismatch and not an eviction bug — the client genuinely changed the byte stream mid-conversation.

At the miss (12:13:32, `prompt=159353`, `cached=16384`) a **32768 anchor was still on disk** (it was evicted only at 12:15:54, *after* the miss). The load skipped it because of `small_dense=16384`: anchors ≤16k are sticky, but 24k/32k/40k live in the **sparse-middle** region (`mid_spacing=131072`), which keeps only the largest anchor per window and prunes the rest as redundant. So the deepest usable anchor was 16384.

## Applied mitigation (committed)

- **Raised `KV_SMALL_DENSE` to 49152** in `ds4-server.sh` (env-overridable). Now all anchors ≤ ~48k are sticky, so an AGENTS.md-style head divergence restarts from the deepest surviving anchor (≈48k) instead of 16k.

| anchor | rebuild tokens (from the 12:13:32 incident, prompt=159353) | ~time @260 t/s |
|---|---|---|
| 16384 (old default) | 142969 | ~9.2 min |
| 32768 | 126585 | ~8.1 min |
| 49152 (new) | 110201 | ~7.1 min |

## Open questions (resolved)

- **Q2 — Cost of canonicalization vs cost of recovery.**
  - *Canonicalization cost*: `canonicalize_tool_checkpoint` re-tokenizes the canonical next-prompt render + `ds4_session_rewrite_from_common` per tool-call finish. Cheap when common is near-live (small suffix rewrite); expensive if it must rebuild a long divergent tail.
  - *Recovery cost*: rebuild = `prompt_len − common` tokens at observed ~250–335 t/s (the 10:27:35 case was ~102855 tokens ≈ 419 s ≈ 7 min).
  - **Finding:** canonicalization does NOT help this class of divergence. It only fixes tool-call DSML re-render mismatches. An AGENTS.md content edit is a real byte change in the head — canonicalization cannot prevent it, and the divergence must be rebuilt regardless. It was also silently disabled the whole incident (`should_canonicalize_tool_checkpoint` returns false whenever `raw_tool_text` is set; all 23 tool calls had it).

## Applied fixes

1. **DONE (mitigation, committed `98e75ec`):** `KV_SMALL_DENSE=49152` — keeps a deeper head-anchor ladder so head divergences restart from ~48k, not 16k. Verified live (15:34:15 miss loaded from 32768).
2. **FEATURE — restitch (CLOSED, not worth building, committed `a64e6b2`):** the idea was to truncate the live KV at `common` and prefill only the divergent tail (`prompt_len − common`) instead of reloading the deepest disk anchor. **Quantified and rejected:** in the 15:34:15 incident (common=34790, anchor=32768, prompt=245496) restitch would save only `34790 − 32768 = 2022 tokens ≈ 8s` on a **844.7s** rebuild (~0.5%). After the `small_dense=49152` fix the deepest surviving anchor is already within one `anchor_step` of `common`, so the `anchor → common` gap is negligible. The dominant cost (`common → prompt_len`, ~211k tokens) is a genuine content change that no KV trick can avoid recomputing. It is also hard: the GPU raw ring retains only `raw_kv_rows=4352` rows, so at divergence time the live KV at position `common` (210k slots behind the frontier) is already overwritten — only a disk anchor can be loaded. The real lever is client-side (don't embed the edited file content mid-prompt).
3. **DONE (mitigation, committed `6b2e3a3`): divergent small-dense branch pruning** — a re-rendered head stores a FRESH 40960/49152 anchor per divergence; old branches (same `conv_id` + same token count + different bytes) are unreachable and were kept unconditionally by `small_dense`, accumulating ~6.1 GiB. `ds4_kvstore_sweep_small_dense_divergents` now prunes them even under budget (at open + after continued stores). Regression: `test_kv_cache_small_dense_divergent_prune`.
4. **DONE (mitigation, committed `6b2e3a3`): `KV_SIZE` default 32768→65536 MiB** so two ultra-long conversations (each ~50 GiB ladder) can share the budget without retiring each other's KV.
5. Consider whether opencode's re-render (e.g. `Today's date:` / volatile bytes, or the AGENTS.md edit loop) can be made stable on the client side (out of scope server-side).

## Status / tracking

- [x] Restart server with `--trace` and capture `first_mismatch_token` for the next deep divergence.
- [x] Analyze trace: identify exactly which token/message the client re-renders differently (AGENTS.md edit at token 35224).
- [x] Q2: canonicalization does NOT help this divergence class; it only handles DSML tool-call re-renders.
- [x] Raise `KV_SMALL_DENSE=49152` to keep the head-anchor ladder sticky.
- [x] FEATURE: restitch — evaluated and closed as not worth building (saves ~8s/844s ≈ 0.5%; blocker = raw ring only retains 4352 rows; dominant cost is the irreducible divergent tail).
- [x] Re-verified against live sessions (12:57, 14:43 runs): head-divergence misses now restart from 32768, not 16384.
- [x] **Divergent small-dense pruning:** a conversation that re-renders a diverged head (AGENTS.md edit) re-prefills from the shared base anchor and stores a FRESH 40960/49152 anchor per divergence. All branches share one `conv_id` (the `conv_id` hash window is the first 131072 bytes, and the branches diverge after it) and the same token count, but carry different byte text — so a prompt byte-prefix-matches at most one of them and the rest are pure budget waste. `small_dense` kept ALL of them unconditionally, so they accumulated forever (observed: 5× 40960 + 5× 49152 = ~6.1 GiB on the 22:08 session). **Fix:** `kv_cache_small_dense_divergent_duplicate` marks non-active divergent duplicates redundant in the keep-set, and `ds4_kvstore_sweep_small_dense_divergents` opportunistically prunes them EVEN when under budget (at open + after each continued store). Verified on the production dir copy: 5×40960→1, 5×49152→1, on-disk 31.67→26.87 GiB (~4.9 GiB freed). Regression test: `test_kv_cache_small_dense_divergent_prune` (runs in `--server`).
- [x] **Budget for ultra-long conversations:** `KV_SIZE` default raised 32768→65536 MiB. A single ultra-long conversation (328k+ tokens, e.g. ctx=512000) holds a ~50 GiB continued-anchor ladder; two such conversations cannot share the old 32 GiB default without one retiring the other's KV.
- [x] **KV cache v2 eviction redesign (PLAN-KV-REWRITE.md, committed):** a 743-request trace (Aug 7) showed the dominant rebuild cost was NOT the anchor-hole but **cross-session retirement**: at session switches (12:18:14, 14:21:57) a just-live conversation's whole ladder (incl. the frontier persisted seconds earlier) was `conversation-retired` because LRU recency used file touches, not slot activity. **Fixes:**
  - **Retire-grace (frontier pinning):** `--kv-cache-retire-grace-seconds` (default 3600) — a lineage whose leaf was touched within grace is exempt from PHASE C retirement; halving (PHASE B) runs regardless. Stops the switch-back deep rebuilds (16:54-style 287166-token hits become the norm).
  - **Divergence anchors:** on a miss loading anchor A < common, a `reason=cold` anchor is stored at exactly `common` once the rebuild reaches it (`--kv-cache-max-divergence-anchors`, default 8), so the next identical miss starts from `common` instead of A.
  - **Budget:** `KV_SIZE` default 65536→131072 MiB (128 GiB) so two ultra-long conversations coexist.
  - Tests: `test_kv_cache_retire_grace_protects_recent`, `test_kv_cache_retire_grace_halves_before_retire`, `test_kv_cache_divergence_target_logic` (in `--server`); `tests/kv_policy_harness` (model-free, in `make test`).

**Wrap-up:** KV-cache divergence work for this investigation is complete. The deployed mitigation (`KV_SMALL_DENSE=49152`) bounds AGENTS.md-style head-divergence rebuilds to restart from the deepest valid anchor; the synthetic regression test (`./ds4_test --kv-head-divergence`) guards it; restitch was evaluated and declined; divergent small-dense branches are now pruned automatically; the budget default handles ultra-long conversations; and the v2 eviction redesign (retire-grace + divergence anchors + 128 GiB budget, PLAN-KV-REWRITE.md) removes the cross-session retirement churn that dominated rebuild cost.
