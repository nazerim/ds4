# IMPLEMENTATION PLAN — KV cache: keep multiple checkpoints, conversation-scoped eviction

Build order: **B (kvstore core) → A (header) → C (server wiring) → D (tests)**. Concurrency = 1.
No code written yet — this is the full scaffold + logic.

## Key structural fact (drives everything)

`ds4_kvstore` entries are NOT kept in memory across stores. `kv_cache_refresh()`
(ds4_kvstore.c:468) clears and rebuilds `kc->entry[]` by **scanning the dir and reading each
file's header** via `ds4_kvstore_read_entry_file` → `ds4_kvstore_read_header` (417). Eviction
and prefix-find therefore only see **header fields**. So `conv_id`, `bucket`, `level`, `stale`,
`model_fp` **must be persisted in the file header** — they cannot be derived at eviction time
without reading multi-hundred-MB payloads.

`model_fp` is a **per-process constant** (from the loaded weights), not per-file: it's stored
in `ds4_kvstore.model_fp` and used to filter; per-file `model_fp` only needs to distinguish
"legacy (0) vs current" — see header design.

---

## A. ds4_kvstore.h

### A1. `ds4_kvstore_entry` — add fields
```c
typedef struct {
    char sha[41]; char *path;
    uint8_t quant_bits; uint8_t model_id; uint8_t reason; uint8_t ext_flags;
    uint32_t tokens; uint32_t hits; uint32_t ctx_size;
    uint64_t created_at; uint64_t last_used;
    uint64_t payload_bytes; uint64_t text_bytes; uint64_t file_size;
    /* NEW */
    uint64_t conv_id;      /* conversation lineage key (per namespace) */
    uint64_t model_fp;     /* weight fingerprint this checkpoint was written for; 0 = legacy */
    uint32_t bucket;       /* tokens / anchor_step */
    uint8_t  level;        /* halving level of this conversation's large-middle spacing */
    uint8_t  hdr_version;  /* in-memory only: 1 or 2, from the file's version byte */
    bool     stale;        /* failed byte_prefix_match at load, same namespace */
} ds4_kvstore_entry;
```

### A2. `ds4_kvstore_options` — add knobs
```c
typedef struct {
    int min_tokens; int cold_max_tokens;
    int continued_interval_tokens; int boundary_trim_tokens; int boundary_align_tokens;
    /* NEW */
    int anchor_step;            /* bucket granularity, default 8192 */
    int small_dense_tokens;     /* keep ALL anchors <= this, default 16384 */
    int tail_anchors;           /* frontier + this many below kept dense, default 2 */
    int mid_spacing_tokens;     /* large-middle spacing at level 0, default 131072 */
    int min_anchors;            /* retire a conversation when ladder <= this, default 4 */
    int max_conversations;      /* cap distinct conv_ids; retire LRU over the cap, default 0=unlimited */
} ds4_kvstore_options;
```

### A3. `ds4_kvstore` — add field
```c
    uint64_t model_fp;   /* set once at open from the loaded weights */
```

### A4. New / changed API (prototypes)
```c
#define DS4_KVSTORE_HEADER_V2_EXTRA 24u   /* 48 base + 24 = 72 total */
#define DS4_KVSTORE_CONV_ID_HEAD_BYTES 512u

uint64_t ds4_kvstore_compute_conv_id(const char *text, size_t text_len, uint64_t model_fp);
void ds4_kvstore_mark_stale_at_load(ds4_kvstore *kc, const char *prompt_text,
                                    int model_id, int quant_bits, int ctx_size);

bool ds4_kvstore_open(ds4_kvstore *kc, const char *dir, uint64_t budget_mb,
                      bool reject_different_quant, uint64_t model_fp,
                      ds4_kvstore_options opt, const char *log_name,
                      void (*log)(void *ud, ds4_kvstore_log_type type, const char *msg),
                      void *log_ud);   /* + model_fp param */
```
`fill_header`/`read_header`/`touch_file` are file-local (static); only the header-layout constants
go in the .h.

---

## B. ds4_kvstore.c

