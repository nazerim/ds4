# Moving disk-KV I/O out from under `inference_mu` — safety analysis and gated design

Status: design only. No repo files were modified; the live engine (PID 64188) was
not touched.

Every symbol, line number and measurement below was read or measured in this
session. Where I could not verify something it is labelled **UNVERIFIED** or
**ESTIMATE** with its basis. Commands are quoted so the numbers can be re-derived.

---

## 0. Method, and a correction to the handoff's headline number

The handoff
(`.codebase-memory/HANDOFF-20261117-PREFILL-DIPS.md`) reports "66 of 101
**7.32×**". I re-ran the attribution on
`log/ds4-qwen.log.20261117-094911` (10 MB, 1115-1117) and could **not reproduce
66 exactly**; the answer depends on the sign convention of the window:

```
Total decode=46536, slow(<10 t/s)=101, healthy=46435
orig_kv window (ts-12s, ts+3s): slow=69/101 (68.3%), base=4209/46435 (9.1%), enrich=7.55x
orig_kv window (ts-3s,  ts+12s): slow=61/101 (60.4%), base=4259/46435 (9.2%), enrich=6.58x
```

Both are in the same range and the handoff's 66 sits between them, so the
conclusion stands; the exact figure is convention-dependent. Reporting this
because the whole point of the brief is that numbers must be reproducible.

A **tighter and better-founded** test — "did a KV event fall inside the decode
sample's own measurement interval" (`[ts - n_tokens/tps_interval - 3s, ts + 3s]`)
— gives a much larger effect:

```
interval variant: slow=80.2%  base=3.6%  enrichment=21.98x
  <5 t/s (n=69):  92.8% have a KV event in their own interval
  5-10 t/s (n=32): 53.1%
```

Caveat: that variant is **length-biased** (a slow sample has a longer interval
and therefore more chance to contain an event), so the fixed-window 6.5–7.5× is
the number to quote. The interval result only says the association is strong
enough that it survives a different, more local test.

The genuinely unexplained residue is real and I measure it consistently: **20 to
32 of 101** dips have no KV store/load anywhere near them depending on the
window, and the ones I inspected are prefill-adjacent (a decode sample on one
slot while another slot's `prefill chunk` lines are still landing — e.g. log
line 30379 at 3.69 t/s sits between two prefill chunks of a different request).
That is the 1.15× class the handoff already refuted as a *cause*; it is a
different, smaller effect.

### Other figures I re-measured (all from the same log)

| Quantity | Measured | Command/method |
|---|---|---|
| stores | n=792, p50 258.5 ms, p90 500.7 ms, max 1155.7 ms | parse `save=` on `kv cache stored` |
| loads | n=81, p50 821.2 ms, p90 2500.9 ms, max 4892.6 ms | parse `load=` on `kv cache hit text` |
| store sizes by reason | continued 353 @ 569.7 MiB avg; cold 345 @ 135.2; evict 92 @ 419.1; shutdown 2 @ 345.3 | `reason=`, `size=` |
| slot evictions | **93**, 5 stale / 88 protected, **17,463,570 tokens dropped** | `request reuses nothing; evicting checkpoint` |
| evictions followed by store+load ≤8 lines later | **66 of 93** | window scan |
| disk-file evictions | 522 | `kv cache evicted` |
| delta chain length | avg 12.7, max 28 | `chain=` on load lines |
| "corrupt text-prefix payload" discards | 41 | see §4.3 |
| `prefill hold` WARNING lines | **0** (log predates `cbc4db6`) | grep |

Note the handoff's store/load percentiles are within rounding of mine
(258/500/1156 vs my 258.5/500.7/1155.7; 804/2470/4893 vs 821/2501/4893). The
"93 evictions, 17.46M tokens dropped" figure that was flagged **UNVERIFIED** in
the brief is in fact exactly right — I reproduced 17,463,570 tokens.

---

## 1. Safety analysis of the current locking

### 1.1 The lock graph as it exists (measured, not assumed)

`struct server` (`ds4_server.c:11095`) declares five mutexes:
`tool_mu` (11110), `kv_mu` (11111), `inference_mu` (11112), `model_mu` (11113),
`trace_mu` (11142). A sixth, `server_log_mu`, is a file-static
(`ds4_server.c:10580`).

`inference_mu` is **recursive**:

```
ds4_server.c:18797   pthread_mutexattr_settype(&inference_attr, PTHREAD_MUTEX_RECURSIVE);
ds4_server.c:18798   pthread_mutex_init(&s.inference_mu, &inference_attr);
```

That single line is why the re-entrancy bug in §1.5 is invisible today.

Observed nesting (each verified by reading the site):

| Order | Where | Notes |
|---|---|---|
| `inference_mu` → `kv_mu` | store `ds4_server.c:12733`→`12927`; text load `13198`→`13205`; vision load `13308`→`13311` | the two paths in question |
| `inference_mu` → `kv_mu` → `tool_mu` | store: trailer size/write hooks run inside `ds4_kvstore_store_live_prefix_text`, which is called under `kv_mu`; hooks take `tool_mu` (`12111`, `12143`) | verified |
| `kv_mu` → `server_log_mu` | every `kv_logf` under the store/evict | leaf |
| `tool_mu` alone | `dispatch_jobs_locked` `17626`, `kv_cache_store_current` `12974` | never holds `inference_mu` |
| `model_mu` alone | `server_prefill_enter` `14504`, `server_prefill_leave` `14553` | taken and released *around* the `inference_mu` acquire, never nested |
| `trace_mu` alone | `14017`, `14064`, `14072`, `14097` | leaf |

**No path holds `kv_mu` and then acquires `inference_mu`.** The two candidates
release first:
`kv_cache_discard_failed_disk_entry` unlocks `kv_mu` at `13098` before taking
`inference_mu` at `13100`; `kv_cache_reload_repeat` unlocks at `13079`.
`kv_cache_store_current` takes `tool_mu` at `12974` and releases at `12993`
before any store. This invariant is what makes the design in §2 legal and it must
be preserved.

