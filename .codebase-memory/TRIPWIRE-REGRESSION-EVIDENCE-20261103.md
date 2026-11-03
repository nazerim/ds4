# TRIPWIRE-REGRESSION-EVIDENCE-20261103 — independent battery for "tripwire re-entry escalation" (uncommitted)

**Verdicts** (details below; evidence files in
`/private/var/folders/gp/hjkf5cnx3vg4pxxv0zbd1c8h0000gn/T/opencode/`):

| # | Check | Result |
|---|-------|--------|
| 1 | Build `make ds4-server ds4_test`, zero warnings | PASS |
| 2 | Full `./ds4_test` engine-down, rc=0, set == baseline | PASS |
| 3 | Engine start (DeepSeek, :8001) + `/v1/models` ready | PASS |
| 4 | Off-path byte parity vs `resp-baseline.json` | PASS (message dict-equal True; usage == HEAD-era capture exactly except `perf_time_ms`; delta vs the older baseline file is pre-existing committed drift, classified below) |
| 5 | Budget exactness (128 -> reasoning_tokens=138 + fresh enforce line) | PASS |
| 6 | Tripwire live trip + trainer TRIP_RE compat + sidecar shape | PASS (tripped on FIRST try at temp 0; no retry needed) |
| 7 | Budget enforce line format unchanged vs HEAD | PASS |
| 8 | Engine left RUNNING on DeepSeek | PASS (PID 47339, port 8001, `deepseek-v4-flash`) |

**Scope note:** task pinned `HEAD=e94d055`; at run time the repo HEAD is
`9f73f91` (e94d055 confirmed ancestor via `git merge-base --is-ancestor`;
the extra commits are docs/trainer/tooling of the same era). The change
under test is the working-tree diff `git diff HEAD -- ds4_server.c`
(94 insertions / 18 deletions, ds4_server.c only): tw_closes / tw_suppress
re-entry escalation, valve short-circuit fix, new suppressed-close log
variant, `(gen=N)` on detector close lines, `closes`+`suppressed` usage
sidecar keys (OpenAI + Responses), extended unit tests.

---

## 1. Build — PASS

```
$ make ds4-server ds4_test
make: `ds4-server' is up to date.
make: `ds4_test' is up to date.
exit=0
```

Binary freshness proven by mtimes: `ds4_server.c 07:55:20 < ds4_server.o
07:55:28 < ds4-server 07:55:28 / ds4_test 07:55:33` (make had already
compiled the diff). Warning check re-compiled both affected TUs from
scratch with the exact Makefile flags
(`-O3 -ffast-math -Wall -Wextra -std=c99 -D_GNU_SOURCE -fno-finite-math-only`,
plus `-Wno-unused-function` for the unity test TU):

```
cc ... -c ds4_server.c        -> exit=0, stderr 0 lines
cc ... -c tests/ds4_test.c    -> exit=0, stderr 0 lines
```

Zero warnings.

## 2. Full engine suite — PASS

`./ds4-server.sh stop` -> "ds4-server stopped", port 8001 listener count 0,
`status` -> "not running". Then bare `./ds4_test` (fixture: default
`DS4_TEST_MODEL=ds4flash.gguf` -> Vision-Exp build).

- rc=0 (`ds4_test exit=0`), last line `ds4 tests: ok`
- 27 `: OK` lines + 13 skipped lines; `diff` of sorted OK/skipped multisets
  vs `.codebase-memory/baseline-thinking-20261102/ds4_test_followup.log`:
  **empty (rc=0)** — pass/skip set identical.
- `server: OK` present -> `test_tripwire_detector()` (wired at ds4_server.c
  :28517), incl. the NEW sidecar assertions (`"closes":2,"suppressed":false`,
  `"closes":5,"suppressed":true`, budget+tripwire coexistence), passed.
- Full run log: `$SP/ds4_test_full_20261103.log` (274 lines).

## 3. Engine start — PASS

