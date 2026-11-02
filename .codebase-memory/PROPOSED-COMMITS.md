# PROPOSED-COMMITS v2 (2026-11-03 02:2x) — execute after battery + review verdicts

Tree: `M ds4.c` (+505), `M ds4-server.sh` (+65 uncommitted remnant; budget/
tripwire script blocks already shipped in a1861bc/9acedbc), untracked:
tools/, tests/, trainer/, docs. Suite: `ds4 tests: ok` Nov-03 01:4x on the
guard-fixed tree (first REAL validation of capture+parity - prior ok-lines
were partly stale-binary artifacts). Tripwire/budget server code is already
committed (5249d80, 2fd684d, e94d055) - NOT part of this plan.

EXCLUDED BY RULE (never commit): dbwal-val-conversation.json (raw session
text at repo root - MOVE to .codebase-memory/baseline-thinking-20261102/
locally or scratch; consider .gitignore entry `*-conversation.json`), any
blade data paths, *.pt weights (trainer/v1_head*.pt - model artifacts:
recommend EXCLUDE or LFS; default: exclude, they are regenerable by rerun).

1. `server: DS4_TRAIN_DUMP capture module (ds4_td_*, v1.3b)`
   ds4.c — capture module + hooks (grab/close/segment_end/adopt sites),
   lineage-shift backstop, per-cycle label snapshot, fp8 e4m3 emitter,
   DS4_TD_DEBUG, NO_GPU guards (jitter/parity/grab stubs + qwen4_depth_*
   struct fields relocated outside GPU region so -DDS4_NO_GPU compiles).
   Evidence: day1-11 chain=100%; ds4 tests ok; validators era.
2. `server: DS4_TD_PARITY draft-parity dump`
   ds4.c — parity note fn (81,952 B/cycle: proposals + 5x4096 conf rows),
   DS4_TD_PARITY[_MAX] envs, banner. Evidence: s1 65,536 cycles zero header
   anomalies; milestone-1 conf-parity 0.033% violations (mirror validated).
3. `server: DS4_DSPARK_ALLOW_CUSTOM_SUPPORT escape + FP8_JITTER harness`
   ds4.c — revision-pin escape (family guards intact) + jitter experiment
   hook (recorded verdict: acceptance-neutral; fp8 format KEPT).
4. `server(dsh): blade KV retarget + guard, vision keys, tuned defaults`
   ds4-server.sh — KV dir on FireCuda blade + mount guard + budget 256 GiB
   (operator Nov-02 halving), vision DSPARK_KEYS, conf .45 / window 12.
5. `tests: dspark eval harnesses`
   tests/dspark_{traffic_probe.sh,temp_battery.sh,followups.sh,bounding_
   pass.py} (+ tools[] payload requirement documented inside).
6. `tools: capture audit + gguf utilities`
   tools/td_validate.py (incl clamped-streak labels0 EXEMPTION Nov-03),
   td_manifest.py, td_classify.py, td_textclass.py, vocab_ids.py,
   gguf_write.py, gguf_meta.py.
7. `trainer: stage-3 offline scaffold (night-2/3)`
   trainer/ pyproject + dspark/{data_contract,records,dataset,markov_ft,
   confidence_probe,model,gguf_dq,baseline,train_v1} + tests (12) +
   BASELINE.json + TRAIN_V1_{scale..cos32x15}.json + MARKOV/CONFIDENCE
   results — the pre-mirror era (v0 .179 / v1 .4014/.4369 evidence chain).
8. `trainer(mirror): confidence-gate parity + loop quarantine`
   dspark/mirror.py (milestone-1, s1-validated), dspark/loops.py (depth v2
   + TRIP_RE + pid-attributed span excision), train_v1 --quarantine wiring,
   tests/test_loops.py (7 loop tests, 19/19 green), LOOP-QUARANTINE json,
   TRAIN_V1_cos32clean.json (HONEST ANCHOR .4495/.5956).
9. `docs: dspark state + plans (Nov-02/03 era)`
   .codebase-memory/{DSPARK-STATE-20261102, NIGHT-RUN-2-RESULTS,
   NIGHT-RUN-3-RESULTS, PLAN-TRAINER-20261101 (+milestone-2 notes),
   PLAN-CAPTURE-PIPELINE-20261029, CAPTURE-SCHEMA (+loader rules),
   DSPARK-STATE-20261029, DSPARK-VISION-EVAL-20261028, HANDOVER-20261023,
   TODO-THINKING-BUDGET/TRIPWIRE/FOLLOWUP, THINKING-BUDGET-RESULTS,
   TRIPWIRE-RESULTS, PROPOSED-COMMITS (this)} — KEEP OUT: baseline-thinking
   dir contents if >few MB binary-ish (study logs ok), *.csv sweeps ok.
10. `repo: binary-artifact hygiene`
    .gitignore additions IF needed (verify after every merge per AGENTS:
    git ls-files | grep -E '\.o\.tmp$|^tests/(kv_policy_harness|
    test_prompt_prefix|test_spec_rejection)$' must print nothing).

Post-commit: push branch main -> nazerim (fork remote), signed (github_signing
key per SSHGUIDE). NEVER push upstream (origin=antirez).
