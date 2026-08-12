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
- DSpark: `PLAN-DSPARK-PERF.md`, `PLAN-DSPARK-TEMP-SPEC.md`
- Fork divergence notes: `DS4FORK.md`

## Conventions

- C11, no C++; Objective-C only where Metal requires it; kernels in `metal/`.
- No binary artifacts tracked — keep `.o`/binaries out of commits.
- Do not commit secrets (see `auth_proxy.py` usage docs; credentials go in
  environment variables only).