```
$ ./ds4-server.sh start
Starting ds4-server on port 8001 (ctx: 1048576)...
Logging to ./log/ds4.log
ds4-server started (PID: 47339)
```

`GET /v1/models` HTTP 200 on first poll (warm KV disk cache): models
`deepseek-v4-flash` + `deepseek-v4-pro`, ctx 1048576.

## 4. Off-path byte parity — PASS

`$SP/step4_parity.py`: POST `req-baseline.json` (temp 0, seed 42,
max_tokens 1536) to `/v1/chat/completions`.

```
message_equal: True
usage ref: {"prompt_tokens":85,"completion_tokens":1536,"total_tokens":1621,
           "prompt_tokens_details":{"cached_tokens":0,"cache_write_tokens":85,"perf_time_ms":34544}}
usage new: {same counters..., "perf_time_ms":34084,
           "completion_tokens_details":{"reasoning_tokens":1536}}
```

- `choices[0].message` dict-equality vs `resp-baseline.json`: **True**
  (headline). No `thinking_tripwire` key in new usage -> the sidecar
  absence rule holds when the feature is off (detector never fired:
  log shows zero tripwire lines for this request).
- Usage strict-except-`perf_time_ms` compare vs the *older*
  `resp-baseline.json` fails on one pre-existing feature:
  `completion_tokens_details.reasoning_tokens`. Classified as **not**
  introduced by this diff, three independent ways:
  1. `git diff HEAD -- ds4_server.c | grep -E '^[+-].*reasoning_tokens'` -> empty (diff never touches the emission or its gate);
  2. HEAD source already emits it (`git show HEAD:ds4_server.c` line 7720, same `have_details` gate as working tree);
  3. behaviorally: new usage == **HEAD-era capture**
     `resp-committed-nobudget.json` (same request) exactly, except
     `perf_time_ms` (`$SP`: comparison -> True).
  `resp-baseline.json` predates that committed feature => baseline-file
  drift, off-path behavior of THIS change is byte-identical.
- Response saved: `$SP/step4-resp.json`.

## 5. Budget still exact — PASS

`$SP/step5_run.py` with the prescribed wolf-goat-cabbage payload
(max_tokens 768, temp 0, seed 42, reasoning_budget_tokens 128):

```
completion_tokens: 768
reasoning_tokens: 138        (target ~138: exact)
answer non-empty: True len: 2393
thinking_tripwire in details: False   (tripwire feature off here -> absent, correct)
```

Fresh enforce line in `log/ds4.log` (log is truncated by `start`, so every
line is post-change):

```
54: 1103 08:05:09 ds4-server: chat ctx=0..38:38 thinking budget 128 enforced
    after 128 in-span tokens (gen=128), injecting wrap-up + close
```

## 6. Tripwire live trip + trainer compat — PASS

`core_replay_check.py` does not exist in the baseline dir -> recreated per
the prescribed recipe (`$SP/step6_tripwire.py`): msg index 33 of
`dbwal-val-conversation.json` concatenated `kind==reasoning` text
(11569 chars) -> mid-slice 2000 chars (`r[4784:6784]`) -> duplicated
(slice+slice, user content 4227 chars) -> POST model=deepseek-v4-flash,
temperature 0, max_tokens 8000, thinking_tripwire=true,
tripwire_min_tokens=600, reasoning_budget_tokens=0.

Response (`$SP/step6-resp-temp0.0.json`):

```
finish=stop  completion_tokens=1820  reasoning_len=2758  content_len=4523
usage.completion_tokens_details.thinking_tripwire:
  {"tokens_in_span":696,"period":127,"closes":1,"suppressed":false}
```

Sidecar checks: all four keys present; `closes=1 >= 1`; `suppressed` is
JSON boolean `false`; no `reasoning_tokens` key (budget unset -> gate holds).

Fresh detector log line (log was 69 lines at step-6 launch, is fresh since
engine start; line 87 @ 08:10:38):

