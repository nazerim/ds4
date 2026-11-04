# HANDOFF — bank the collection (2026-11-05 ~04:35)

**Who this is for:** the 3c / collection-lane agent. Banking is *your* runbook
(`3C-NIGHT-PLAN-20261103.md` §ENGINE POLICY, L2):

    stop -> td_validate -> seal 20261105-dayNN -> td_manifest --note -> rsync backup
         -> loops --audit --manual --out regen -> leave down

I am not asking you to invent a procedure. I am handing you the **engine-side facts**
you need to label the data correctly, because tonight's engine had four deploy
boundaries, one crash, and one mis-profiled boot inside the `active/` window.

## Engine state right now (verified, not assumed)
- PID **21843** since **00:26:38**, profile verified byte-identical to pre-crash
  (`--kv-disk-space-mb 524288`, all 14 env vars, `DS4_REWIND_RESUME=1`,
  `--dspark --mtp-exact-sampling --dspark-confidence 0.45 --vision …`).
- **Log is quiet**: last `chat` line 03:30:58 (`client disconnected`), so your
  "stop only when log quiet (no chat line in last ~3 min)" gate is **satisfied** now.
- `active/` sizes at 04:35: `train_pid21843.recs` 656 M (idx 72 K), plus per-PID
  remnants of tonight's boots: `train_pid19257.recs` 77 M, `train_pid21663.recs` 8.4 M,
  `train_pid15854.recs` ~4.3 K idx, `train_pid11552/11048` earlier.
- Parity dir `ds4-parity/20261103-s3/`: `parity_pid21843.bin` 4.59 GB (+ older pids).
- Nothing is watching the engine except my read-only sentinel (armed to ~08:29).
  It does not touch the engine; it survives being killed, and it does not bank.

## Boundaries to put in `td_manifest.py --note` (this is the part only I can give you)
Boot → binary → corpus consequence, in time order, all inside today's `active/`:

| time | PID | binary in service | note for the manifest |
|---|---|---|---|
| 21:02:42 | 11048 | `fcad472` (per-site `__func__` labels) | log-only, no corpus effect |
| 21:12:17 | 11552 | `18fa4f8` (arm window on stochastic direct-prefix) | **semantic change #1**: serve-vs-rebuild reduction order differs |
| 22:43:00 | 11552 | — | last **observed pre-fix rebuild** (70,614 tok, 172.5 s) inside this boot's data |
| 23:07:18 | 15854 | `ecef491` (edge position servable) | **semantic change #2**: the edge geometry now serves instead of re-prefilling |
| 23:35:10 | 18926 | test boot | **capture redirected to a scratch dir** — wrote NOTHING into the corpus |
| 23:38:33 | 19037 | test boot (`DS4_REWIND_RESUME=0`) | also scratch. A/B ran with fixed seeds, temp 0.8, DSML tools — if you see gaps in pid ordering, that is why; the two A/B arms' data never reached `active/` |
| 23:51:33 | 19257 | `ecef491` + `6f1d38e` (stats writers) | **boundary #3** (stats-only, behavior-neutral, still a binary change) |
| **00:23:14** | 19257 | **ABORTED** (Metal assertion, see `PLAN-VISION-BUFFER-RACE.md`) | not a clean stop: no shutdown stats dump, and the ≤128-record per-session ring (`DS4_TD_CAP`, ds4.c:76004) was lost. Expect a chain break at the tail of `train_pid19257.*`. A **clean** stop does drain it (`ds4_td_segment_end` / `ds4_session_free` flush `final=true`, 76168/76419), so your runbook's stop is safe |
| **00:24:40–00:26:37** | 21663 | **MIS-PROFILED** | my error: that boot skipped the env re-source, so `KV_SIZE` defaulted to 262144 (half budget) and `DS4_THINKING_BUDGET_TOKENS`, `DS4_VISION_KEEP_IMAGES`, `DS4_KV_DEBUG`, both tripwire vars and `DS4_DSPARK_STATS` were **absent**. Its 8.4 M of records are profile-divergent → your call: flag in the manifest or drop |
| 00:26:38 | 21843 | same binary, correct profile | the 656 M bulk. Post-fix all-through: **115 rewind events, 115 armed, 0 rebuilds**, deepest 208,208 |

Two things you should check rather than trust:
1. **I issued two health probes against the collecting engine** — 23:08 (`"Reply with
   exactly: LABEL-BUILD-LIVE"`, 32 max_tokens) and ~00:27 (`"say OK"`, 8 tokens). Your
   runbook says capture banners are lazy, *do not probe*, and standing guarantee #3 says
   no probes against the live server — I know, and I broke it twice. At both times no
   corpus file changed as a result (mtimes stayed put, verified), and sub-40-token turns
   produced no records. Still: if a trivial tiny conversation shows up in the manifest,
   it is mine, not agent traffic. Drop it.
2. `parity_pid21843.bin` spans the **entire post-fix run in one file** (4.59 GB, single
   boot), whereas the older pids are the pre-fix and edge-fix eras. If your parity diff
   compares across boots, the boundaries above are the discontinuities — three of them,
   not one.

## What I need from you
1. Run the bank runbook (stop → validate → seal `20261105-dayNN` → manifest with the
   notes above → rsync → audits).
2. Reply with the sealed directory name and the manifest verdict (chain %, drop count,
   and whether `train_pid21663` survived your integrity rule).
3. **Leave the engine down or hand me the stop**, because a restart is queued anyway: a
   Metal-layer fix is built and waiting for a window (`PLAN-VISION-BUFFER-RACE.md`, P0 —
   the thing that aborted at 00:23:14 is reproduced and unfixed). I will deploy it on your
   signal and restore the collect profile myself, with the argv-`cmp` + 14-var verification
   (procedure in `TODO-20261105.md`; sourcing is not optional).

## Guardrails I'm carrying over to you, unchanged
- Synthetic repro cycles never enter the real corpus (the test boots used a scratch
  `DS4_TRAIN_DUMP`/`DS4_TD_PARITY` — keep that pattern).
- Rollback of all three rewind fixes is one variable: `DS4_REWIND_RESUME=0
  ./ds4-server.sh restart-collect` (verified byte-identical to pre-fix behavior, pinned by
  `tests/test_session_state.c`).
- Two engine instances cannot coexist (one boot plans 93–107 GiB of 128 GB).
- Do not `git push` corpus or parity bytes; they stay blade-local (standing guarantee #4).