### 1.2 What `inference_mu` protects

It is the model-executor lock. The decode worker takes it once per batch
(`15618`), prefill takes it once per scheduling slice (`14522`), and every
session mutation (`ds4_session_sync`, `ds4_session_eval`, `ds4_session_rewind`,
`ds4_session_invalidate`, `ds4_session_rewrite_from_common`,
`ds4_session_set_vision_identities`) is bracketed by it. Concrete state:

* the whole `ds4_session` graph state (Metal/CUDA tensors) of **every** slot —
  it is one global lock, not per slot;
* `s->image_cache` (`11108` comment: "Protected by inference_mu");
* the prefill-hold counters `prefill_hold_*` (`11129-11133`, by construction —
  `server_prefill_leave` says so at `14527-14531`).

It does **not** protect the KV store index (`kc->entry`, `text_refs`,
`rel_bits`, …) — that is `kv_mu` — and it does not protect the tool map.

### 1.3 Store path: what is actually under the lock

Entry point: `kv_cache_store_live_prefix_text` (`12723`). It acquires
`inference_mu` at **12733**, `kv_mu` at **12927**, calls
`ds4_kvstore_store_live_prefix_text` at **12928**, releases `kv_mu` at
**12937** and `inference_mu` at **12938**. So the entire store is inside both.

Inside `ds4_kvstore_store_live_prefix_text` (`ds4_kvstore.c:3113`), in order:

| # | Step | Line | Locks needed | Touches session? |
|---|---|---|---|---|
| 1 | copy token prefix | 3132 | — | reads token list |
| 2 | quant/model id | 3134,3143 | — | engine, immutable |
| 3 | **reject if live < store** | 3146-3159 | — | reads token list |
| 4 | render text (tokenizer walk over N tokens) | 3168 | — | engine |
| 5 | trailer size (tool map scan) | 3180 | `tool_mu` | no |
| 6 | `existing_compatible` — open file, read header + **full text**, SHA1, `touch_file` | 3193 | — | no |
| 7 | `kv_cache_refresh` — `readdir` + open + header per file in the dir | 3222 | — | no |
| 8 | `kv_store_pick_parent` — per candidate: open, header, read text prefix, tokfp seek, **token-span payload read** | 3223 | — | reads token list |
| 9 | **`ds4_session_stage_payload[_span]`** — flush the GPU queue, `ds4_gpu_tensor_read` every tensor, `fwrite` to a `mkstemp` file in `/tmp` | 3235/3244/3248 | — | **yes, reads all GPU state** |
| 10 | size fit check | 3268 | — | no |
| 11 | `ds4_kvstore_evict` — `readdir` + header per file, lineage matrix, text comparisons, `unlink` | 3296 | — | no (`(void)live;` at 2202) |
| 12 | write header + text + **copy the staged file** + trailer + `fflush` + `rename` | 3302-3384 | — | no |

So: **the store already stages under the lock, but staging is not a memory
snapshot — it is a full second write of the payload to `/tmp`.** Steps 6–8 and
11–12 need no session access at all. Step 9 is the only step that must see
quiescent GPU state, and it is genuinely irreducible without an engine change.

Two consequences that matter for the design:

1. The `save=` metric the handoff quotes is **only step 12**. The timer starts
   at `kvstore.c:3301`, immediately before `fopen(tmp,"wb")` at 3302, and stops
   after `rename` at 3388. Steps 1-11 are *unmeasured* and are already inside
   the same hold. The 258 ms p50 is therefore a **lower bound** on the store's
   under-lock cost, not the total. This is the single most important correction
   to the brief's framing.
2. What a deferred store may hold on to is: the rendered `text` (heap, owned by
   the store), the copied `store_tokens`, the staged payload **file path**, and
   the scalars `{model_id, quant_bits, ctx_size, live_len, store_len,
   supports_delta}`. No session pointer, no engine pointer beyond immutable
   queries. That is a complete and sufficient hand-off.

### 1.4 Load path: what is read vs what is mutated

Entry point: `kv_cache_try_load_text` (`13185`); it acquires `inference_mu` at
**13198**, `kv_mu` at **13205**, calls `ds4_kvstore_try_load_text`
(`ds4_kvstore.c:3949`) at **13206**, releases at 13209/13210. The vision variant
(`13291`) is the same shape with extra session mutation after the call
(`13316-13488`), and it keeps `inference_mu` until `13488` deliberately
(comment at 13483: "inference_mu still held: the session is reset here, not
after unlocking, so no other worker can observe the unverified load").

Inside `ds4_kvstore_try_load_text` → `kv_cache_try_load_one`
(`ds4_kvstore.c:3632`):

| # | Step | Line | Touches session? |
|---|---|---|---|
| 1 | `kv_cache_refresh` — `readdir` + open + header per file | 3966 | no |
| 2 | candidate scan: SHA1 of the prompt prefix per candidate | 3979 (`kv_cache_find_text_prefix_skip`) | no |
| 3 | open, header, **read text**, SHA1, byte-prefix test | 3644-3684 | no |
| 4 | tokenizer-fingerprint seek/read | 3710 | engine, immutable |
| 5 | **chain walk**: per ancestor, open + header + read text prefix + tokfp seek | 3736-3821 | no |
| 6 | **payload load** — `ds4_session_load_payload_span` → `ds4_gpu_synchronize` + `ds4_gpu_tensor_write` per tensor | 3831/3856 | **yes — mutates the whole graph** |
| 7 | token-count check, `ds4_session_invalidate` + `unlink` on mismatch | 3872-3906 | **yes** |
| 8 | trailer `hooks->load` (tool map install → `tool_mu`) | 3883 | no |
| 9 | `touch_file` (rewrites the header in place) | 3927 | no |

Split: **steps 1-5 and 8-9 are pure I/O + index work. Only steps 6-7 mutate the
session.** And step 6 is the only part that needs GPU quiescence.

Unlike the store, the `load=` metric **is** the whole operation (timer starts at
`3643`, the top of `kv_cache_try_load_one`), so 821 ms p50 / 2501 ms p90 is the
real under-lock cost of one successful load. Deep chains dominate: avg 12.7
links, each costing an open + a header read + a *text* read (up to ~500 KB) +
a tokfp seek.

Loads are rare (81) but 3× longer per event than stores; stores are ~10× more
frequent (792). Aggregate under-lock time is therefore **store-dominated**:
792 × ~0.5 s ≈ 400 s vs 81 × ~1.5 s ≈ 120 s (**ESTIMATE**: store total assumes
the unmeasured steps 1-11 roughly match step 12; the load total assumes discards
cost about the same as hits, which they do not measure).

### 1.5 The re-entrancy nobody has written down: the store runs *inside* the prefill hold

This is the most important structural fact I found and it is not in the handoff.

`server_session_sync` (`14581`) brackets the engine call with
`server_prefill_enter` (`14609`) / `server_prefill_leave` (`14612`). The engine
invokes the progress callback from inside `ds4_session_sync` (`ds4.c:80870`,
`80907`, …). `server_progress_cb` (`14931`) calls
`kv_cache_maybe_store_continued` at **14969** and **15010** — i.e. the
continued-store path executes **while `inference_mu` is already held by the
prefill hold**, re-entering it through the recursive mutex.

Consequences:

1. A continued store's whole duration (steps 1-12 above, including the GPU
   flush inside `ds4_session_stage_payload`) is charged to the *prefill hold*,
   so `cbc4db6`'s `prefill hold` lines will report it. That is the validation
   hook for §5, and it means holds ≥ 2 s are the right observability to watch.