### B1. Header layout — v1 vs v2
Keep the base 48 bytes byte-for-byte identical to today (v1) for backward compatibility:
```
base 48: [0..2]="KVC" [3]=version [4]=quant [5]=reason [6]=ext [7]=model_id
         le32 tokens@8, hits@12, ctx@16, [20]=payload_abi,
         le64 created@24, last_used@32, payload_bytes@40
```
v2 appends 24 bytes at offset 48 (total 72):
```
48..55 le64 conv_id
56..63 le64 model_fp
64..67 le32 bucket
68     u8 level
69     u8 stale (0/1)
70..71 reserved (0)
```
`DS4_KVSTORE_FIXED_HEADER` stays 48 (base). New `DS4_KVSTORE_HEADER_V2` = 24. Store always
writes v2 (version byte 2). `touch_file` **preserves the original version** (never upgrades v1
in place — that would corrupt the v1 text_bytes at offset 48).

### B2. `ds4_kvstore_fill_header` (v2-aware) — pseudocode
```c
void fill_header(uint8_t h[72], uint8_t version, uint8_t model_id, uint8_t quant_bits,
                 uint8_t reason, uint8_t ext_flags,
                 uint32_t tokens, uint32_t hits, uint32_t ctx_size,
                 uint64_t created_at, uint64_t last_used, uint64_t payload_bytes,
                 uint64_t conv_id, uint64_t model_fp, uint32_t bucket,
                 uint8_t level, bool stale):
    memset(h, 0, 72)
    h[0..2]="KVC"; h[3]=version; h[4]=quant_bits; h[5]=reason; h[6]=ext_flags; h[7]=model_id
    le32 tokens@8, hits@12, ctx@16; h[20]=payload_abi
    le64 created@24, last_used@32, payload@40
    if version >= 2:
        le64 conv_id@48, model_fp@56, le32 bucket@64, level@68, stale?1:0 @69
```
Callers: store writes version=2 + all new fields. `touch_file` writes back the version it read
(v1 → base 48 only; v2 → full 72), preserving `conv_id/bucket/level/stale/model_fp` from `e`.

### B3. `ds4_kvstore_read_header` (v2-aware) — pseudocode
```c
bool read_header(FILE *fp, ds4_kvstore_entry *e, uint32_t *text_bytes,
                 size_t *header_total):
    h[48]; fread 48
    if magic/abi mismatch: return false
    version = h[3]
    if version == 1:
        // no extra; text_bytes immediately after base
        read tb[4] -> text_bytes
        e->hdr_version=1; e->conv_id=0; e->model_fp=0;
        e->bucket = (e->tokens>0)? e->tokens / anchor_step : 0;   // derive, opt not yet known?
        e->level=0; e->stale=false
        *header_total = 48 + 4
        return valid
    if version == 2:
        read 24 extra; conv_id@48, model_fp@56, bucket@64, level@68, stale@69
        read tb[4] -> text_bytes
        e->hdr_version=2
        *header_total = 72 + 4
        return valid
    return false   // unknown version
```
NOTE: `bucket` derivation needs `anchor_step`. read_header is called before options are known in
some paths (touch_file reads header standalone). Solution: if `bucket==0` and tokens>0, derive
bucket = tokens / `KV_CACHE_DEFAULT_ANCHOR_STEP` (a module constant default) — legacy files get
a reasonable bucket; store always writes the exact bucket. Keep it simple: use a default-constant.

### B4. `ds4_kvstore_read_entry_file` — pseudocode
```c
bool read_entry_file(path, sha, out):
    stat path; if st_size < (48+4): return false
    fopen rb
    read_header(fp, &e, &text_bytes, &header_total)   // detects version
    fclose
    if !ok: return false
    // validate size with version-aware header_total
    expected = header_total + text_bytes + payload_bytes
    if st_size < expected: return false
    copy sha; path=xstrdup; file_size=st_size
    *out = e
    return true
```
This replaces the hardcoded `FIXED_HEADER + 4` size check (ds4_kvstore.c ~445).

### B5. `ds4_kvstore_compute_conv_id` — pseudocode
```c
uint64_t ds4_kvstore_compute_conv_id(text, text_len, model_fp):
    // A conversation's HEAD is stable across its own turns and across compaction
    // (head = system + early turns persists). New sessions have a different head.
    n = min(text_len, CONV_ID_HEAD_BYTES)
    sha1(text[0..n)) -> 20 bytes
    conv = first 8 bytes (le64) of the digest
    // fold model_fp so distinct namespaces can't collide:
    conv ^= (model_fp * 0x9E3779B97F4A7C15ull)     // mix
    return conv
```
Guarantees: same namespace + same head → same id; different namespace → different id.
Acceptable merge risk: two conversations sharing an identical head window (rare) behave as one
lineage — identical to prefix-clustering.

