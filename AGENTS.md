# AGENTS.md — DwarfStar 4 (ds4)

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
- **KV cache checkpoints matter.** Long agent sessions rely on disk KV persistence (`ds4_kvstore.c`). The live KV state must stay consistent with the rendered token prefix. Token-mismatch after recovery injection is a known issue — see `DS4PLAN.md`.
- **Thinking mode has a recovery path.** When the model emits tool calls inside `<think>`, `chat_think_tool_recovery()` injects `</think>`. This can cause downstream KV cache misses if the injected tokens diverge from what the next request expects.
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
- **Fork:** `nazerim/ds4` (nazerim remote) — has DSML recovery hardening commits ahead of upstream
- **Local mirror:** `/Users/naz/Projects/ds4-upstream` tracks `antirez/ds4` for comparison

## Known Issues

1. **DSML tool inside `<think>`** — model sometimes emits `<｜DSML｜tool_>` inside unclosed `<think>`. Recovery forces `</think>` after 9 tokens. **Fixed:** `think_tool_recovery_fired` flag forces checkpoint canonicalization after recovery, preventing the token-mismatch KV cache miss that previously caused 7+ minute full prefills. See `BUGFIXPLAN.md`.

## Resolved Issues

- **KV cache miss after think-tool recovery** — Fixed by forcing canonicalization when `think_tool_recovery_fired` is set. The live KV suffix is rewritten to match what the client will replay, avoiding token-mismatch.
- **DSpark confidence default** — Changed from 0.9 to 0.6. The 0.9 threshold was too conservative, rejecting most draft tokens and negating MTP throughput gains.
- **No-tools test model compatibility** — Test now uses `server_model_syntax_for_engine()` and `parse_generated_message_ex_for_syntax()` to handle both DeepSeek and GLM models correctly.

## Style

- C99, `-Wall -Wextra`, zero warnings expected.
- Comments explain *why* (shape, ordering, cache boundary), not *what*.
- Keep public APIs narrow. CLI/server must not know tensor internals.
- Prefer comments beside implementation over separate design docs.
- Follow existing patterns. Read `AGENT.md` for deeper design philosophy.