2. Any design that "unlocks `inference_mu` before the write" must **not** do so
   when entered re-entrantly — releasing a recursive mutex once inside a hold
   leaves the outer frame owning it, so the unlock is useless *and*, if made
   asymmetric, would leak the mutex. The guard must be explicit and balanced.

The decode-loop store (`16389`), the post-prefill store (`16196`), the
post-turn store (`17358`), the evict store (`15965`) and the shutdown store
(`18975`) are all called *outside* a hold, so they are the ones that can
actually benefit.

### 1.6 Inversion risk if we release and re-take

The hazard is precise and easy to get wrong:

* Today the order is `inference_mu` → `kv_mu`.
* The load path takes `inference_mu` then `kv_mu` (`13198`, `13205`).
* So if a store released `inference_mu` **while still holding `kv_mu`** and then
  re-acquired `inference_mu` before releasing `kv_mu`, the order would become
  `kv_mu` → `inference_mu` — a textbook ABBA deadlock against every load.

The design in §2 avoids it by never re-acquiring: the store releases
`inference_mu`, does its I/O under `kv_mu`, releases `kv_mu`, and **never takes
`inference_mu` again** (nothing after the write needs it — the post-store slot
fields at `12942-12952` are already written outside the lock today).

For the load the same rule applies in the other direction: prepare runs under
`kv_mu` **only**, then apply takes `inference_mu` *then* `kv_mu`. Never the
reverse.

---

## 2. The design

### 2.1 Principles

1. **Snapshot under the lock, bytes after it.** The lock buys a consistent
   point-in-time view of the GPU state; it is not needed to put that view on
   disk.
2. **`kv_mu` remains the format lock.** It is what makes sha-keyed files,
   in-place header touches, trailer rewrites and unlink-based GC safe. It is
   never dropped in the middle of a store or a load in this design.
3. **Re-validate, never trust the plan.** Anything staged (a payload, a chain of
   fds) is re-checked under the lock before it is used, and a failed re-check
   degrades to a retryable miss — the same contract the loader already
   implements ("delta chain file changed mid-load", `ds4_kvstore.c:3851`).
4. **One flag per step, default OFF, reverted by restart.** The operator's
   constraint is no rebuild to roll back, so each step is a `getenv` checked at
   runtime, not a compile-time variant.

### 2.2 Step S1 — split the store into stage / commit

New public API in `ds4_kvstore.h`:

```c
/* Everything a store needs that requires the model executor to be quiescent.
 * After this returns successfully, no session or engine pointer is retained:
 * the payload is a file, the text is heap, the scalars are copies. */
typedef struct {
    ds4_kvstore *kc;
    char  *text;              size_t text_len;
    ds4_tokens store_tokens;
    int    model_id, quant_bits, ctx_size, live_len, store_len;
    bool   supports_delta;
    ds4_session_payload_file staged;
    const char *reason;
    uint8_t cache_text_ext;
    const char *cache_text_key;
    const ds4_kvstore_trailer_hooks *hooks;   /* caller-owned; must outlive commit */
    const char *evict_protect_text;  size_t evict_protect_len;
} ds4_kvstore_staged_store;

bool ds4_kvstore_stage_store(/* same arg list as store_live_prefix_text */,
                             ds4_kvstore_staged_store *out,
                             char *err, size_t err_len);
bool ds4_kvstore_commit_store(ds4_kvstore_staged_store *st,
                              char *err, size_t err_len);
void ds4_kvstore_staged_store_free(ds4_kvstore_staged_store *st);
```

`ds4_kvstore_store_live_prefix_text` becomes `stage` + `commit` in one call, so
`ds4_agent.c` and the existing unit tests are untouched.

The server wrapper (`kv_cache_store_live_prefix_text`, `12723`) becomes:

```c
pthread_mutex_lock(&s->inference_mu);
/* vision key/record preparation: unchanged, still needs the session */
ds4_kvstore_staged_store st = {0};
bool staged = ds4_kvstore_stage_store(&s->kv, s->engine, slot->session, ... , &st, ...);
pthread_mutex_lock(&s->kv_mu);
/* the only behavioural change: drop the executor lock for the I/O, but only
 * when we own it outright -- a recursive prefill hold must keep it. */
const bool release = staged && s->prefill_hold_t0 <= 0.0;
if (release) pthread_mutex_unlock(&s->inference_mu);
bool ok = staged && ds4_kvstore_commit_store(&st, err, sizeof err);
pthread_mutex_unlock(&s->kv_mu);
if (staged && !release) pthread_mutex_unlock(&s->inference_mu);
if (!staged) pthread_mutex_unlock(&s->inference_mu);
ds4_kvstore_staged_store_free(&st);
```

(the two `if (!staged)`/`!release` branches collapse in real code; shown
separately to make the lock-count accounting explicit). `s->prefill_hold_t0` is
written in `server_prefill_enter` (`14500`, `14523`) and cleared in
`server_prefill_leave` (`14536`), always under `inference_mu`, so reading it
here is consistent.

Under-lock before: steps 1-12. Under-lock after: steps 1-9 (render, trailer
size, stage). Off-lock: `existing_compatible`, `refresh`, `pick_parent`,
`evict`, the whole final write, `fflush`, `rename`. **ESTIMATE**: that is
somewhat more than half the store's under-lock time, because the stage write to
`/tmp` and the final write to the blade are comparable in bytes (both are the
payload) while steps 6-8/11 add a directory scan and several text reads.

### 2.3 Step L1 — split the load into prepare / apply

```c
typedef struct {
    char    *path;
    FILE    *fp;              /* held open: unlink after prepare cannot hurt us */
    uint32_t text_bytes, tokens, delta_from;
    uint64_t payload_bytes;
    ds4_kvstore_entry hdr;
} ds4_kvstore_load_node;

typedef struct {
    int n;
    ds4_kvstore_load_node node[64];   /* same cap as the existing chain[64] */
    ds4_kvstore_entry hdr;
    ds4_kvstore_load_result result;
} ds4_kvstore_load_plan;

/* steps 1-5 + 8: refresh, select, verify the whole chain, keep every fd open.
 * Requires kc (kv_mu) but no session. On failure nothing is open. */
bool ds4_kvstore_prepare_load(ds4_kvstore *kc, ds4_engine *engine,
                              const char *prompt_text,
                              const ds4_kvstore_trailer_hooks *hooks,
                              bool responses_protocol, bool want_vision,
                              const int *skip, int skip_len,
                              ds4_kvstore_load_plan *plan, char *err, size_t err_len);
/* steps 6-7 + 9: re-check headers, read the payloads, apply the trailer,
 * touch. Requires session (inference_mu) + kc (kv_mu). */
int  ds4_kvstore_apply_load(ds4_engine *engine, ds4_session *session,
                            const char *prompt_text, ds4_tokens *effective_prompt,
                            ds4_kvstore_load_plan *plan, bool responses_protocol);
void ds4_kvstore_load_plan_free(ds4_kvstore_load_plan *plan);
```

The caller becomes:

```c
/* outside the executor lock */
pthread_mutex_lock(&s->kv_mu);
bool planned = ds4_kvstore_prepare_load(&s->kv, ..., &plan, ...);
pthread_mutex_unlock(&s->kv_mu);
if (!planned) return 0;
/* under it: only the payload->GPU copy and the session swap */
pthread_mutex_lock(&s->inference_mu);
pthread_mutex_lock(&s->kv_mu);
int loaded = ds4_kvstore_apply_load(..., &plan);
pthread_mutex_unlock(&s->kv_mu);
pthread_mutex_unlock(&s->inference_mu);
ds4_kvstore_load_plan_free(&plan);
```

**Holding the fds is the load's crash/race answer.** Files are
content-addressed and their text and payload sections are immutable — the
header comment at `ds4_kvstore.h:123-127` states this and it is enforced by the
only in-place writer (`ds4_kvstore_rewrite_trailer`, `ds4_kvstore.c:3035`,
which touches only the header and the trailer). A concurrent eviction may
`unlink` the path; POSIX keeps the inode alive while the fd is open, so the
apply step still reads the verified bytes. `apply` re-reads each header and
checks `tokens`/`payload_bytes` before touching the session (the pattern
already at `3846-3849`), so a *rewritten* file (different sha, same path is
impossible; same sha, different payload size is the only case) fails closed
into the existing retryable-miss path.

Note what does **not** change: `inference_mu` is still held for the whole
vision-load validation block (`13316-13488`) and for the session swap. The
brief's instinct — "the mutation must stay under the lock" — is right, and this
design keeps it.

### 2.4 Step S2 — async store worker (only if S1 is not enough)

S1 already yields the whole off-lock win *except* the caller still blocks for
it. S2 adds a single-threaded queue of `ds4_kvstore_staged_store` and makes the
store submit-and-return.

Additional requirements S2 must satisfy:

* **Drain on shutdown** before `server_close_resources` (`18302`) — the shutdown
  store at `18975` must complete, otherwise the last frontier is lost.
* **Bound the queue** (I suggest 4) and unlink the staged file of a dropped
  item; the staged payloads live in `/tmp` and a 4-deep queue is ~2.4 GB of
  internal SSD (**ESTIMATE**: 600 MiB typical chained payload × 4).
* **`hooks` must be heap-copied.** Today `hooks` points at a stack
  `kv_trailer_ctx` (`12737`, `13195`); an async item outlives that frame.
  `kv_trailer_ctx` embeds `kv_vision_record recs[KV_VISION_MAX_RECORDS]` and a
  `const char *` to the request; both must be deep-copied or the payload must be
  serialized under the lock.
* **Success feedback changes shape.** `kv_cache_slot_note_store` (`13036`) and
  `slot->durable_frontier_tokens`/`durable_conv` (`12942-12952`) currently mean
  "on disk". With S2 they must mean "submitted"; the multimodal guard at
  `15917` (`newer_unsaved`) then becomes optimistic. That is a *policy* change
  and is the main reason I do not put S2 first.

