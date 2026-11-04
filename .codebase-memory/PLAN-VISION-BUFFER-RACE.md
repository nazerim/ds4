# PLAN — Metal batched command buffer is not thread-safe (engine abort, 2026-11-05 00:23:14)

Split out from `PLAN-DSML-REWIND.md`: the crash is unrelated to the rewind fixes, but
found while monitoring them, and the rewind path is one of the exposed callers.

### STATUS — safety net implemented and gated (2026-11-05 06:30, NOT yet deployed)
`ds4_metal.m` now records which thread owns the live batch section and refuses to let a
foreign thread join it:

- `ds4_gpu_set_batch_cb()` replaces the 10 raw `g_batch_cb = ...` writes, so a site cannot
  forget to stamp/clear ownership (nil clears it; the next opener owns it, and a foreign
  close/recreate is logged rather than silent).
- `ds4_gpu_command_buffer()` hands the shared buffer **only** to the owner. A foreign
  caller gets an owned buffer - correct for independent work like the vision encode, whose
  path already ends in `ds4_gpu_finish_command_buffer(cb, owned, ...)` - and the six sites
  that test `if (!cb || owned) return 0` now fail cleanly instead of joining.
- `ds4_gpu_tensor_copy()` (batch-only by contract) refuses foreign callers with a log.
- The compute and blit factories carry a **log-only** tripwire for direct `g_batch_cb`
  users (TP gate encode, expert-cache fill): behavior unchanged on purpose, so the field
  names the remaining sites instead of us guessing across 112 references.

Proof, same command before and after:

    $ make test-metal-command-memory            # now includes `race`
    race: rc=1  (child SIGABRT, driver assertion 1094)   BEFORE
    race: rc=0  (child survived, 0 assertions, diverted+logged)  AFTER
    default suite + row/session/batch/big TP modes: rc=0 both - owner path untouched
    make test-session-state-gpu -> rc=0 (new target; closes the CI gap for the rewind gate)

**This is a safety net, not the real fix.** It cannot make same-session GPU work correct:
if a rewind/restore runs on the rewind thread while a decode holds an uncommitted batch, an
owned buffer reads state that batch has not written yet. That class needs serialization or
thread-local sections (option C above). Watch for the log line in `log/ds4.log`:
`GPU batch section touched by a foreign thread at <site>` - each named site is a real
cross-thread dependency to fix properly.

Deploy state: binary built and tested at 06:26; **not running** - the engine was stopped
cleanly at 06:22:09 (by the operator or the bank runbook, not by me) and I have not started
it, because banking may be mid-flight.

