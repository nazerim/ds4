# Draft — upstream question (ds4 / antirez/ds4): Metal batch command buffer & multi-thread callers

**Status: DRAFTED, NOT FILED.** Awaiting operator go-ahead. Fork-side evidence lives in
`.codebase-memory/PLAN-VISION-BUFFER-RACE.md`; reproduction is in-tree.

---

### Title
`ds4_metal.m`: the batch command buffer + cached encoder are process-global and unguarded — is "one thread does GPU work" an invariant?

### Body

We hit a production abort on Apple Silicon (M5 Max) and traced it to a threading assumption we
can't find documented. Asking before patching, because the right fix depends on which way you
intended it.

**Symptom.** Engine dies with a driver assertion under concurrent load (a long sampled decode
running while another request's multimodal prompt was being parsed):

```
-[AGXG17XFamilyCommandBuffer tryCoalescingPreviousComputeCommandEncoderWithConfig:
  nextEncoderClass:]:1094: failed assertion
  `A command encoder is already encoding to this command buffer'
```

Two threads in the crash report were inside GPU code simultaneously: one in
`ds4_gpu_tensor_copy`'s blit encoder (reached from `ds4_session_eval_dspark_speculative_stochastic`
→ `spec_frontier_snapshot`), one in `ds4_gpu_compute_encoder` (reached from
`server_encode_image` → `ds4_engine_vision_encode_memory`).

**What we found in the Metal layer.** The batch section is a set of process globals with no lock:
`g_batch_cb`, `g_batch_enc`, `g_batch_has_work`, `g_batch_encoder_concurrent`.
`ds4_gpu_command_buffer()` hands the live `g_batch_cb` to **any** caller while a section is open,
and `ds4_gpu_compute_encoder()` returns the single cached `g_batch_enc` to whoever asks.
`ds4_gpu_begin_commands()` returns 0 when a section is already open, so a second thread cannot
even tell it is joining someone else's batch. `g_timeline_mutex` guards diagnostics and
`g_tensor_mu` guards tensor bookkeeping — neither guards encoding. Two sibling arrays are in the
same state: `g_transient_buffers` (~20 mutation sites, reached from generic helpers like
`ds4_gpu_new_transient_buffer()`, `ds4_gpu_router_project_select_fused_tensor()`,
`hc_rms_norm`/`hc_expand` paths) and `g_pending_cbs`, with no mutex, `@synchronized`,
`os_unfair_lock` or atomic at any site.

**Reproduction (no model, ~3 s).** Two threads: A runs the decode shape
(`begin_commands` → `add_tensor` → `tensor_copy` → `end_commands`), B does GPU work with no
section of its own. Deterministically trips assertion 1094. It lives in-tree as a mode:

```
MTL_DEBUG_LAYER=1 ./tests/test_metal_command_memory race
```

**Our reading of the server side.** We serialize generation with `model_mu` and multimodal request
parsing with `inference_mu` — two different mutexes over one GPU. Also: `generate_job()` takes
`model_mu` only around job entry/exit and calls `generate_job_inner()` outside it, which releases
`inference_mu` before the token loop — so generation touches the GPU holding **no** mutex, and is
kept single-threaded only by the one-worker/one-coordinator discipline
(`worker_main` in non-batched mode; a single `decode_worker_main` coordinator over N slot threads
when `--batched-session N`).

### Questions

1. **Is "exactly one thread submits GPU work" an intended invariant** of the Metal batch section,
   or an accident of the current server topology? If intended, what is the recommended
   serialization point for a caller that needs GPU work from a request-parsing thread — and should
   `ds4_gpu_begin_commands()` distinguish "already open by me" from "already open by another
   thread" (e.g. return an owner token, or `pthread_self()` check) so violations fail loudly
   instead of aborting in the driver?
2. **Should `g_transient_buffers` / `g_pending_cbs` be protected independently of the section?**
   They look like they outgrew the single-thread assumption. We'd rather fix these upstream than
   carry a fork-local divergence — is a mutex acceptable on that path (it is per-buffer-alloc
   hot), or do you prefer an owner-thread-only contract with an assertion?
3. **`share_session_prefill_workspace = batched_sessions > 0`** suggests batched mode deliberately
   shares state across threads. Could you confirm what is *meant* to be shared across slot threads
   vs per-thread? That single answer decides whether making the section thread-local is a valid
   fix or would break the batched profile.

### What we have locally, pending your answer
- A guard that refuses cross-thread joins (foreign caller gets an owned buffer; batch-only
  primitives fail and log instead of joining), which converts the abort into a defined outcome.
- The `race` mode as a regression gate.
We're happy to send whichever shape you prefer — owner-token assertion in the Metal layer,
thread-local sections, or a documented single-GPU-thread contract enforced by an assert.

*(From the nazerim/ds4 fork; Vision-Exp Metal engine. Happy to attach the crash report and the
per-thread backtrace table.)*