### B6. `ds4_kvstore_find_text_prefix` — add model_fp filter
```c
for each entry i:
    if e->text_bytes > prompt_bytes ... (as today)
    if e->tokens < min_tokens: continue
    if e->model_id != model_id: continue
    if e->model_fp && e->model_fp != kc->model_fp: continue   // NEW cross-model reject
    if reject_different_quant && quant mismatch: continue
    if ctx_size < e->ctx_size: continue
    ... keep largest valid by text_bytes + sha compare (unchanged)
```
Legacy `model_fp==0` always matches (accept old caches). `kc->model_fp` is the process constant.

### B7. `ds4_kvstore_mark_stale_at_load` — NEW
```c
void mark_stale_at_load(kc, prompt_text, model_id, quant_bits, ctx_size):
    prompt_bytes = strlen(prompt_text)
    for i in 0..len:
        e = entry[i]
        if e->model_fp && e->model_fp != kc->model_fp: continue   // different namespace
        if e->model_id != model_id: continue
        if reject_different_quant && quant mismatch: continue
        if e->ctx_size > ctx_size: continue
        if e->text_bytes > prompt_bytes: continue    // may match a future longer prompt
        if e->tokens < min_tokens: continue
        // is prompt[0..e->text_bytes) exactly e's cached text?
        char sha[41]; sha1(prompt_text, e->text_bytes, sha)
        if strcmp(sha, e->sha) != 0:
            // e's text is NOT a byte-prefix of the current prompt -> stale for it
            e->stale = true
            persist: touch_file(e->path, e->hits, stale=true)  // rewrite header in place
```
Cheap: no payload reads; one SHA1 per candidate over the prefix bytes. Called in
`try_load_text` right after a `find_text_prefix` hit, before opening the selected file.

### B8. Keep-set helper (conversation ladder) — NEW static
```c
// For one conversation, which of its anchors are "kept" at level L?
// tokens_list = tokens (ascending) of entries with conv_id == C (same namespace, not stale)
keep_set_for_conv(C, level):
    frontier = max tokens in C
    keep = {}
    // dense small: all <= small_dense_tokens
    for t in C where t <= small_dense_tokens: keep += t
    // tail: the tail_anchors largest (frontier + tail_anchors-1 below it)
    top = tail_anchors largest tokens of C; keep += top
    // middle: large anchors, spaced to the frontier
    spacing = mid_spacing_tokens * (1 << level)      // level 0 -> mid_spacing
    for t in C where t > small_dense_tokens and t not in top:
        if (frontier - t) % spacing == 0: keep += t
    return keep
// An entry is "redundant" iff its (conv_id, tokens) is NOT in its conv's keep_set.
```
For `conv_id==0` legacy singletons: treat each as its own keep-set (always kept) so old files are
never dropped as redundant; they exit only via LRU/retirement/over-budget.

### B9. `ds4_kvstore_evict` — rewrite (conversation-scoped)
```c
void evict(kc, live, extra_bytes, incoming):
    if !enabled or budget==0 return
    if extra > budget return
    kv_cache_refresh(kc)
    now = time
    total = sum(entry.file_size)
    target = budget_bytes - extra_bytes
    active_conv = incoming && incoming->text
                  ? compute_conv_id(incoming->text, incoming->text_len, kc->model_fp)
                  : UINT64_MAX                       // no active conversation (open/shutdown)

    while total > target && len > 0:
        // PHASE A: remove stale or redundant entries (applies to ALL convs, incl. active)
        victim = find_stale(kc)                      // any stale, same-namespace
        if victim < 0:
            victim = find_redundant(kc, active_conv) // entry not in its conv keep-set
        if victim >= 0:
            unlink victim; total -= file_size; remove entry; continue

        // PHASE B: nothing stale/redundant left -> create space by halving LRU non-active
        conv = least_recently_active(kc)             // max last_used, conv != active_conv,
                                                     //   conv_id != 0, count > min_anchors
        if conv exists:
            bump conv level: for entries of conv: level = level+1  // doubles mid spacing
            // persist level via touch_file for each bumped entry
            continue                                   // next iter evicts newly-redundant middle

        // PHASE C: retire the LRU non-active conversation entirely
        conv = least_recently_active(kc)              // any conv != active_conv
        if conv exists and conv != active_conv and count(conv) <= min_anchors:
            unlink ALL entries of conv; total -= sum; remove them; continue

        // PHASE D: only the active conversation remains at its floor -> stop
        break
```
- `active_conv`'s keep-set is computed at its current level; evicting its **redundant** middle
  anchors (compaction collapse) is allowed, but its **frontier + tail + small-dense** are always
  in the keep-set → never dropped while active.