### 2.5 Staleness and durability semantics

| Question | Answer under this design |
|---|---|
| Can the session mutate while bytes are written? | Yes, and that is the point. The staged payload is an immutable point-in-time snapshot; the text was rendered from the same token prefix; the header records that prefix's length. The file is internally consistent no matter what the live frontier does next. |
| Can the snapshot be stale relative to the live frontier? | Yes, by construction, by exactly the number of tokens decoded during the write. That is not a defect: a checkpoint is a frontier marker, and the loader already treats "stale by N tokens" as a prefix hit plus N tokens of prefill. |
| Is a stale snapshot worse than a torn one? | No, and the design never produces a torn one: it is still written to a unique temp file and `rename`d, so a reader sees old-file or new-file, never half. |
| Crash consistency | Unchanged from today: temp + `rename`, no `fsync`. A crash can leave a renamed-but-unflushed file; the loader's SHA1-of-text check (`ds4_kvstore.c:3672-3676`) and payload-size check (`67231`) fail it closed, and `kv_cache_sweep_orphan_temps` (`2349`) reaps temps older than an hour and finals whose header no longer parses. **The design adds no new crash window** because the temp name stays `%s.tmp.%ld` and `kv_mu` still serialises writers, so two in-flight writers can never share a temp name. |
| Ordering | Two stores of the same text still serialise on `kv_mu`, so the second one's `existing_compatible` sees the first one's file. No lost update. |

---

## 3. What actually breaks if the executor lock is dropped

Nothing in the file format. The format's mutual exclusion is `kv_mu`, not
`inference_mu`:

* **sha-keyed, content-addressed names.** The name is the SHA-1 of the rendered
  text (`ds4_kvstore.c:3189`), so two stores of the same text race to the same
  name and are resolved by `kv_mu` (one wins `rename`, the other's
  `existing_compatible` refreshes the trailer).
* **temp + `rename`.** `"%s.tmp.%ld"` (`3299`) includes the pid; `rename` at
  `3384` is the commit point. Under `kv_mu` only one writer per path exists, so
  the pid suffix is sufficient. *If a later step ever drops `kv_mu` across the
  write, the temp name must gain a per-item sequence number.*
* **In-place header rewrite.** `ds4_kvstore_touch_file` (`744`) does
  `fopen(path,"r+b")` + `fwrite` of the header, with no temp. Under
  `inference_mu` **alone** this would be a read-modify-write race; under `kv_mu`
  it is serialised. Same for `ds4_kvstore_rewrite_trailer` (`3035`), which
  truncates and rewrites the trailer and then the header, and unlinks the file
  if any step fails (`3098-3109`).
* **GC/eviction.** `kv_cache_unlink_entry` (`1937`) unlinks and decrements the
  parent's `children` count in memory; `kv_cache_retire_leaf` (`2030`) relies on
  index stability across a whole pass; `kv_cache_refresh` (`704`) rebuilds
  `kc->entry` wholesale. All of that is `kv_mu`-protected. The **only** thing
  `inference_mu` adds today is that a load cannot be concurrent with a store —
  and that is a property the load's fd retention makes unnecessary.

So the answer to "does eviction/GC already assume the exclusion you would
remove" is: **it assumes `kv_mu`'s exclusion, which this design keeps.** It does
not assume anything about `inference_mu`.

One genuine dependency I did find, and it is a real (pre-existing) durability
hole rather than a locking one — see §4.3.

---

## 4. Three verified defects on this path that change the cost model

These are not part of the lock design; they determine how much of the *need*
for the lock can be removed instead. Each is measured, not inferred.

### 4.1 `save=` measures only the last 30 % of the store

