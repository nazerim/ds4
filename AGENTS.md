# AGENTS.md — ds4 fork (nazerim/ds4)

Fork of [antirez/ds4](https://github.com/antirez/ds4), DeepSeek V4 Flash
inference engine in C with Metal, CUDA, and ROCm backends. See `AGENT.md`
(upstream agent notes) for goals, quality rules, safety, and layout.

## Remotes

- `origin` — upstream antirez/ds4
- `nazerim` — fork at github.com/nazerim/ds4
- Local `main` carries fork work (KV cache v2, DSpark, server hardening); integrate upstream as real merges, never force-push.

## Build & test

- `make` — build all binaries (ds4, ds4-server, ds4-bench, ds4-eval, ds4-agent)
- `make test` / bare `./ds4_test` (needs a model + Metal) require the engine
  STOPPED — it holds a process lock, tests refuse mid-suite (Error 2). Runbook:
  `./ds4-server.sh stop` → tests → `start-qwen` (`restart-qwen` = fresh binary).
  `--server` and `--mtp-slice` subsets coexist with a running engine, but
  `ds4_test --server` does NOT cover server code (it links `ds4.c`); for
  `ds4_server.c` use `make test-server-logic` → `ds4_server_test`.
- `make clean` — remove build artifacts
- `make strix-halo` / `make cuda` / `make rocm` — platform builds; `make
  dspark-acceptance`, `dspark-verify-depth`, `mtp-verify-depth` — verification
- Live API-surface tests in `tests/` (e.g. `kv_cache_integration.py`) are only for intentional API testing.

## Workstreams

- KV cache: v2 rewrite `PLAN-KV-REWRITE.md`, `PLAN-KV-LINEAGE.md`, `.codebase-memory/adr.md`
  (2026-11-17 ADRs), `HANDOFF-20261117-PREFILL-DIPS.md`; disk-KV chunk schedule + eviction
  density (2026-11-19) → `HANDOFF-20261119-KV-CHUNKSCHEDULE.md`, `.codebase-memory/20261118-kvstorm/`
- Checkpoint self-healing + session handoff: `.codebase-memory/adr.md`
  (ADR 2026-09-06) and `.codebase-memory/TODO-20260906.md` (PR states,
  open questions, proven diagnostics).
- Vision cache (thinking bridge, multimodal disk KV; embed cache=KEEP since F1 11-10 —
  `.codebase-memory/adr.md` Sep-3 ADRs + amendment, HANDOFF-DECODE-FIX §5; Vision-Exp default, TRACE_PATH=./log/ds4.trace)
- DSpark: `PLAN-DSPARK-PERF.md`, `PLAN-DSPARK-TEMP-SPEC.md`
- V4.1 decode degeneration — SOLVED 2026-11-08, lane FINALIZED 11-09 (512k all-spec
  dspark = script default, validated 319k): `PLAN-DECODE-DEGRADE-20261108.md`,
  `HANDOFF-DECODE-FIX-DSPARK-20261108.md`.
- Qwen3.8 PLE n-gram preload/cache: `PLAN-PLE-PRELOAD.md`
- Qwen3.8 prefill/decode optimization: `PLAN-PREFILL-M5.md`, `PLAN-INT8-MOE.md` (closed),
  `PLAN-GATED-SCAN.md` (closed), `.codebase-memory/QSA-ATTRIBUTION-20261004.md`
- Live-state + handoffs: `.codebase-memory/RESUME-MERGE-CLOSEOUT-20261106.md` (supersedes
  the closed `PLAN-DSML-REWIND.md` stall lane, defaults now on).
- Fork divergence notes: `DS4FORK.md`

## Conventions

- C11, no C++; Objective-C only where Metal requires it; kernels in `metal/`.
- No binary artifacts tracked. Regressed once (`b3a4cc5` left artifacts untracked;
  merge `7d7b8cc` resurrected them and dropped the ignore block). After every
  upstream merge, re-check both:
  `git ls-files | grep -E '\.o\.tmp$|^tests/(kv_policy_harness|test_prompt_prefix|test_spec_rejection)$'`
  must print nothing, and `git check-ignore tests/kv_policy_harness` must match.
  `tests/kv_policy_harness` and `tests/test_prompt_prefix` are Makefile targets,
  so untracking them is always safe. `tests/test_spec_rejection` has no source and
  no rule — its `.c` was deliberately removed in `dd1a02a` when upstream stochastic
  decoding superseded the fork's rejection-sampling verifier — so if it ever
  reappears, delete it rather than trying to rebuild it.
- Dated docs rot: before following a file path in a workstream doc, check
  `.codebase-memory/DOC-ROT-20261117.md` (name is stale; regenerate via `doc-rot-scan.py`).
- Do not commit secrets (see `auth_proxy.py` usage docs; credentials go in
  environment variables only).