- `least_recently_active` = conversation with the **oldest** `last_used` (LRU). Entries carry
  `last_used`; take min over conv members.
- Over `max_conversations`: Phase C also retires the LRU conv whenever distinct active conv count
  exceeds the cap, even before budget pressure.
- Deterministic: each pass picks a single victim by a fixed priority; O(entries) per pass,
  bounded by number of removals.

### B10. `ds4_kvstore_store_live_prefix_text` — write v2 header + protect active conv
Insert before `ds4_kvstore_evict(...)` (ds4_kvstore.c:1052), after `text`/`text_len` are known:
```c
uint64_t conv_id = ds4_kvstore_compute_conv_id(text, text_len, kc->model_fp);
uint32_t bucket = store_tokens.len / anchor_step;   // anchor_step>0
uint8_t  level  = 0;
bool     stale  = false;
incoming.conv_id = conv_id;    // add field to ds4_kvstore_eviction_context
ds4_kvstore_evict(kc, live_tokens, est_file_bytes, &incoming);
```
And in the file-write path, call `fill_header(h, 2, model_id, quant_bits, reason, ext_flags,
tokens, hits, ctx, created_at, last_used, payload_bytes, conv_id, kc->model_fp, bucket, level,
stale)` — replacing the current v1 call. Size accounting: use `DS4_KVSTORE_FIXED_HEADER + 4 +
24` for new stores (72+4), and `ds4_kvstore_file_size_fits` must use the v2 header total.

### B11. `ds4_kvstore_try_load_text` — stale marking + clear on hit
```c
idx = find_text_prefix(...)
if idx < 0: return 0
ds4_kvstore_mark_stale_at_load(kc, prompt_text, model_id, quant_bits, ctx_size)  // NEW
... read/validate/load selected entry (unchanged) ...
if loaded > 0:
    if cold_max exceeded: unlink; consumed=true   (unchanged)
    else:
        // clear stale on the selected (valid) hit and refresh last_used:
        touch_file(path, hdr.hits + 1, stale=false)   // NEW param: clear stale
        // also bump last_used so its conversation is "recently active"
    ... log, fill result (unchanged) ...
```
`touch_file` now writes back the original version with `stale` cleared and `last_used=now`.

### B12. `ds4_kvstore_open` — accept model_fp
Add `uint64_t model_fp` param; set `kc->model_fp = model_fp`. Call the initial
`ds4_kvstore_evict(kc, NULL, 0, NULL)` (incoming NULL → no active conv → pure LRU/retirement
prune of over-budget legacy files). Existing v1 files load fine (conv_id=0, model_fp=0).

---

## C. ds4_server.c wiring

### C1. CLI flags (parse into `kv_cache_options` before open)
```
--kv-cache-anchor-step        (default 8192)
--kv-cache-small-dense        (default 16384)
--kv-cache-tail-anchors       (default 2)
--kv-cache-mid-spacing        (default 131072)
--kv-cache-min-anchors        (default 4)
--kv-cache-max-conversations  (default 0 = unlimited)
```
Keep existing `--kv-cache-continued-interval-tokens` (controls when continued checkpoints are
written, independent of bucket step).

### C2. `kv_cache_open` — pass new options + model_fp
```c
kv_cache_open(kc, dir, budget_mb, reject_different_quant, opt):
    // opt now carries the new knobs (defaults set in kv_cache_default_options)
    // compute model_fp once:
    uint64_t fp = ds4_engine_model_fingerprint(s->engine);   // NEW, see C4
    ds4_kvstore_open(kc, dir, budget_mb, reject_different_quant, fp, opt, ...)
    log the new knobs (anchor_step, small_dense, tail, mid, min_anchors)
```
`kv_cache_default_options()` must seed the new option fields with the defaults above.

