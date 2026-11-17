# Serialize-Prefill Scheduling Option — research + implementation plan (v3, final)

Repo `/Users/naz/Projects/ds4` (nazerim/ds4 fork). Research only; no repo file touched
(`git status --porcelain` → empty). Anchors read directly from `ds4_server.c` (28,960 lines).

**v3 restructure.** Two earlier framings are retracted here:
(i) my first version's "1:1 handoff arithmetic" as the explanation, and
(ii) the mid-task "the tail is tiny suffix syncs + checkpoint-evict churn, so serialize targets
the wrong case" steer.
Re-measured data (§2) says **long-prefill-interleaved-with-decode is the primary cause (64% of the
catastrophic dips)**, the evict/cold-rebuild path is a *secondary/upstream* contributor, and disk
KV I/O under the executor lock is a separate ~12% contributor. Serialize-prefill is back as the
primary scheduling candidate. Also corrected: the claim that this design's premise had been
"falsified by the `mixed=2048` re-run" — §2.6 shows **no post-2048 traffic exists in any log**, so
that conclusion cannot be drawn from this data (the design point in §3 stands on its own, not on
that re-run).

---

## 1. Scheduler map (as briefed, re-verified)

### 1.1 Threads / locks
| Actor | Anchor |
|---|---|
| client thread → job | `enqueue()` `ds4_server.c:17641` |
| N slot workers (`--batched-session N`) | `slot_worker_main()` `17690` (spawned `18826`), `generate_job()` `17477` |
| 1 decode coordinator | `decode_worker_main()` `15523` (spawned `18819`) |
| router | `dispatch_jobs_locked()` `17578`, `job_slot_score()` `17543` |

`struct server` `11095`–`11133`: `mu`, `tool_mu`, `kv_mu`, `inference_mu` (**recursive**,
`18746`–`18749` — the model executor), `model_mu`+`model_cv` (`11113`–`11114`) guarding
`model_busy` `11115`, `decode_pending` `11118`, `active_generations` `11119`,
`last_prefill_slot` `11122`.

**Lock topology, including the I/O finding (verified, not assumed):**
* `model_mu` and `inference_mu` are **never co-held** — `server_prefill_enter()` releases `model_mu`
  (`14509`) before taking `inference_mu` (`14510`); `server_prefill_leave()` releases `inference_mu`
  (`14516`) before touching `model_mu` (`14518`); `decode_worker_main()` releases `model_mu`
  (`15577`) before `inference_mu` (`15581`). Any mid-hold peek at `decode_pending` would create new
  nesting → avoid (see §5.2).