Covered in §1.3. Important because it means "move the `fwrite` off the lock"
(the handoff's §6 sketch) captures only the measured 258 ms, while steps 6-8 and
11 are also I/O under the same lock and are unmeasured. **S1 moves all of them.**

### 4.2 `kv_cache_refresh` runs 2× per store and 1× per load, and it is O(files)

`ds4_kvstore.c:3222` (pick_parent), `2205` (evict, reached from `3296`), `3498`
(after a continued store), `3598` (public lookup), `3966` (load). Each is
`readdir` + `open` + `fread` of a 48–120 byte header **per file**, and the
directory is allowed to hold `cold_max = 30000` files (`ds4_kvstore.c:37`).
At the observed 149+ files this is tens of milliseconds; at 30k it would be
seconds. S1 moves these off the executor lock but does not reduce them; §6
ranks reducing them separately.

### 4.3 **41 of 81 loads read a full payload and throw it away** (`kvstore.c:3894-3906`)

The store's header records `store_tokens.len`, but the staged payload is the
**whole live session**: `qwen4_session_save_payload_span` takes
`rows = s->checkpoint.len` (`ds4.c:66475`). When a continued store snaps down to
a grid boundary below the live frontier (the documented snap-down at
`ds4_kvstore.c:2656-2658`), header and payload disagree, and the loader's check
`loaded_tokens->len == hdr.tokens` (`ds4_kvstore.c:3872`) fails, so it
`unlink`s the file and falls back (`3895-3905`).

Measured on the 1117 log:

```
184 continued stores and 3 cold stores were written with trimmed != 0
41 "discarded corrupt text-prefix payload" events
41 of 41 discarded files were last written by a store with trimmed != 0
 0 of 41 by a store with trimmed == 0
```

Worked example from the log (`e8877a36…`): stored at line 528 with
`tokens=81920 trimmed=705`, discarded at 1203, re-stored at 1214 with
`trimmed=0` (live == 81920 after the rebuild), and only then hit at 1644.

So each such rung costs: one full payload write, one full payload read, one
unlink, a fallback to a shallower anchor, and a re-prefill of the difference.
That is the single largest *avoidable* I/O on the path, and it is worth more
than any lock work per unit of risk.

### 4.4 The evict storm is real and exactly as stated

93 slot evictions dropped 17,463,570 tokens; 66 of the 93 are immediately
followed by a store (`reason=evict`, 419 MiB avg) and a load in the same
request, inside the 2-try loop at `ds4_server.c:15968-15981`. That is the
back-to-back pattern the handoff describes, and it is the worst case for the
lock: ~0.3 s store + ~0.8 s load, twice.

---

## 5. Gated delivery sequence

Each step is independent, ships behind its own `getenv`, defaults OFF, and is
reverted by restarting with the variable unset. Nothing here changes on-disk
format, so no step can strand checkpoints.

| # | Step | Gate (default OFF) | Under-lock removed | Abort criterion |
|---|---|---|---|---|
| **1** | **S1 store split** (§2.2) | `DS4_KV_IO_OFFLOCK_STORE=1` | `existing_compatible`, `refresh`, `pick_parent`, `evict`, final write, `fflush`, `rename` | any new `kv cache store failed`, any `kv cache discarded` for a file written by this build, or store count/1k tokens down >2 % |
| **2** | **Trim guard** (§4.3) | `DS4_KV_TRIM_GUARD=1` | — (removes work instead) | `trimmed != 0` stores no longer occur but `kv cache hit text` rate falls >1 pp |
| **3** | **L1 load prepare/apply** (§2.3) | `DS4_KV_IO_OFFLOCK_LOAD=1` | refresh, candidate scan, text reads, chain walk | any `delta chain file changed mid-load`, `payload does not match its header`, or load p50 *worse* than baseline |
| **4** | **S2 async worker** (§2.4) | `DS4_KV_IO_ASYNC=1` | everything, including the stage | queue depth hits the bound more than once an hour, `/tmp` growth, or any shutdown store missing |

### Why S1 first, and not the load

The brief asks explicitly whether the load matters more. Per event it does
(821 ms p50 / 2501 p90 vs 258/501 measured, and the store figure is a
*lower bound*). In aggregate the store wins on every axis that matters:

* **frequency**: 792 stores vs 81 loads over the log — 9.8×;
* **risk**: S1 touches one function's lock lifetime and keeps the payload
  staging (the part that must see quiescent GPU state) exactly where it is. L1
  restructures the loader's candidate/fallback loop, which is the code path the
  ADR calls out as having produced "corrupt files on load" and "a 94 s prefill"
  when it was wrong;
* **reversibility**: S1's failure mode is a late checkpoint. L1's failure mode
  is a wrong session — the thing the fork's rules say must never happen.

So: **S1 is the first shippable step and captures most of the benefit.** L1 is
step 3, after S1's telemetry has proven the release/re-acquire accounting is
sound on real traffic. The trim guard (step 2) is placed between them because it
is small, provably safe, and it removes half the load events that L1 would
otherwise optimise.

### Smallest first step with most of the benefit

Step 1 alone, and specifically **not** just deferring the `fwrite`. Deferring
only `fwrite`+`fflush`+`rename` would capture the measured 258 ms p50 and
nothing else. Deferring everything after staging captures the measured 258 ms
*plus* the unmeasured `existing_compatible` + 2× `refresh` + `pick_parent` +
`evict`. Same code change, same review surface, strictly more benefit. That is
why the API is split at the staging boundary rather than at the write.

---

## 6. Verification plan

### 6.1 Unit tests (`make test-server-logic`, model-free, safe alongside the engine)

The target is `Makefile:1484`; the binary links `ds4_server_test.o ds4_kvstore.o
rax.o` and is built with `-DDS4_SERVER_TEST`, so no model and no Metal device are
needed. Tests are registered in `ds4_server_unit_tests_run()` around line 28990.

1. **`test_kv_store_stage_rejects_short_prefix`** — `stage_store` with
   `live_len < store_len`, or `store_len < min_tokens`, returns false and leaves
   `out->staged.path == NULL` (nothing to leak).
2. **`test_kv_store_stage_commit_owns_temp_file`** — point `staged.path` at a
   `mkstemp` file, call `staged_store_free`, assert the file is gone. Pins the
   one resource the split introduces.
3. **`test_kv_store_commit_requires_stage`** — `commit_store` on a zeroed handle
   fails without touching the filesystem.
4. **`test_kv_store_trim_mismatch_refused`** (step 2) — with the guard on, a
   store whose `store_len < live_len` is either refused or written with a header
   that matches the payload; assert the written file's
   `hdr.tokens == payload_rows` by reading it back with
   `ds4_kvstore_read_header` **and** the payload's own 13-u32 header, which is
   model-free arithmetic (`DS4_SESSION_PAYLOAD_U32_FIELDS`).
5. **`test_kv_io_offlock_release_is_balanced`** — add a test-only counter
   `kv_io_offlock_releases` incremented where S1 drops the lock. Drive
   `kv_cache_store_live_prefix_text` twice: once with `s.prefill_hold_t0 = 0`
   (expect counter +1) and once with `prefill_hold_t0 > 0` (expect +0). This is
   the only mechanical test of the re-entrancy guard from §1.5 and it does not
   need a session, because the guard is evaluated before the store runs.
6. **`test_load_plan_orders_chain_root_first`** — build three stub `.kv` files
   with `test_kv_stub_file` (`25347`) forming a v3 chain, call
   `ds4_kvstore_prepare_load`, assert `plan->n == 3` and
   `node[0].tokens < node[1].tokens < node[2].tokens`. Model-free: the existing
   `test_kv_cache_lookup_uses_longest_text_prefix` already builds files this
   way.
7. **`test_load_plan_fd_survives_unlink`** — prepare, `unlink` one node's path,
   then `fread` from the held fd and assert the bytes are the text that was
   verified. This pins the exact POSIX property L1 relies on; without it the
   design is folklore.
8. Keep `test_prefill_hold_accounting` (`19046`, from `da66d6c`) as the
   regression that the hold counters still mean what §5 assumes.

All eight are model-free and run in the same suite. None requires a running
engine, so they are safe to run while PID 64188 serves.

### 6.2 Live A/B using the telemetry that shipped

`cbc4db6` gives `server_prefill_leave` (`14532`) a hold counter and a WARNING at
`DS4_PREFILL_HOLD_WARN_SEC` (default 2.0 s, `18771-18777`) carrying slot,
`tokens=a..b`, `remaining`, `quantum`, and lifetime `holds/total/max`.

Because §1.5 shows the store runs inside the hold, **these lines are the direct
instrument for this fix** — no new instrumentation is needed for step 1.

Design the A/B as a before/after on equal traffic, not a simultaneous A/B (the
box has one engine and one 512 GiB-blade KV dir):

* **Phase 0 (baseline, gate OFF).** Let the current engine run until it has
  served ≥ 30k decode samples (the 1117 log has 46,536 over ~2 days; a matched
  window is ~24 h at the same request mix). Record: `prefill hold` lines/1k
  decode samples, hold `total`/`max` deltas, dips < 10 t/s per 1k, store/load
  p50/p90/p99, store count, hit count, dspark accept rate, RSS.
* **Phase 1 (gate ON).** Same duration and, as far as possible, the same client
  workload.

Win thresholds (all relative to Phase 0):

| Metric | Must move | Threshold |
|---|---|---|
| dips < 10 t/s per 1k decode samples | down | **≥ 50 %** (101/46536 = 2.17 → ≤ 1.08) |
| of those, the KV-adjacent subset | down | **≥ 70 %** |
| of those, the non-KV-adjacent subset | flat | within ±20 % |
| `prefill hold` lines/1k where `remaining > 0` | down | ≥ 50 % |
| `prefill_hold_max_sec` | down | max ≤ 2.5 s |

Guardrails that must **not** regress:

| Metric | Tolerance |
|---|---|
| decode t/s, median over samples | −2 % |
| dspark accept rate | ±1 pp |
| `kv cache stored` count per 1k generated tokens | ±2 % (a drop means lost checkpoints) |
| `kv cache hit text` count | ±1 % |
| store `save=` p50/p90 | +10 % (the I/O must not get slower) |
| load `load=` p50/p90 | +10 % |
| RSS (`ps -o rss=`) | +500 MiB (staged payloads are files, not RAM) |
| `kv cache discarded` / `store failed` lines | **zero new** |

### 6.3 Distinguishing "fixed the KV cause" from "fixed nothing"

This is the part that a single aggregate number cannot answer, so the test is
built on the decomposition:

* The 101 dips split into a **KV-adjacent** set (61–69 by the fixed-window
  measure) and a **non-KV-adjacent** set (32–40). Both are computed by the same
  script before and after.
* If the fix works, the KV-adjacent set collapses and the non-KV set does not
  move. Both conditions are required: a KV-adjacent drop accompanied by an equal
  non-KV drop means the traffic changed (fewer long requests, fewer evictions),
  not that the lock was the cause.
* If the KV-adjacent set is unchanged, the fix did nothing — go back to §1.3
  and check which of steps 1-12 is still under the lock (the likely culprit:
  the release guard never fired because every store came from the progress
  callback, in which case `holds` per store is the diagnostic).
* The 32–40 unexplained dips are the **control group**. Reporting them
  unchanged is not a failure; reporting them *improved* is a red flag that the
  comparison window is not matched.

A cheap leading indicator, available within minutes rather than days:
`prefill hold` lines whose `remaining > 0` (i.e. a hold that did not end on the
prompt boundary) rose during the store, plus `holds`/`total` deltas. If S1 works,
`total` grows more slowly per `hold` because the store's I/O left the hold.

---

## 7. Ranked alternatives

Ranked by benefit ÷ risk, with the reasoning that decided the order.

### 1. S1 — stage under the lock, commit after release (recommended first)

*Benefit:* removes steps 6-8 and 11-12 from the executor hold — the measured
`save=` tail (258 ms p50, 500 ms p90, 1156 ms max) **plus** the unmeasured
`refresh`/`pick_parent`/`evict` scans. Applies to all 792 stores, including the
66 evict stores that are immediately followed by a load.
*Risk:* low. One function's lock lifetime; the payload staging (the part that
needs quiescence) is unchanged; `kv_mu` still serialises writers and readers.
*Why first:* highest frequency × lowest review surface. See §5 for why the split
belongs at the staging boundary and not at the `fwrite`.