## Symptom
`ds4-server` aborted during a live benchmark run. Sentinel alerted 19 s later
(`ALERT (engine not listening on 8001)`, 00:23:33). Crash report
`~/Library/Logs/DiagnosticReports/ds4-server-2026-11-05-002314.ips`, SIGABRT:

    -[AGXG17XFamilyCommandBuffer tryCoalescingPreviousComputeCommandEncoderWithConfig:
      nextEncoderClass:]:1094: failed assertion `A command encoder is already encoding
      to this command buffer'

First and only ds4-server crash report on this box. Not a clean stop, so no shutdown
stats dump for that boot (PID 19257). Disk KV store survived: after restart,
`kv cache discarded=0`, no checksum/verification errors, and a 229k-entry KV hit.

## Faulting interleaving (from the .ips thread stacks)
| thread | stack | lock it holds |
|---|---|---|
| 2 (decode) | `worker_main → generate_job → generate_job_inner → ds4_session_eval_speculative → ds4_session_eval_dspark_speculative_stochastic → spec_frontier_snapshot → spec_frontier_copy → ds4_gpu_tensor_copy` (mid `copyFromBuffer:`) | `model_mu` region (ds4_server.c:17412) |
| 18 (FAULTING) | `client_main → parse_chat_request → request_tokenize_multimodal_prompt → server_encode_image → ds4_engine_vision_encode_memory → ds4_gpu_deepseek4_vision_encode → glm53_vision_dispatch_rows → ds4_gpu_compute_encoder` | `inference_mu` (ds4_server.c:4002) |
| 1 | `ds4_gpu_queue_keepalive_thread` — sleeping; has its own queue (ds4_metal.m:10524) so not the culprit | – |

## Verified: generation holds NO lock while it uses the GPU
`generate_job` takes `model_mu` only around job entry/exit (ds4_server.c:17412-17414,
17420-17423) and calls `generate_job_inner` **outside** it. Inside that function
`inference_mu` is acquired at 15639 and 15747 and released at **15761** — while the
token loop that calls `ds4_session_eval_speculative` is at **16522**. So the decode path
touches the GPU with no server lock held, and is kept safe only by the one-decode-thread
discipline (`s.decode_thread` at 18743, `slot->decode_in_flight` at 15381/15530).

Consequence: "unify the locks" is not a fix, because there is no lock on the generation
side to unify with. Any GPU work from another thread — the client thread's vision encode,
or a rewind under `inference_mu` — is free to collide with a running decode.


## Root cause
The Metal layer's batch section is **process-global and caller-serialized by
assumption**:

- `g_batch_cb` (ds4_metal.m:57) is one shared command buffer; `ds4_gpu_command_buffer()`
  (1285) hands it to *any* caller whenever a section is open (`owned = 0`), and only
  allocates a private buffer when none is.
- `ds4_gpu_compute_encoder()` (1296) caches **one open encoder** (`g_batch_enc`) and
  returns it to whoever asks; `ds4_gpu_end_compute_encoder()` deliberately does *not*
  close it for the batch buffer. Direct users pass `g_batch_cb` explicitly
  (e.g. `ds4_gpu_tensor_copy` blit at 9521, compute at 10650/11172/11279).
- Nothing inside ds4_metal.m serializes encoding: `g_timeline_mutex` guards timeline
  diagnostics, `g_tensor_mu` guards tensor bookkeeping only.
- `ds4_gpu_begin_commands()` (9639) returns 0 when a section is already open, so a
  second thread cannot even tell it is joining someone else's batch.

The server enforces "one thread in GPU code" with **two different mutexes**: generation
under `model_mu`, multimodal request parsing under `inference_mu`. Lock-order scan found
no `model_mu → inference_mu` nesting anywhere (only `inference_mu → kv_mu` at 13160 and
13266), so the two regions are genuinely concurrent, and the driver abort is what
happens when they both touch the batch buffer. The quiet alternative is worse in a sense:
the second thread joins the live `g_batch_enc` and dispatches into another request's batch.

## Reproduction (deterministic, 3 s, no model, no engine)
`tests/test_metal_command_memory.c` mode `race`: thread A mirrors the decode path
(`begin_commands` → `add_tensor` → `tensor_copy` → `end_commands`), thread B mirrors the
vision encode (write/add/copy with no section of its own, so it inherits A's buffer).

    $ MTL_DEBUG_LAYER=1 ./tests/test_metal_command_memory race
    -[AGXG17XFamilyCommandBuffer tryCoalescingPreviousComputeCommandEncoderWithConfig:
      nextEncoderClass:]:1094: failed assertion `A command encoder is already encoding
      to this command buffer'
    DEFECT REPRODUCED: child killed by signal 6 (Abort trap: 6)
    rc=1   (exit 1 == defect present, 0 == child survived)

Same driver file and line (1094) as the engine crash. Implemented as a `fork`+`execvp`
child because a merely-forked child cannot reach `MTLCompilerService`
("Connection init failed"), and a driver assertion cannot be reported from inside the
process that dies by it. `race` is an explicit mode; the default argument-less run
(`make test-metal-command-memory`) is unchanged.

## Who else is exposed
Anything doing GPU work outside the generation lock. Found by grep, not by incident:

