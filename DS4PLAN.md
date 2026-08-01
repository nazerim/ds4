# DS4 Server — DSML Recovery Hardening

**Status:** Complete & committed
**Commit:** `a171132` — "fix: harden DSML recovery, add post-recovery validation and streaming fallback" (2026-07-30)
**Repo:** `nazerim/ds4` (fork of `antirez/ds4`); local `main` is 12 commits ahead of upstream `origin/main`
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
  (`ds4_server.c:11418–11425`) — preserves tool calling + MTP.
- Setup hoisted above the `decode_again:` label (`ds4_server.c:11427`) to avoid per-retry spam.

### 3.2 Post-recovery validation + streaming fallback (M2)
- Eager trial-parse validation at tool-block close (`observe_tool_markers` at `11672`;
  validation/recovery branch ~`11729–11783`).
- Streaming fallback: `strip_dsml_keep_prefix()` + `clamp_live_stream_positions()` then
  `finish=stop` (call sites `11765–11766`, `11866–11867`, `11961–11962`).
- `saw_tool_start = false` reset after fallback (`11773`, `11869`) to prevent re-triggering.
- Output truncated at the marker before injecting recovery in `chat_think_tool_recovery`.

### 3.3 Bounded retry + parser robustness (M3)
- Bounded counter `dsml_recovery_attempts` (init `11412`; guard `< 2` at `11738`/`11832`/`11921`;
  increments `11747`/`11848`/`11940`) replaces the single-shot flag.
- Bare-parameter parsing without an `<｜DSML｜invoke>` wrapper (`ds4_server.c:4870–4901`;
  rationale comment at `4871`). Invoke macros at `4552–4559`; tag-variant selection `4837–4852`.

### 3.4 New helpers (`ds4_server.c`)
- `strip_dsml_keep_prefix(buf*)` — `5238`: removes DSML markup while keeping any plain prefix.
- `clamp_live_stream_positions(...)` — `7560`: re-clamps plain/OpenAI/Anthropic/Responses stream
  byte offsets after stripping so clients never see out-of-range positions.

### 3.5 Launch-script tuning (`ds4-server.sh`)
- `CTX=256000` (:9), `MTP_DRAFT=1` (:17), `MTP_MARGIN=3` (:18), `DSPARK_CONFIDENCE=0.6` (:19).
- Wired through `--mtp … --dspark --mtp-draft … --mtp-margin …` (:85) and
  `--dspark-confidence …` (:86).

## 4. Tests (`tests/ds4_test.c`)

| Test | Line | Notes |
|---|---|---|
| `test_think_tool_recovery` | 6242 | Strengthened with `inject_at` assertions (6338–6342): `> 0`, `< text.len`, marker-position check |
| `test_dsml_token_suppression_excludes_id` | 6699 | GPU-gated; skips when `ds4_engine_dsml_id() < 0` (GLM) |
| `test_no_tools_request_yields_no_tool_calls` | 6744 | **New (working tree)** — compaction regression: a tool-less request (`has_tools=false`) decoded with the M1 DSML-token ban yields no tool marker and zero `tool_calls`; GLM (`dsml_id<0`) skips suppression but still asserts no call. Flag `--no-tools-no-tool-calls` |
| `test_strip_dsml_keep_prefix` | 17628 | `strlen`-based assertions (rewritten via Python generator) |
| `test_clamp_live_stream_positions` | 17692 | Verifies offset re-clamping across all stream formats |
| bare-parameter parser test | via `test_server_unit_group` (6722) | Covers invoke-less parameter emission |

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
- **`ds4-server.sh` DSPARK wart — FIXED (working tree, uncommitted).** Defaults now resolve via
  `${VAR:-…}` at the top (`MTP_DRAFT` / `MTP_MARGIN` / `DSPARK_CONFIDENCE`), so env overrides work
  for all three; the redundant override block was removed and the help text corrected to `0.6`.
- **Untracked artifacts — FIXED (working tree, uncommitted).** Added `*.o.tmp` and `log/` to `.gitignore`.
- **Upstream drift:** local `main` is 12 commits ahead of `antirez/ds4` (0 behind); rebase/merge periodically.

## 8. Reference index (@ `a171132`)

| Symbol | Location |
|---|---|
| `ds4_engine_dsml_id` (def) | `ds4.c:36672` |
| `ds4_engine_dsml_id` (decl) | `ds4.h:313` |
| DSML suppression setup | `ds4_server.c:11418–11425` |
| `decode_again:` label | `ds4_server.c:11427` |
| `dsml_recovery_attempts` (init / guards / incs) | `11412` / `11738,11832,11921` / `11747,11848,11940` |
| `strip_dsml_keep_prefix` | `ds4_server.c:5238` |
| `clamp_live_stream_positions` | `ds4_server.c:7560` |
| bare-parameter parser | `ds4_server.c:4870–4901` |
| invoke tag macros | `ds4_server.c:4552–4559` |
| DSpark/MTP tuning | `ds4-server.sh:9,17–19,85–86` |
