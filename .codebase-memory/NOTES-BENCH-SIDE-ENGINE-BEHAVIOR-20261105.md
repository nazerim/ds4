# Bench-side engine behavior notes (20261105) — for the engine/dspark lane

Findings from running the 3c A/B campaign as an external client. None
require engine changes; several will bite the next person who doesn't
know. Ordered by surprise-cost.

1. **DSpark stats flush at `ds4_session_free` (pool evict / shutdown),
   NOT per request.** Each boot era emits ONE aggregate `ds4: DSpark
   stats` line into its own log; the next boot's rotation snapshots it.
   Any protocol that parses "the live log after a sweep" is broken by
   design; parse the rotated era file instead. (Our first v5 window
   lost everything because stops landed on busy sessions -> force-kill
   -> no free -> no line.) The fix on our side: stop, then WAIT and
   verify the line before proceeding — worth 30 s at every capture.

2. **Force-kill is the default when clients are in-flight.** A client
   mid-long-prefill cannot observe the stop request until the prefill
   completes; graceful drain silently becomes "Warning: Force killing".
   Bench-side rule we now follow: only stop after the sweep process has
   fully exited.

3. **Greedy t=0 replay is NOT perfectly deterministic across arms**:
   2/9 pairs diverged (tool-call args 199 vs 268 ctok; prose 300 vs
   300) — consistent with batched-vs-single logit reduction ties
   flipping argmax, which the rewind-window code itself acknowledges
   ("serve vs rebuild are not bit-identical (reduction order)").
   Protocols assuming byte-identity of greedy replays need a
   hash-and-drop step; ours drops divergent pairs before scoring.

4. **`tool_choice` is parsed but only `none` has effect**
   (ds4_server.c:4477-4485). Prompt + history-shape is the only tool
   forcing available to clients. Related: canned *prose* assistant
   echoes in history teach the model away from tool-calling (2/14
   turns); canned REAL tool shape (assistant tool_calls + role=tool
   results) restores it (12/14) — matters for anyone synthesizing
   agent-like traffic.

5. **`ds4-server.sh` collect arm resolves `${DS4_TRAIN_DUMP:-real}` /
   `${DS4_TD_PARITY:-real}`** — ABSENT env re-arms the real corpus.
   Dev/smoke boots must REDIRECT both to /tmp paths, not unset them.
   `DS4_TD_PARITY_MAX=0` does not disable (coerced to 4096,
   ds4.c:76172).

6. **Prompt-prefix disk-KV is shared across engine boots and arms** —
   a 66k-token prefix that cost minutes cold costs seconds on any
   later boot (measured ftok 3-5 s at 66-98k). Great for replay
   benchmarks (our scored cycles ride informal's warmth); one privacy
   note: content-addressed cache persists across restarts on the blade
   (budget env `KV_SIZE`, default 262144 MiB — half what the day-14
   incident needed; we run 524288).

7. **char->token ratio spans 0.24-1.04 tok/char across real
   conversation content types.** Any client-side context-size
   estimation must measure (`prompt_tokens` from usage) — every
   static estimate we shipped failed at least once (v5.0 timeouts,
   v5.1 off-band).

8. Engine-side epoch detail that saved us: session/conversation
   identity for prefix reuse survived restarts, so a killed bench
   leaves cheap residue, not poison; and `tests/test_session_state.c`
   (`DS4_REWIND_RESUME` rollback pin) made arms safe to interleave
   with the collecting engine.

9. PARITY DUMP STALE-ROW BUG (20261106 audit): ds4_td_parity_note copies
   conf_hidden slots d<dlen only; slots >= dlen in its static
   hidden_rows[5][4096] are leftovers from the PREVIOUS record while the
   function comment claims zero-padding beyond draft_len (props ARE
   zero-padded; rows are not). Harmless to kept-row analyses (consumers
   truncate at dlen) but silently unmeasurable beyond-dlen scores; fix =
   read all 5 slots or zero the tail. Found by interpretation review
   ses_e4fb7acdc; relevant to any future gate-economics replay.
