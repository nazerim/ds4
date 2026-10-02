# HANDOVER 2026-10-02B — golden-vectors follow-ups (tasks 2 & 3)

> DONE same day: task 2 shipped as `addae6c` (env-anchor guard), task 3 as
> `2c580eb` (`--local-golden-capture`, `# model` header self-activation,
> capture->verify pin, qwen38-flashnext fixture; 3 cold-boot captures
> bit-identical). Text below kept as the design record.

> Fresh-session entry point for the `--local-golden-vectors` work only.
> Everything else from Oct-2 (fixes deployed, cache state, decisions) is in
> HANDOVER-20261002.md — that file stays authoritative; this one supersedes
> its "Known-drift" NEXT-item #2 with full research + the two open tasks.

## Live state (verify first, standing rule)

- `main` @ `5492039` == `nazerim/main` (pushed). HEAD contains the full
  afternoon review-fix round (`8261e64..ac1225f`, all signed, each commit
  compile-verified standalone).
- Engine PID 87986, booted 16:04:54, binary == HEAD. WARN 0. Cache dir
  `/Volumes/FireCuda520/ds4-kv-qwen` ~90 files and growing, zero warnings.
- Ops rule re-confirmed: bare `./ds4_test` (and thus full `make test`)
  REFUSES while the engine holds the process lock ("another ds4 process is
  already running"). Flow: `./ds4-server.sh stop` → tests →
  `./ds4-server.sh start-qwen`. (`--server` / `--mtp-slice` subsets coexist
  fine with a running engine.)

## Research result (this session) — the failure is identity mismatch, not drift in our code

- **Origin: 100% upstream.** Test `17502b9` (antirez, May 28, "Add local
  golden inference drift test"); fixture versioned by checkpoint `b7e9f00`
  (Aug 3); usage documented in upstream `QA_BEFORE_RELEASES.md:85`, which
  expects an explicit `DS4_TEST_MODEL=/path/to/0731.gguf`.
- **Fixture**: `tests/test-vectors/flash-0731/local-golden.vec`, header:
  `# ds4-local-golden-v1` / `# DeepSeek V4 Flash 0731, captured from the
  dated Q2 imatrix GGUF on Metal.` Grammar (see `test_read_local_golden_case`
  / `test_fill_local_golden_case`, tests/ds4_test.c:6316-6362):
  `case <id> <mode> <ctx> <frontier> <prompt-file> <top-count>` then
  `top <rank> <token-id> <logit>`. Thresholds (ds4_test.c ~6455-6460):
  top1 exact, top5_overlap≥4, top20_overlap≥15, top64_overlap≥40,
  top20_max_abs≤8.0.
- **What actually runs here**: `DS4_TEST_MODEL` defaults to `ds4flash.gguf`
  (Makefile:22), which is a SYMLINK (Sep 21; the "119 bytes" is the link
  target string length — do not be fooled) to
  `gguf/DeepSeek-V4-Flash-Vision-Exp-…gguf` (90.9 GiB). So the suite
  compares the Vision-Exp experimental weights against a golden captured
  from a DIFFERENT checkpoint/quantization (0731 Q2 imatrix base, not on
  this disk — only derivatives exist in gguf/).
- **Signature matches**: deterministic (identical 9.45367 across runs AND on
  clean `8e4b0f8` baseline — verified), top1 still matches, only the
  near-tie ordering (top5 3/5) and spread (max_abs 9.45>8) drift. Not a
  fork regression; the test just lacks a model-identity guard.
- **No recapture tool exists** anywhere upstream or in-repo; antirez's
  capture was manual and one-off.

## Task 2 — skip guard (hygiene, small)

Make `test_local_golden_vectors` (tests/ds4_test.c:6470) SKIP unless the
loaded engine is plausibly the fixture's model. Copy the established pattern
at ds4_test.c:155-164 (`ds4_engine_is_qwen4()` gate, `puts("…: required,
skipped")`). Options, cheapest first:
- gate on env anchor: skip unless `DS4_TEST_LOCAL_GOLDEN_MODEL` is set and
  matches `DS4_TEST_MODEL` (operator points both at a real 0731 gguf when
  they have one); or
- record model identity in the fixture (`# model <general.name or sha>`
  header line) and compare against the open engine — cleaner, but touches
  fixture format (version the comment, readers ignore `#` lines already).
Acceptance: `make test` GREEN end-to-end on this box (step reports skipped),
unchanged behavior when the correct model IS supplied; suite still registered
as `--local-golden-vectors`.

## Task 3 — fork-versioned golden capture (the real net, medium)

Follow the `b7e9f00` precedent: capture per checkpoint.
1. Add `--local-golden-capture` to the suite table (registration pattern:
   ds4_test.c:7697) that replays the SAME case prompts (`case … text 5000
   4096 tests/long_context_story_prompt.txt 64` and any others in the .vec)
   through `test_open_engine(false)` under the same canonical streaming
   prefill env forces the reader path uses, and emits the `# ds4-local-golden-v1`
   + `top <rank> <id> <logit>` grammar to stdout/file.
2. Engine STOPPED during capture (Metal lock + determinism). Current runs
   are bit-stable (same 9.45367 thrice), so capture should be reproducible —
   re-run capture once and diff before committing.
3. Land as `tests/test-vectors/qwen38-flashnext/local-golden.vec` (header
   comment naming `gguf/Qwen3.8-Flash-Next-Q4.gguf`); default
   `DS4_TEST_LOCAL_GOLDEN_FILE` resolution stays upstream-safe (env override
   only; don't silently repoint upstream's default).
4. Pin it: add a harness/ds4_test assertion that capture→verify is a no-op
   (freshly captured fixture passes its own thresholds trivially) so the
   net can't silently regress to always-skip.
NOTE for both tasks: the fixture's ref logits belong to a Q2 model; thresholds
were tuned for that. A Qwen3.8 capture re-establishes its own baseline — first
capture IS the golden, no independent check of correctness at t=0 (same
weakness upstream has; acceptable for drift detection only).

## Discipline (this session's lessons)

- Never `cat`/`head` binary or gguf files into the shell — context poisoning
  (happened this session with the symlink stat confusion; `file`/xxd ≤64B
  ranges or a parser-to-file instead).
- TDD doctrine holds: RED against original logic (worktree at old SHA +
  additive-only API shims) → GREEN; all-8-commits compile loop via
  throwaway worktree; hygiene post-check: `git ls-files | grep -E '\.o\.tmp$|^tests/(kv_policy_harness|test_prompt_prefix|test_spec_rejection)$'`
  empty, `git check-ignore tests/kv_policy_harness` matches.
- Commits: repo style `kv:`/`server:`/`tests:`/`docs:` lowercase, em-dash,
  WHY in body, signed (gpgsign=true, SSH key), push to `nazerim` only,
  never force.

## Verification commands

```sh
cd /Users/naz/Projects/ds4
git log --oneline -3          # 5492039 docs, ac1225f tests, 48852ba server
pgrep -f 'ds4-server --model' # expect 87986 (or operator's new boot)
readlink ds4flash.gguf        # symlink → vision-exp gguf (context for task 2/3)
./ds4_test --local-golden-vectors 2>&1 | tail -3   # needs engine STOPPED; ERR is known
make tests/kv_policy_harness && ./tests/kv_policy_harness  # all green
./ds4-server.sh start-qwen    # restore after any engine-stopped test run
```