### 2. Trim guard / stop writing unloadable checkpoints (§4.3)

*Benefit:* eliminates 41 full payload reads + 41 full payload writes + 41
unlinks + the consequent re-prefill, with no locking change at all. On this log
that is literally **half the load events**.
*Risk:* very low if done as "refuse the store when `store_len < live_len`" — it
can only lose a rung that was already dead. Slightly more work if done properly
(truncate the payload to `store_len`, which needs an engine-side row cap).
*Why second:* it is the cheapest real win on the path and it makes step 3's
measurement cleaner. It is also the honest answer to "did you consider just
needing less I/O?" — yes, and it is worth more per unit of risk than L1.

### 3. L1 — prepare the load outside `inference_mu` (§2.3)

*Benefit:* the largest per-event win (821 ms p50, 2501 ms p90, 4893 ms max), and
the deep chains (avg 12.7, max 28 links) mean the verification half is a large
fraction of it.
*Risk:* medium-high. Restructures the candidate/fallback loop that the ADR
records as having produced corrupt restores and a 94 s prefill when it was
wrong. Requires the fd-retention argument (§2.3) to be right, and requires the
apply step's re-validation to fail closed into the existing retryable-miss path.
*Why third, not first:* 9.8× rarer than the store, and its failure mode is wrong
KV rather than a late checkpoint.

### 4. S2 — dedicated KV I/O worker with a queue