- `server_generation_rewind` (ds4_server.c:14690, holds `inference_mu`) calls
  `ds4_session_rewind_speculative` → `ds4_session_restore_speculative_prefix` →
  `spec_frontier_restore` + `ds4_session_eval_probe_tp` → GPU copies and dispatches.
  **The DSML rewind fixes increase how often this route runs** (that is their purpose), so
  on a concurrent workload they widen exposure to this pre-existing race. Single-stream
  traffic cannot hit it, which is why 12 served rewinds tonight were fine.
- `ds4_session_sync` / `ds4_session_sync_multimodal` (14522, 14593) under the same lock.
- Any future parse-time preprocessing that touches the GPU.

## Fix options
| # | change | kills the crash | fixes the class | cost / risk |
|---|---|---|---|---|
| A | ~~server-side: vision encode takes the generation lock~~ | **invalid** | no | generation holds no lock during GPU work (see above), so there is nothing to take; making it hold one across the decode loop serializes the engine |
| B | run vision encode as a queued job instead of on the client thread | yes for vision | partial | touches the request lifecycle; images parsed late means the 409/length checks move |
| C | **thread-local batch section state** (`_Thread_local` on `g_batch_cb`/`g_batch_enc` + friends) | yes | yes | small diff, big semantic change: cross-thread ordering becomes the caller's problem; must audit `g_pending_cbs`, `g_transient_buffers`, the stream-expert service thread (847/853) and the TP paths; more command buffers |
| D | mutex inside the Metal layer around the whole section | yes | yes | serializes all GPU work to one thread at a time — defeats `--batched-session` throughput, which is the feature it exists for |
| E | assertion, not a fix: record the section's owner thread and log a clear engine error (instead of a driver abort) when another thread joins it | converts abort→diagnostic | no | ~10 lines, precedent already in the file (`g_stream_expert_service_thread`) |

Recommendation, in order:
1. **Tonight, no code, no restart:** do not let an image-bearing request overlap a
   running decode — run the vision part of a batch serially, or text-only runs without
   `--vision`. The crash needs exactly that overlap.
2. **Safety net (small, reviewable):** thread-affinity on the batch section. Record the
   owner at `ds4_gpu_begin_commands()` (precedent already in the file:
   `g_stream_expert_service_thread`, 847/853) and in `ds4_gpu_command_buffer()` hand a
   *foreign* thread an owned buffer plus a one-time log naming the thread. For
   independent work like a vision encode that is correct and removes the abort; it does
   NOT make same-session ops safe (an uncommitted batch's writes would be read stale), so
   those paths must be logged and then fixed properly rather than silently forked off.
3. **Real fix:** option C (thread-local batch state), gated by the `race` mode, with
   `g_pending_cbs` / `g_transient_buffers` / stream-expert / TP auditing. That is the only
   option that makes multi-thread use correct rather than merely non-fatal.

## Operational mitigation until then
Do not overlap image-bearing requests with generation. Concretely: run the vision part of
a benchmark batch serially, or run text-only batches without `--vision`. The trigger needs
an image request arriving while a decode is mid-batch — which is exactly what happened at
00:23:14 (`request_tokenize_multimodal_prompt` for an image, decode at ctx 45,896).

## Open questions before patching
1. Is single-threaded GPU use a documented invariant upstream, or assumed? (batch/concurrent
   encoder code is upstream: `git log -S g_batch_encoder_concurrent` → the "Metal TP" series.)
   If upstream, this is worth an issue with the `race` mode attached.
2. Does `--batched-session` (Qwen profile) already rely on cross-thread batch joining? If
   yes, option C must preserve that behavior explicitly.
3. ~~`g_batch_encoder_concurrent` — does "concurrent" mean concurrent encoders?~~
   **Resolved: no.** It is set at 9890/10051/22801, each time immediately after
   `ds4_gpu_close_batch_encoder()`, for the parallel gate/up/down FFN lanes on ONE
   thread (`MTLDispatchTypeConcurrent`, see 326/1315). Intra-thread dispatch
   concurrency, not thread safety: it cannot be switched on to fix this, and a
   cross-thread join would end another thread's encoder mid-batch — the silent
   corruption mode rather than the clean abort.