```
ds4-server: chat ctx=0..1102:1102 thinking loop tripwire: closed span at
696 tokens (period 127) (gen=696), injecting wrap-up + close
```

Trainer-compat validation, run verbatim from `/Users/naz/Projects/ds4/trainer`:

```
$ python3 -c "import sys; sys.path.insert(0,'.'); from dspark.loops import TRIP_RE; ... log/ds4.log ... 'thinking loop tripwire' ..."
True ('1102', '696')      # rc=0
```

TRIP_RE matches the live new-format line and extracts (ctx-hi, span
tokens) exactly -> `(gen=N)` addition is log-side only, trainer parses
unchanged. No `repeated re-entry` / `disabled` / `would not fit` lines
(grep count 0): single detector close, model answered after the chop —
consistent with the implementation session's two live trips (p=127 class
reproduced a third time: spans 920/1080, here 696). Full re-entry
suppression escalation (closes>=2 -> suppressed closes) was not induced
live (traffic-dependent, as the task anticipates); the sidecar shape and
detector trip — the required evidence — are fully observed.

## 7. No-regression of budget line format — PASS

The only budget enforce line this window (log is fresh since start):
`thinking budget 128 enforced after 128 in-span tokens (gen=128), injecting
wrap-up + close` — matches the HEAD format string
`"thinking budget %d enforced after %d in-span tokens (gen=%d), injecting
wrap-up + close"` verbatim. The diff touches only the TRIPWIRE close line
(adds `(gen=%d)`, TRIP_RE-compatible) and adds the suppression variants;
the budget path lines (`enforced`, `attempted close`, `would not fit`) are
format-identical when suppression is not engaged (the `would not fit`
suffix `(re-entry suppression lifted)` renders as the empty string when
`tw_suppress==false` — verified in source context of the diff).

## Static review notes (supporting evidence)

- All new code paths gated behind `tw_on` / `tw_suppress` (both false when
  `thinking_tripwire` absent) -> zero new statements off-path.
- Valve trap fix per plan: `if (!tw_suppress && ++budget_force_count > 3)`
  — short-circuit skips the increment while suppression is engaged, so
  suppressed closes cannot trip the 4-strike disable that would
  re-enable thinking.
- Escalation is once-only (`tw_closes >= 2 && !tw_suppress` on span re-open;
  `tw_suppress` sticky within the request, reset per request + on
  `decode_again` re-entry along with the other report fields).
- `decode_again` re-entry resets all four report fields (tokens, period,
  closes, suppressed) — a discarded attempt cannot leak a half-true sidecar.

## 8. Final engine state — PASS

```
$ ./ds4-server.sh status
ds4-server is running (PID: 47339)
$ lsof -nP -iTCP:8001 -sTCP:LISTEN
ds4-serve 47339 naz 6u IPv4 ... TCP 127.0.0.1:8001 (LISTEN)
$ curl http://127.0.0.1:8001/v1/models
{"object":"list","data":[{"id":"deepseek-v4-flash", ... DeepSeek V4 Flash Vision Experimental ...
```

Engine left RUNNING on the DeepSeek profile, port 8001 (the newly built
binary carrying the change). No commits made; no source files touched
(`git status` end-state: same working set as start + this evidence file;
`git diff HEAD -- ds4_server.c` still 94+/18-). Not restarted into qwen.

## Repro/sequence record

1. build check (up-to-date; scratch recompile of both affected TUs -> 0 warnings)
2. engine stop -> full `./ds4_test` (rc=0, ok-line, set-identical) -> `./ds4_test --server` re-run later while engine live (rc=0, coexisting)
3. `./ds4-server.sh start` (PID 47339, truncates log/ds4.log) -> /v1/models 200
4. step 4 parity POST -> message_equal True
5. step 5 budget POST -> reasoning_tokens=138 + enforce line
6. step 6 replay POST temp 0 -> detector trip live, sidecar 4-key, TRIP_RE True
7. final status -> running