* **All disk KV I/O runs inside the executor critical section.** Stores: `kv_cache_store_live_prefix_text()`
  locks `inference_mu` `12722` → nested `kv_mu` `12916` → `ds4_kvstore_store_live_prefix_text`
  (the actual `fwrite`+`fflush`: `ds4_kvstore.c:3362`–`3368`) → unlock `kv_mu` `12925`,
  `inference_mu` `12927`. Loads: `kv_cache_try_load_text()` `13187`→`13199` (nested `kv_mu`
  `13194`–`13198`) and `kv_cache_try_load_vision()` `13297`→(nested `kv_mu` `13300`–`13304`).
  And the pre-load save: `kv_cache_store_current(…, "evict", …)` at `15928` immediately before
  `kv_cache_try_load()` at `15933` — one routing decision can cost store+load, both under
  `inference_mu`. No fsync (only `fflush`), so the observed cost is copy+page-cache, not durability.
  Fork precedent for the fix pattern: the tool-map bootstrap moved its I/O outside `tool_mu`
  ("Guarded by tool_mu with the install phase outside it, since tool_memory_put_source takes the
  same non-recursive mutex", `.codebase-memory/adr.md:317`) — never applied to `inference_mu`.

### 1.2 Prefill: the executor is acquired **per quantum**, never per load
* `server_prefill_enter()` `14481`–`14512`: sets `prefill_waiting` (`14493`), waits on `model_cv`
  while any of `g_stop_requested` / `slot_job_cancelled` / `model_busy` / **`decode_pending > 0`** /
  "not my round-robin turn" (`14495`–`14499`; `server_next_prefill_slot_locked()` `14472`–`14479`).
  Grant → `last_prefill_slot = id` (`14507`), `model_busy = true` (`14508`) → `inference_mu`.
* `server_prefill_leave()` `14514`–`14522`.
* Quantum: `server_prefill_quantum()` `14535` → pure `server_prefill_quantum_for()` `14524`–`14533`
  (`generation_active ? mixed_prefill_quantum : (idle_prefill_quantum>2048?idle:2048)`, GLM-5.3
  floor `1024` at `14528`–`14531`). `idle_prefill_quantum` is set only for DeepSeek V4.1
  (`18718`–`18721`), so **on Qwen3.8 mixed == idle == the same number** once
  `QWEN_MIXED_QUANTUM=2048` (`ds4-server.sh:118`, commit `676eca7`) — confirmed by the live startup
  line `prefill_quantum=2048 mixed_prefill_quantum=512` (`log/ds4-qwen.log.20261117-094911:18`).
* Loops: `server_session_sync()` `14546`–`14587`, `server_session_sync_multimodal()`
  `14608`–`14672`; per iteration `{ quantum → target clamp 14567–14569 (image-end rounding
  14639–14653) → enter 14573/14656 → ds4_session_sync 14574/14657 → done 14575/14660 → leave
  14576/14661 }`. **Chunk boundary ≡ executor-release boundary** — the exact coupling this option
  must break.
* Guard: strict `done < target` (`14580`, `14665`) ⇒ `quantum == 0` + `done > 0` spins forever;
  `14569`/`14638` only rescue `done == 0`. Unreachable via CLI (`parse_int_arg` `18441`/`18199`
  rejects `v <= 0`). Never use 0 as "unlimited".
* Other in-flight executor holders: `append_rendered_suffix_to_live_session()` `14689`;
  `canonicalize_tool_checkpoint()` `15340`; **`server_generation_rewind()` `14812`, called from
  inside the decode loop (`16898`)** — the trap that kills any drain-based policy.

### 1.3 Decode
`server_generation_enter/leave()` `15397`–`15411` (`16345`/`16910`, i.e. **after** prompt prefill
`16119`–`16123`). `server_eval_tokens()` `15427`–`15497`: one outstanding token per slot (`15454`);
`decode_pending`/`s->decode_pending++` (`15465`–`15466`); waits `decode_done` (`15468`–`15482`);
cancel only withdraws *pending* tokens (`server_cancel_pending_decode_locked()` `15416`–`15425`),
never in-flight ones (`15478`–`15481`). `decode_worker_main()` `15523`–`15625`: coalesce
(`15539`–`15552`, `server_decode_coalesce_us()` `15505`, env `DS4_SERVER_DECODE_COALESCE_US`,
default 2000 µs, until `decode_pending == slot_count` or `== active_generations`) →
`while (model_busy) cond_wait` (`15554`) → claim (`15561`–`15574`) → `model_busy=true`, unlock
`model_mu`, lock `inference_mu`, `ds4_sessions_eval_batch[_speculative_argmax]`
(`15581`–`15590`) → release + publish (`15601`–`15617`).
⇒ **A decode step can never preempt a running prefill chunk; it queues behind it.**

---

## 2. Measured evidence (re-derived; scripts in `…/T/opencode/scratch/*.py`, outside the repo)

Source: `log/ds4-qwen.log.20261117-094911` (11-15 07:43 → 11-17 09:49, single server lifetime,
startup line `prefill_quantum=2048 mixed_prefill_quantum=512`, `--batched-session 2`). Parsed
46,536 `decoding chunk=X t/s` samples (50 tokens each), 2,079 prompt start/done pairs, 4,646 prefill
chunk lines, 792 stores, 81 loads, 93 eviction lines.

**2.1 The dips are long prefills.**
| activity holding the executor at the sample | n | %all | <10 t/s | enrich | p50 |
|---|---|---|---|---|---|
| long prefill (interval ≥5 s), ±1.5 s pad | 127 | 0.3% | **72** | 261× | 6.1 |
| short prefill (<5 s) | 1,877 | 4.0% | 3 | 0.7× | **61.3** |
| KV store only | 927 | 2.0% | 6 | 3.0× | 57.6 |
| nothing else | 43,602 | 93.7% | 20 | 0.2× | 58.3 |

Strict per-sample containment (no padding): 74 samples (0.16%) fall inside the 88 long-prefill intervals,
their sub-10 enrichment 405×; **65 of the 101 catastrophic (<10 t/s) samples — 64% — are inside
one** (p50 3.2 t/s, p10 1.7).
**Short prefills are measurably harmless: p50 61.3 t/s, 3 dips in 1,877 samples.**

**2.2 Magnitude.** 88 long intervals, 2,577 s of executor-held long prefill, 2.42M new tokens.
p50 14.5 s / p90 56 s / max 590 s. Worst: 11-16 11:45:52→11:55:48, 180,222 new tokens at
ctx 262,144→442,366, 595.6 s, aggregate **305 t/s**, with 13 co-resident decode samples at
p50 **1.5 t/s**.

**2.3 Co-residency, not depth, costs prefill throughput.** Long intervals ≥16,384 new tokens:
**solo median 1,353 t/s (n=49)** vs **co-resident median 859 t/s (n=39)** — a 37% penalty. Depth is
not the driver (solo shallow 1,333 vs solo deep 1,265, −5%). So today's interleaving is the worst of
both: prefill 37% slower *and* decode at 2–3 t/s.

**2.4 The counterfactual for a block policy (co-resident long prefills only).** 710,724 tokens /
1,185 s of measured co-resident wall. At the solo rate (1,353 t/s) the same work takes 525 s ⇒
**~660 s (56%) of wall time is destroyed by interleaving**, and 2,350 decode tokens (median 2.8 t/s)
convert into ~38,600 decode tokens in the recovered time. That, not "fairer handoffs", is the win.

**2.5 Secondary: KV I/O under the executor.** 792 stores, total **238.3 s** (p50 258 ms, p90 501 ms,
max 1,156 ms, payloads 140–660 MiB); 81 loads, total **96.3 s** (p50 821 ms, p90 2,501 ms,
max 4,893 ms; restore chain p50 12 / max 28 files). Directly explains 12–14 of the 101 dips
(±3 s window) and adds 0.1–4.9 s to *any* hold, which matters for §5's cap sizing. Upstream of the
long prefills themselves: 93 `reuses nothing; evicting checkpoint` events (`job_slot_score`
`17543`–`17576`, cheapest-victim = `SLOT_PROTECTED_SCORE_BASE - live`, `17541`), 17.46M tokens of
checkpoints dropped, and 84–87% of long-prefill volume starts within 10 s of an eviction /
`live kv cache miss reason=token-mismatch` (all 93 misses are token-mismatch, median common prefix
70 tokens; the measured examples are cross-conversation placements, e.g. `live=92808 prompt=233941
common=4885`) — with 2 slots and alternating agent sessions, eviction is structural routing, not a
bug. Treated here as ranked item #3 (§7), not as the primary explanation.

**2.6 The "we already tried the bigger quantum" premise is not supported by the logs.**
The only startup line anywhere with `mixed_prefill_quantum=2048` is `log/ds4-qwen.log:18`
(11-17 09:49:13) — that instance has 6 log lines and zero prefill events. The analysed log's 11-17
lines cover only 00:01–00:13 and 09:24–09:49 (`grep -c "^1117 03:" → 0`); the 180,222-token episode
is 11-16 11:45 at quantum **512**, and no ~1,135 s episode appears in any log. So quantum 2048 has
never been measured under load, and the "2 samples, both still <10 t/s" figure is n=2 — no
statistical weight either way. **This does not rescue the wider-quantum idea**: §3 shows it is the
wrong instrument on first principles, and §2.1's short/long asymmetry says the discriminator is
*load duration*, not chunk size. But the re-test should be run for real, and it is cheap (§7/§9).

**2.7 Unexplained residual, stated plainly.** 25 of 101 dips have no store/load/evict within ±3 s and
are not inside a long interval: 15 of them sit in one 26-minute rebuild-storm window
(11-16 08:16–08:41, which also contains 11 long intervals and 2.4 s loads — i.e. likely adjacent to
prefill work my interval model doesn't cover), 10 scattered at 3.5–9.9 t/s. Closing that gap needs
the mechanism analysis another agent owns (§8 Q2), plus the observability in item #4 of §7.

---

## 3. Why "wider quantum" is NOT the same option as serialize/block (four points)

1. **Bigger chunk = longer stall.** A 65k chunk delays decode 65k/859 ≈ 76 s instead of 512/859 ≈
   0.6 s; interleaving still happens, just coarsely. Serialize never interleaves.
2. **`server_prefill_quantum()` is re-read every iteration (`14566`) and release is tied to the
   chunk (`14573`/`14576`)** — no quantum value makes the loop stop releasing. The release is a
   *statement*, not a consequence of the quantum. Only hoisting the enter/leave (or making the loop
   run once) removes it.
3. **Quantum also changes engine chunk truncation.** `target` becomes the prefix length handed to
   `ds4_session_sync()` (`14574`), which internally clamps to `cap_tokens` (`ds4.c:80787-80788`,
   resolved to 8192 on Qwen3.8 by `ds4.c:1063`–`1071`), so the quantum alters engine chunking, MTP
   prep boundaries (`ds4.c:80841`–`80850` "uninterrupted prefill must use the same real boundary")
   and prefill numerics (`ds4.c:1060`–`1062` reduction-order note). A policy flag that keeps the
   quantum and moves only the release does not perturb chunk shapes at all.
   (§2.3's 859-vs-1,353 t/s is the *observed* cost of staying co-resident; whether that specific
   number is per-chunk cost, cache thrash or graph re-entry is the other agent's question. My
   recommendation survives either answer — §8.)
4. **`generation_active` is evaluated per chunk (`14537`), not per load**, so a load can change
   behaviour mid-flight (a generation starting/ending between chunks flips mixed↔idle). A per-load
   hold decision fixes it (and must, since the measured dips coincide with exactly these overlaps).

---

## 4. Options on their merits

| Option | Mechanism | Verdict |
|---|---|---|
| **A. Blanket block** (serialize everything, no yield) | one hold per load | Fixes the 64% (§2.1) but also serializes 96-token suffix syncs and mid-generation rewind rebuilds — the measurably harmless population (§2.1 short-prefill p50 61.3) — and turns a 590 s load into a 525 s *total* blackout. Not shippable as-is. |
| **B. Minimum-slice gate alone** (don't interleave below N remaining) | small syncs atomic, big loads keep interleaving | **Inverted value**: short syncs are already harmless; long loads are the whole problem. Alone, benefit ≈ 0. |
| **C. Gate + block + max hold cap** (recommended) | block only when remaining ≥ `min_tokens`; cap each hold at `hold_max_tokens` | Targets the population that produces the dips, leaves the harmless population untouched, bounds blackout, keeps round-robin meaningful between holds, keeps cancel/stop latency bounded. See §5. |
| **D. Interleave-better** (decode-priority grant, N decode steps per release) | change who wins `model_cv` | Ceiling is set by §2.3: any co-residency costs prefill ~37%. Cannot make both fast. Worth one experiment, not a flag. |
| **E. Engine mixed prefill+decode batch** | one epoch serves both (`ds4.h:652`, `ds4.c:85951`) | The real long-term fix; unavailable: `#if defined(__APPLE__) return false` (`ds4.c:89682`–`89690`), qwen4 excluded (`ds4.c:90178`–`90184`), `support_kind != DS4_SUPPORT_NONE` gate (`ds4.c:85410`), rows ≤ `DS4_TP_BATCH_MAX_ROWS` (`ds4.c:85382`); no server callers today. Track upstream. |
| **F. Drain-wait** (`active_generations == 0` before prefill) | gate on generation count | **Rejected: deadlock.** `server_generation_rewind()` (`16898`→`14812`) prefills from inside its own generation, and that slot is already counted at `16345`. |
| **G. Just raise `--mixed-prefill-quantum`** | existing flag | §3 points 1–4; never measured under load anyway (§2.6). |

---

## 5. Recommended design: **C — gated, capped serialize**

### 5.1 Semantics
`--prefill-policy serialize` ⇒ when a generation is active on *another* slot and the load's
remaining work ≥ `prefill_serialize_min_tokens`, the executor is acquired once per **hold** instead
of once per quantum. Quanta remain the KV/multimodal slice unit; only the *release* moves. A hold
ends on: load complete, cancel/stop, or `prefill_hold_max_tokens`. Everything below the gate —
suffix appends (`14689`), canonicalizations (`15340`), rewind rebuilds (`14812`), short prefills —
behaves exactly as today.

### 5.2 Exact change points
| Change | Anchor | Detail |
|---|---|---|
| enum + fields | `11120`–`11122` | `int prefill_policy; int prefill_hold_max_tokens; int prefill_serialize_min_tokens;` (enum near `11095`) |
| slot field | `10803` | `bool generating;`, set/cleared in `server_generation_enter/leave()` `15397`–`15411` (add a `slot` parameter; sites `16345`, `16910`) — this is what structurally exempts `14812`/`14689` |
| pure decision fn | new, immediately after `server_prefill_quantum_for()` `14533` | `static bool server_prefill_hold_allowed(const server *s, bool generation_active, bool same_slot_generating, int remaining)` ⇒ `policy==SERIALIZE && generation_active && !same_slot_generating && remaining >= min_tokens`. Pure ⇒ unit-testable with no threads (§6) |
| hold body, text | loop `14562`–`14584` | hoist enter (`14573`)/leave (`14576`) around the loop; keep per-iteration quantum/target/done (`14566`–`14569`, `14574`–`14575`); make the enter conditional (`if (!holding) enter()`); every return path (`14578`, `14580`–`14583`, `14585`) must leave first; release+re-enter when the token cap is hit so round robin (`14472`, `14507`) still operates |
| hold body, multimodal | loop `14632`–`14668` | same surgery; image-end rounding `14639`–`14653` unchanged (it only widens `target`) |
| stop-aware cancel | `17483` | install `server_job_or_stop_cancelled()` = `g_stop_requested \|\| job_cancelled(ud)` (today `job_cancelled` `11178`–`11185` ignores stop), so a hold stays interruptible per engine chunk (`ds4.c:80781`–`80785`) for SIGINT as well as disconnect |
| CLI | beside `18437`–`18441` | `--prefill-policy interleave\|serialize`; `--prefill-hold-max-tokens N` (`parse_nonneg_int_arg` `18206`); `--prefill-serialize-min-tokens N`; `server_config` field `18193`, default block `18346`, unknown-value handling matching `18196`–`18214` |
| init + startup log | `18728`, `18791`–`18797` | print policy / gate / cap next to the existing quantum line |
| per-hold log | beside `16160`–`16166` | `hold tokens=%d remaining=%d elapsed=%.1fs released_for=%s` — today a 590 s blackout leaves no direct trace, and §7 item #4 depends on this existing |
| help | `ds4_help.c:351`–`352` | new rows; cross-ref `--prefill-chunk` (`ds4_help.c:179`, server parse `18514`–`18521`) since a hold hands chunking back to the engine |
| docs | `docs/SERVER.md:89`–`91` | "Long prefills yield to active decoders in bounded intervals, normally 128 tokens" becomes policy-dependent |
| launcher | `ds4-server.sh:104`–`118`, `607`–`618` | `QWEN_PREFILL_POLICY`, `QWEN_PREFILL_MIN_TOKENS`, `QWEN_PREFILL_HOLD_TOKENS`; note `ds4-server.sh:116`–`117` already names serialize-prefill as the intended follow-up to the 512→2048 change |

Size: ~90–120 lines in `ds4_server.c`, 2 in `ds4_help.c`, docs + launcher. No `ds4.c` change, no new
lock ordering (hold length is tracked from executor-local state: token counter + `now_sec()`; never
take `model_mu` while holding `inference_mu` — if a "something is waiting" valve is ever wanted,
`pthread_mutex_trylock(&s->model_mu)` at the boundary only).

### 5.3 The default cap, from the data
Measured: 2 of 88 long intervals exceed 120 s; 7 exceed 60 s; p50 14.5 s. Serialized at the solo
rate, the worst case (180,222 tokens) is ~133 s of total 0 t/s instead of 595 s at 1.5 t/s; with
`hold_max_tokens = 65536` it is ≤ ~48 s per hold (65,536/1,353) plus one decode window each time.
Recommend default `hold_max_tokens = 65536` and `prefill_serialize_min_tokens = 8192` (== engine
chunk == continued-store grid `ds4_kvstore.c:2576`–`2600`, so the gate never lands between rungs),
then re-decide after one week of the per-hold log (§9).

### 5.4 Two-part justification
1. **Primary (§2.3/§2.4):** interleaving costs prefill ~37% *and* decode ~97%; blocking converts a
   lose-lose regime into "load finishes ~2× sooner, then decode runs at 58 t/s". On the measured
   window that is +~36k decode tokens and −~660 s of wall time, and it eliminates the population
   that holds 64% of the catastrophic dips.
2. **Explicitly not the mechanism I first claimed.** My v1 model — "strict 1:1 alternation, so decode
   gets one step per chunk" — predicts ~29 t/s at quantum 512, not 1.5 t/s, and the log refutes it:
   inside the 590 s episode decode advanced ~1.85 steps per chunk while each chunk cost 1.68 s
   (512 tokens at 305 t/s), not 0.4 s. **The stall lives inside co-resident chunks, not in the
   handoff count.** So do not sell this option as "fewer context switches": sell it as "no
   co-residency". Corollary: the fairness cap must not be tuned to *increase* release frequency —
   more releases mean more co-resident samples, i.e. the bad regime more often (§2.3).

---

## 6. Risks and what to test

1. **Never encode "unlimited" as quantum 0** (strict `done < target` spin, `14580`/`14665`;
   currently CLI-blocked `18441`→`18199`). Use `hold_max_tokens = 0`.
2. **Blackout is the product decision**: bounded by `hold_max_tokens / 1353 t/s` (≈48 s at the
   recommended cap; ~133 s uncapped for the 180k case). Non-streaming clients just wait; a streaming
   client sees **zero bytes**, since the SSE keepalive (`14907`–`14927`) only runs on the prefilling
   request's progress callback while the blocked request parks in `pthread_cond_wait` (`15481`) —
   check proxy/first-token timeouts (`auth_proxy.py`; `DS4_SERVER_IO_TIMEOUT_SEC` `48` is write-side).
3. **Cancel/disconnect**: loop checks per iteration (`14564`, `14633`); engine polls per chunk
   (`ds4.c:80781`–`80785`) and keeps completed chunks (`80783`, `80865`–`80867`) ⇒ an interrupt loses
   ≤ one engine chunk, not the load. Dead-client detection rides the progress callback
   (`14914`/`14923`) ⇒ survives serialization.
4. **Shutdown**: without the §5.2 cancel-predicate fix, SIGINT waits the whole hold (stop is only
   checked at `14564`/`14633`). Verify `server_request_decode_stop()` `18612`–`18617` still drains.
5. **Rewind/suffix syncs must never hold** (§1.2, `16898`→`14812`): this is also why F is dead.
   `SERVER_REWIND_REBUILD_TPS` `14706` (~350 t/s) shows how long those rebuilds already are.
6. **KV grid coupling is benign**: continued rungs are written from the progress callback
   (`14932`/`14973` → `13094` → `13011` → `ds4_kvstore.c:2622`) which **snaps down** to the deepest
   grid boundary ≤ live (`ds4_kvstore.c:2632`–`2656`; kill switch `2616`); grid =
   `lcm(anchor_step 8192, align, prefill_chunk)` (`2576`–`2600`), doubled above 48k
   (`2604`–`2612`, ADR `.codebase-memory/adr.md:27`). Engine chunk == grid step, so rungs still land
   (log-confirmed at 8,192/16,384/32,768/49,152 boundaries). Assert
   `test_kv_cache_continued_step_aligns_to_prefill_chunk` (`26991`). Cold-store suppression
   (`16069`–`16079`, `13033`) is boundary-independent.
7. **Stores/loads lengthen holds** (§2.5) — under a hold they can no longer overlap another slot's
   decode at all, so item #3 of §7 has value *after* this option too.
8. **Multimodal**: rounding `14639`–`14653` stays correct; the vision encoder runs under
   `inference_mu` (`11144`, cache `11108`) so image-heavy loads hold longer. Keep `19514`/`19521`
   resume-frontier tests green.
9. **GLM-5.3 floor ≥1024** (`14528`–`14531`) must not be bypassed — the policy changes releases,
   not the quantum, so it is inherited; assert it in the new unit test.
10. **Prefill-vs-prefill fairness degrades by construction**: round robin (`14472`, `14507`) acts at
    release granularity; with the cap it still alternates per hold, without it two queued 180k loads
    fully serialize.
11. **Numerics**: unchanged *by design* (quantum still truncates `target`, so engine chunks stay
    512/2048); verify against `tests/test_qwen4_prefill` (`tests/test_qwen4_prefill.c:42`, `:52`) and
    `tests/test-vectors/qwen38-flashnext/*.vec` (`DS4FORK.md:1467`, `docs/QWEN38_FLASH_NEXT.md:523`).
    **Default stays `interleave`.**
12. **Routing/dispatch unaffected** (`17578`/`17543` score on reuse+staleness, not executor state).

### Tests to add — suite entry `ds4_server_unit_tests_run` `28731`, registrations `28740`–`28741`
Model-free: `ds4_server_test.o` built with `-DDS4_SERVER_TEST` (Makefile `904`–`910`), binary
`1474`–`1479`, run **`make test-server-logic`** (`Makefile:1484`; comment `1482`–`1483`: safe
alongside a live engine, unlike `make test`, which needs the engine stopped — `AGENTS.md`).
1. `test_prefill_policy_option` (next to `test_mixed_prefill_quantum_option` `18972`–`18988`):
   default `interleave`; flag parsing incl. unknown-value rejection; full truth table for
   `server_prefill_hold_allowed()`; assert `server_prefill_quantum_for()` returns identical values
   for both policies (protects the GLM-5.3 floor + 2048 idle floor that test already pins).
2. `test_batched_prefill_hold_round_robin`: extend `test_batched_prefill_round_robin`
   (`18951`–`18970`) — `last_prefill_slot` advances once per hold; with a cap, two waiting slots
   still alternate per hold.
3. `test_prefill_hold_release_is_exception_safe`: each early return (`14578`, `14580`–`14583`,
   `14585`, multimodal twins) releases exactly once — no `model_busy` leak. Highest-risk bug class
   in the hoisted enter/leave refactor.
4. Stop/cancel predicate test reusing `test_cancel_server_init` (`24552`–`24559`, already inits
   `model_mu`/`model_cv`) plus the `test_cancelled_progress_callback_is_inert` (`24537`) pattern.
5. Live: `tests/test_server_batching.py` with the policy on; mid-hold disconnect in the style of
   `tests/live_disconnect_test.py` (`:182`); `tests/kv_cache_integration.py` for ladder survival.
   Acceptance = re-run the §2.1/§2.3 analysis script on a fresh log: sub-10 dips inside long
   prefills → 0; co-resident long-prefill aggregate rate ≥ 1,200 t/s; no hold exceeding the cap;
   per-hold log lines present. **Also re-measure quantum 2048 vs 512 (§2.6) while at it — that data
   does not exist yet.**

---

## 7. Ranked by expected benefit for THIS workload (2 concurrent agent sessions, tool round-trips, large evolving contexts)

1. **C: gated + capped serialize (§5).** Addresses 64% of catastrophic dips; recovers ~56% of
   co-resident prefill wall time and ~36k decode tokens per 2-day window of this traffic; opt-in,
   ~2 days, no numerics change.
2. **Observability first (cheap, do it with #1 or before it).** Per-hold log line + a periodic
   "executor held by / decode gap" counter. Without it, 25% of dips are unattributable (§2.7) and
   neither #1 nor #3 can be validated on the live blade. Today a 590 s blackout is invisible except
   by inference from chunk lines.
3. **Move KV store/flush out of `inference_mu`** (stage under the lock, `fwrite`+`fflush` after;
   the pattern already applied to `tool_mu` per `.codebase-memory/adr.md:317`). 238 s of stores +
   96 s of loads, p90 load 2.5 s, max 4.9 s, with chain walks of up to 28 files (`13187`/`12916`,
   `15928`+`15933`). Directly ~12–14% of dips and it shrinks every hold #1 creates.
4. **Reduce the evict/reuse-miss storm that *creates* long prefills** — 93 events, 17.46M tokens
   dropped, 84–87% of long-prefill volume within 10 s of one (§2.5). Largest theoretical win
   (removes the symptom's cause), but it is a checkpoint-keying/routing investigation, not
   scheduling; with 2 slots it is partly structural. Start at `job_slot_score` `17543`–`17576`,
   `slot_probe_reuse_locked` (`13531` comment), `live kv cache miss` log `15861`,
   `SLOT_STALE_AFTER_SEC` `17530` / `SLOT_PROTECTED_SCORE_BASE` `17541`.
5. **Real engine mixed prefill+decode batching (E)** — the only thing that makes prefill and decode
   genuinely concurrent; needs upstream Metal support (`ds4.c:89682`–`89690` today).
6. **B alone (min-slice gate) / D (interleave-better) / G (bigger quantum)** — no measured
   standalone benefit; B ships as part of C, D is an experiment, G is §3's strawman.

## 8. Robustness to the open mechanism question (owned by another agent)
Deliberately **not** analysed here: whether the server quantum bounds one uninterrupted GPU stretch,
per-chunk cost vs context depth, what a decoder actually waits on. Mapping my conclusions:
* **Independent of it:** the F deadlock rejection; B's "short prefills are harmless" verdict (§2.1,
  pure log observation); the §2.5 store/load-under-`inference_mu` finding and its ranking; the
  cancel/stop/rewind guardrails; all anchors, tests, docs touchpoints.
* **Holds under either outcome:** C's core claim. If the quantum *does* bound one stretch → a hold is
  one uninterrupted pass, decode simply waits, and the cap bounds it. If it *doesn't* (e.g. the
  engine already runs many internal chunks per `ds4_session_sync`, which `ds4.c:80780`–`80871` says
  it does, so a single 180k-`target` sync is *already* one acquisition covering 352 internal chunks)
  → the policy still removes the release points that let decode into the middle of that work.
* **Direction that flips with it:** the cap. If co-resident cost is per-release overhead, a *smaller*
  cap with a priority-for-decode twist (D) could be better; if it is per-co-resident-sample cost,
  the cap should be *larger* (fewer co-resident samples), not smaller. §2.3's solo-vs-co-resident
  1,353/859 spread is consistent with the latter, which is why §5.3 picks 65,536 rather than
  something tiny, and why item #2 of §7 (observability) gates the final default.

## 9. Effort
Observability + core policy (fields, decision fn, 2 loop rewrites, `generating` flag, stop-aware
cancel) **1 d**; CLI/help/docs/launcher **0.5 d**; unit tests + `make test-server-logic` **0.5 d**;
live A/B on the 512k box (2 slots, one long rebuild + one decoding session, sweep
`hold_max_tokens` ∈ {0, 16k, 65k, 200k} × quantum {512, 2048}, re-run the §2 analysis) **0.5–1 d**.
**Total ~2.5 engineer-days** for #1+#2 of §7. (§7 #3 ≈ 1–1.5 d extra; #4 is its own investigation.)

## 10. Docs / help touchpoints
`ds4_help.c:351`–`352` (+ `:179` cross-ref); `docs/SERVER.md:89`–`91`;
`docs/QWEN38_FLASH_NEXT.md:193`–`205`, `:523`; `ds4-server.sh:104`–`118` + `607`–`618`;
`DS4FORK.md:1437`–`1460` (stale twice: default moved to 2048, and its "interleaving frequency" model
is contradicted by §2.3/§5.4); amend the rationale comment at `ds4_server.c:14542`–`14545`
("without monopolizing the model executor") per `AGENT.md:20`.

## 11. Open questions
1. Default cap: 65,536 tokens (§5.3) or operator-chosen? Answer depends on §8's last bullet, i.e. on
   the mechanism result.
2. Should `serialize` also apply when nothing else generates? On Qwen the idle path is pinned to
   2048 (`14527`, `idle_prefill_quantum` unset for non-V4.1 `18718`), slicing the engine's own 8192
   chunk with no competitor at all — a one-line throughput bug, independent of this option.
3. Promote or keep the flag? `AGENT.md:23` ("do not add permanent semantic variants behind flags")
   pushes toward eventually making "hold long loads, never yield for short ones" the single release
   path — worth an ADR in `.codebase-memory/adr.md` after one A/B cycle.
4. Is the 25-sample residual (§2.7) prefill-adjacent work my interval model misses, or something
   else entirely? Needs the §7 #2 counters before it can be answered.