*Benefit:* removes the caller's blocking time as well as the hold; makes the
store genuinely fire-and-forget so a decode never waits for a checkpoint at all.
*Risk:* highest of the four. Changes the meaning of "stored" (success feedback,
`durable_frontier_tokens`, the multimodal `newer_unsaved` guard at `15917`),
needs `hooks`/`trailer_ctx` deep copies, needs a bounded queue with `/tmp`
accounting, and needs a shutdown drain. It also **does not subsume S1** — the
staging still has to happen under the executor lock, so S2 without S1 would move
only the same bytes S1 already moves.
*Verdict:* worth doing only after S1+L1 have been measured, and only if the
caller-blocking time (not the hold) is shown to matter.

### 5. Reduce the evict storm (policy, not locking)

*Benefit:* 93 evictions dropping 17.46M tokens, 66 of them followed by
store+load in the same request. The back-to-back pair is the worst case for the
lock, so avoiding it removes the tail rather than shifting it.
*Risk:* medium — it is a routing/retention policy change and the ADR already
records two failed attempts at this class of change (retire-grace, divergence
anchors) that needed live iteration.
*Options worth measuring:* `DS4_KVSTORE_WIDE_STEP_ABOVE_TOKENS` already halves
anchor writes above 48k; `max_conversations` is 0 (unlimited) on this lane;
`retire_grace_seconds` is 3600. The cheapest experiment is not a code change:
raise `KV_MAX_DIVERGENCE_ANCHORS` from 8 and measure whether the 41
divergence-anchor stores per rebuild episode (log 1116 11:46: `divergence anchor
stored` fired once per 512-token chunk for 8 consecutive chunks) stop churning.
**UNVERIFIED** whether that reduces loads; it is a hypothesis, and it is the one
thing in this document I would want measured before implementing.

### 6. Smaller `kv_cache_refresh` cost (complementary, not an alternative)

Cache the directory scan by mtime, or keep the index incrementally (the store
already knows the file it just wrote). Would cut the 2×-per-store full-directory
scan. Independent of the lock work and useful at 30k files; not worth blocking
on at the current 149.

### Not recommended

* **Serialize-prefill** — premise refuted (1.15×), and the handoff's §5 already
  rejects it for good reasons.
* **Raising `--mixed-prefill-quantum`** — it is the executor *holdback* unit;
  2048 doubles the worst-case decoder wait. Reverted to 512 in `1f9f3fa`.
* **Making `inference_mu` finer-grained (per-slot)** — a much larger change than
  any of the above, and the GPU queue is global (`ds4_gpu_synchronize` is
  process-wide, `ds4_metal.m:12384`), so per-slot locks would not actually
  decouple the expensive part.

---

## 8. What I could not verify

* **The under-lock time of a store.** `save=` covers only the final write
  (`kvstore.c:3301`). Steps 1-11 are unmeasured. My "slightly more than half"
  split in §2.2 is an **ESTIMATE** from byte counts, not a measurement. The
  `prefill hold` telemetry from `cbc4db6` is what will measure it, and the
  baseline run in §6.2 is required before any claim about the split's magnitude.
* **The exact 66/101 of the handoff.** I measured 61/101 and 69/101 depending on
  the window's sign convention (§0). I could not reproduce 66 exactly.
* **Whether the 41 trim-mismatched files are *always* unloadable.** I verified
  the mechanism in code (`ds4.c:66475` sets payload `rows = checkpoint.len`;
  `ds4_kvstore.c:3872` compares it to `hdr.tokens`) and the 41/41 correlation in
  the log, but I did not run a live reproduction (that would need the engine
  restarted). The correlation is strong enough to act on; the mechanism reading
  is unambiguous; the *reproduction* is not done.
* **Whether `divergence anchor` churn drives the load count.** §7.5 — hypothesis
  only.
* **Capture-lane interaction.** With `DS4_TRAIN_DUMP` armed,
  `ds4_session_load_payload_span` calls `ds4_td_segment_end` (`ds4.c:66840`),
  which calls `ds4_td_flush_ring` and therefore does I/O under the lock
  (`ds4.c:78281-78290`). The design does not address that; the capture lane
  should not be A/B'd against the serving lane.
* **Whether the 2-try reload loop at `ds4_server.c:15968` is ever exercised.**
  I saw 41 discards but did not instrument retries. The loop is the worst case
  in §4.4 by construction, not by measurement.

---

## 9. Appendix — reproducible commands

```sh
# store/load latency + sizes  (all numbers in §0)
awk '/kv cache stored /{n++;for(i=1;i<=NF;i++){
        if($i~/^save=/){v=$i;sub(/save=/,"",v);s[n]=v+0}
        if($i~/^reason=/){r=$i;sub(/reason=/,"",r);c[r]++}
        if($i~/^size=/){z=$i;sub(/size=/,"",z);b[r]+=z+0}}}
     /kv cache hit text/{m++;for(i=1;i<=NF;i++) if($i~/^load=/){v=$i;sub(/load=/,"",v);l[m]=v+0}}
     END{print n,m; for(x in c) printf "%s %d %.1f\n",x,c[x],b[x]/c[x]}' \
  log/ds4-qwen.log.20261117-094911

# 93 evictions / 17.46M tokens / 66 back-to-back
grep -c "request reuses nothing; evicting checkpoint" log/ds4-qwen.log.20261117-094911
awk '/evicting checkpoint/{for(i=1;i<=NF;i++) if($i~/^\([0-9]+$/){t=$i;sub(/\(/,"",t);s+=t}}
     END{print s}' log/ds4-qwen.log.20261117-094911

# the trim-mismatch correlation (§4.3): 41/41
#   (script in this session's transcript; joins "kv cache stored … sha=XXXX" to the
#    following "discarded corrupt text-prefix payload …/XXXX….kv")

# zero prefill-hold lines in this log — the baseline must be re-collected
grep -c "prefill hold" log/ds4-qwen.log.20261117-094911   # -> 0
```

Repo state at the time of writing: `git status -s` clean; `ds4_server_test` and
`tests/kv_policy_harness` present as ignored build artifacts (as `AGENTS.md`
expects).
