# AGENTS.md — ds4 fork (nazerim/ds4)

Fork of [antirez/ds4](https://github.com/antirez/ds4), DeepSeek V4 Flash
inference engine in C with Metal, CUDA, and ROCm backends. See `AGENT.md`
(upstream agent notes) for goals, quality rules, safety, and layout.

## Remotes

- `origin` — upstream antirez/ds4
- `nazerim` — fork at github.com/nazerim/ds4
- Local `main` tracks upstream and carries fork work (KV cache v2 rewrite,
  DSpark, server hardening). Integrate upstream as real merge commits, never
  force-push to `main`.

## Build & test

- `make` — build all binaries (ds4, ds4-server, ds4-bench, ds4-eval, ds4-agent)
- `make test` — unit/regression tests (needs a model and Metal on macOS)
- `make clean` — remove build artifacts
- `make strix-halo` / `make cuda` / `make rocm` — platform-specific builds
- `make dspark-acceptance`, `make dspark-verify-depth`, `make mtp-verify-depth` —
  specialized verification targets
- Live server tests live in `tests/` (e.g. `tests/kv_cache_integration.py`) and
  are only for intentional API-surface testing.

## Workstreams

- KV cache v2 rewrite: `PLAN-KV-REWRITE.md`, `PLAN-KV-LINEAGE.md`,
  `.codebase-memory/adr.md`
- Checkpoint self-healing + session handoff: `.codebase-memory/adr.md`
  (ADR 2026-09-06) and `.codebase-memory/TODO-20260906.md` (PR states,
  pending #984 port, open questions, proven diagnostics).
- Vision cache (thinking bridge, encoder cache, multimodal disk KV):
  `.codebase-memory/adr.md` (Sep 3 ADRs); server script serves Vision-Exp
  by default (`./ds4-server.sh start`, TRACE_PATH=./log/ds4.trace for cache
  forensics; first-mismatch window lands in log/ds4.trace on live misses)
- DSpark: `PLAN-DSPARK-PERF.md`, `PLAN-DSPARK-TEMP-SPEC.md`
- Fork divergence notes: `DS4FORK.md`

## Conventions

- C11, no C++; Objective-C only where Metal requires it; kernels in `metal/`.
- No binary artifacts tracked — keep `.o`/binaries out of commits. This has
  regressed once already: `b3a4cc5` untracked four `.o.tmp` files and three test
  binaries and added ignore rules, then the upstream merge `7d7b8cc` was an evil
  merge that resurrected all seven *and* dropped the `.gitignore` block. After
  every upstream merge, re-check both:
  `git ls-files | grep -E '\.o\.tmp$|^tests/(kv_policy_harness|test_prompt_prefix|test_spec_rejection)$'`
  must print nothing, and `git check-ignore tests/kv_policy_harness` must match.
  `tests/kv_policy_harness` and `tests/test_prompt_prefix` are Makefile targets,
  so untracking them is always safe. `tests/test_spec_rejection` has no source and
  no rule — its `.c` was deliberately removed in `dd1a02a` when upstream stochastic
  decoding superseded the fork's rejection-sampling verifier — so if it ever
  reappears, delete it rather than trying to rebuild it.
- Do not commit secrets (see `auth_proxy.py` usage docs; credentials go in
  environment variables only).