### C3. Server eviction wrapper `kv_cache_evict` (9244)
No change needed for conv_id: `ds4_kvstore_evict` derives `active_conv` from `incoming->text`
itself. The server's store path already fills `incoming.text`; only add `incoming.conv_id` is
optional (evict recomputes it). Keep the wrapper as-is.

### C4. `ds4_engine_model_fingerprint` — NEW (ds4.c)
```c
uint64_t ds4_engine_model_fingerprint(ds4_engine *e):
    // The cache key must reflect WEIGHTS + TOKENIZER + ARCH, NOT the protocol name/temp,
    // so byte-compatible builds (0731 vs v2) share one fingerprint and reuse KV.
    // Cheap, stable-across-restarts: hash the model FILE identity (path+size+mtime).
    // (Full weight hashing is O(GB); header-only is a weaker but cheap approximation.)
    stat(model_path)
    fp = mix( file_size, mtime, path_hash )
    return fp
```
- Same weights file (0731/v2 alias) → same fp → KV reused. Real weight swap → fp changes →
  cross-model reject (full prefill), old-namespace files survive for routing back.
- If `model_path` is unavailable at open, return 0 (legacy-accept mode).

### C5. Tests (ds4_server.c unit tests)
Update existing (they assert old global-score eviction):
- `test_kv_cache_eviction_protects_current_store`
- `test_kv_cache_eviction_does_not_protect_oversize_current_store`
- `test_kv_cache_eviction_keeps_aligned_continued_frontiers`
- `test_kv_cache_eviction_values_fresh_snapshots` / decayed-hits tests
  → re-target to new keep-set / conversation semantics (small anchor not dropped for a big one;
  frontier+tail never dropped while active).
Add per PLAN §7:
1. **Retention of spread:** anchors ≤ small_dense all kept; no small anchor evicted for a big one.
2. **Tail density:** frontier + tail_anchors kept; 3rd tail anchor is droppable when over budget.
3. **Halving:** LRU conv's mid_spacing doubles; active conv's tail/frontier never halved.
4. **Age tier:** LRU conv downgraded to old-tier largest; large conv (frontier ≥ threshold) downgraded.
5. **Retirement:** conv reduced to ≤ min_anchors is evicted entirely.
6. **Stale at load:** tool-call divergence marks only anchors past the tool-call stale; tail anchor
   below survives and is selected.
7. **Compaction:** middle rewrite marks high anchors stale+evicted; head anchor survives; rebuild =
   prompt_len − anchor_tokens.
8. **Multi-conv:** two convs at same token count keep separate ladders; eviction never strips one
   conv for the other's budget; halving/retirement is conversation-scoped.
9. **Model/quant routing:** different model_fp/quant never selected nor marked stale; old-namespace
   survives; same weights + different name/temp reuses KV.
10. **Budget goal:** 3×150k + 1×220k fits under 32 GB (≈26 GB); assert total ≤ budget after evict.
11. **Header round-trip:** write v2 header, read back equals; v1 file reads with defaults; touch
    preserves version.

---

## D. Migration & risk notes
- Existing v1 cache files: read with defaults (conv_id=0, model_fp=0, bucket=tokens/step). They
  are never upgraded in place (avoids corrupting v1 text_bytes offset). New stores are v2.
- `conv_id==0` legacy singletons are always "kept" (never redundant); they exit via LRU/retirement
  or over-budget. Acceptable one-time migration.
- `active_conv` protection: while a conversation is actively stored, its frontier+tail+small-dense
  are never evicted; only redundant middle (compaction collapse) and stale are dropped.
- Cross-quant: `reject_different_quant` stays default-true (correctness); `model_fp` adds
  cross-model rejection.
- Determinism: eviction is O(entries) per pass, fixed priority (stale → redundant → halve →
  retire → stop). No randomness.

## Verification sequence (after implementation)
1. Build (`make`) + run `ds4-server` unit tests (the kv_cache_* tests).
2. Restart the server; confirm old .kv files still load (v1 read path) and new stores are v2.
3. Replay the 11:39 tool-call divergence (120k): confirm rebuild ≤ step via a surviving tail anchor.
4. Replay the 10:34 compaction (77k, common=30737): confirm rebuild = prompt_len − anchor_tokens
   (irreducible), not worse than current.
5. Check eviction log lines show `conv=`/`level=` and that the active conversation's tail/frontier
   survive; budget total stays ≤ 32 GB.
