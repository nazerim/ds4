# PLAN: PLE N-gram Table Preload & Caching (Qwen3.8 Flash Next)

**Status**: Draft
**Date**: 2026-10-09
**Author**: nazerim + opencode
**Target**: ds4 fork (nazerim/ds4), Qwen3.8 Flash Next on M5 Max 128GB

---

## Upstream PR Survey (2026-10-09)

**No existing PRs for PLE/n-gram table caching.** Searched `antirez/ds4` open + closed PRs.

### Related PRs found

| PR | Title | State | Relevance |
|----|-------|-------|-----------|
| [#1047](https://github.com/antirez/ds4/pull/1047) | Metal: add SSD expert streaming for Qwen3.8 Flash Next | **Closed** | Adds `--ssd-streaming` for Qwen3.8 experts, but **explicitly rejects popularity preloading** for Qwen: *"Qwen demand-fills its cache; popularity preloading and GLM full-resident-layer options are rejected."* N-gram table stays on disk with no caching. |
| [#1072](https://github.com/antirez/ds4/pull/1072) | Let V4.1 single-token decode use the concurrent Engram reader | **Closed** | **Critical insight**: oMLX tried RAM-caching Engram hot rows — measured **22.5% row reuse** over 600-token coding generation. Concurrency won, caching didn't. *"Wrong idea, right place to look."* |
| [#1167](https://github.com/antirez/ds4/pull/1167) | Add n-gram prompt-lookup speculative drafting | Open | Different n-gram use (prompt matching for speculative decoding), not PLE table caching. |
| [#1180](https://github.com/antirez/ds4/pull/1180) | CUDA: V4.1 expert hit profiler and resident fast path for SSD streaming | Open | Expert profiling for CUDA; related to hotlist work but not PLE. |
| [#1179](https://github.com/antirez/ds4/pull/1179) | Speed up Qwen3.8 Flash Next on Metal | Open (WIP) | General Qwen3.8 speedup; body is just "WIP". |

### Key takeaway from PR #1072

> *"The oMLX / drowzeys V4.1-on-Mac work... Their approach was a RAM cache of hot rows; measured row reuse here was only 22.5% over a 600-token coding generation, worth roughly +1 tok/s — so the win turned out to be concurrency rather than caching."*

**3-gram hashing produces mostly unique rows** — each token's context differs, so the hash differs, so the row differs. Caching has inherently low hit rates for n-gram tables.

**However**, PLE may differ from Engram in access patterns (see Analysis below).

---

## Problem

Qwen3.8's **PLE (Progressive Layer Enhancement)** layer uses a **95.37 GiB n-gram embedding table** that stays on disk. Every token triggers a lookup:

```
Per token: 24 heads × 320 bytes = 7.5 KB read from disk
Decode:    synchronous pread() — GPU blocks waiting for CPU
Prefill:   batched with 16 concurrent readers (dedup + sort)
```

### Current implementation (`ds4.c:57465`)

```c
static bool qwen4_ngram_row(const ds4_model *m, uint32_t row, float *out) {
    uint8_t raw[320];
    const uint64_t offset = t->abs_offset + (uint64_t)row * bytes;
    // ...
    ssize_t n = pread(m->ngram_fd, raw + done, bytes - done, ...);  // ← SYNC, NO CACHE
    // ...
}
```

**No cache, no preload, no prefetch.** Every row access is a `pread()` syscall.

### Decode bottleneck

| Phase | PLE access | Latency |
|-------|-----------|---------|
| Prefill (50k tokens) | Batched, 16 readers, dedup | Hidden by parallelism |
| **Decode** (5-10k tokens) | **1 token × 24 heads, serial `pread()`** | **Fully exposed** |

At 30 t/s decode (~33ms/token), PLE `pread()` (~0.1ms) is ~0.3% — negligible. But:
- **MTP speculative decoding**: verifies N tokens at once — PLE reads serialize
- **Batched sessions** (`--batched-session N`): N slots share PLE read, still sync
- **Higher decode speeds** (60-100 t/s with MTP): PLE I/O becomes measurable

---

## Analysis: Will Caching Help?

### Evidence against (from PR #1072)

- **22.5% row reuse** for V4.1 Engram over 600-token coding generation
- 3-gram hashing: each token produces different hash → different row
- **Concurrency > caching** for Engram

### Evidence for (PLE-specific)

| Factor | Engram (V4.1) | PLE (Qwen3.8) |
|--------|--------------|---------------|
| N-gram size | 4 | 3 |
| Heads per token | 48 (2 layers × 24 cols) | 24 (1 layer × 24 heads) |
| Row size | 264 bytes | 320 bytes |
| Access pattern | Every token, 2 layers | Every token, 1 layer |
| **Reuse potential** | 22.5% measured | **Unknown — needs measurement** |

**PLE might have higher reuse because:**
1. **Smaller n-gram (3 vs 4)**: fewer unique combinations → more collisions → more reuse
2. **Single layer**: all accesses go to one table (vs Engram's 2 tables)
3. **Code/text repetition**: coding agents repeat patterns (indentation, function signatures, common phrases)
4. **Multi-turn conversations**: rows from previous turns may recur

**But**: the fundamental issue remains — 3-gram hashing of sequential tokens produces mostly unique hashes.

### Recommendation: **Measure first, then decide**

Before implementing a cache, measure PLE row reuse on real workloads:

```sh
# Instrument qwen4_ngram_row to log row IDs
DS4_QWEN4_PLE_TRACE=/tmp/ple-trace.log \
  ./ds4 -m gguf/Qwen3.8-Flash-Next-Q4.gguf --ctx 32768 \
  -p "coding prompt" -n 2048

# Analyze reuse
python3 -c "
import collections
rows = [int(l) for l in open('/tmp/ple-trace.log')]
c = collections.Counter(rows)
total = len(rows)
unique = len(c)
reuse = 1 - unique/total
print(f'Total accesses: {total}')
print(f'Unique rows: {unique}')
print(f'Reuse rate: {reuse:.1%}')
print(f'Top 10 rows: {c.most_common(10)}')
"
```

**Decision gate**: If reuse > 40%, implement cache. If < 30%, skip cache and do concurrency/prefetch instead.

---

## Implementation Options

### Option A: LRU Row Cache (if reuse is high)

Same architecture as expert cache:

```c
typedef struct {
    uint32_t *keys;        // row IDs
    float *values;         // 320 bytes per row (padded to 512 for alignment)
    uint64_t *last_used;   // LRU timestamp
    uint32_t *hotness;     // LFU counter (optional, decode-biased)
    size_t capacity;       // e.g., 1-4 GB / 512 bytes = 2-8M rows
    size_t count;
    pthread_mutex_t lock;
} ds4_ple_cache;

// In qwen4_ngram_row: check cache first
static bool qwen4_ngram_row_cached(const ds4_model *m, uint32_t row, float *out) {
    if (ds4_ple_cache_get(row, out)) return true;           // hit: memcpy
    if (!qwen4_ngram_row(m, row, out)) return false;        // miss: pread
    ds4_ple_cache_put(row, out);                             // insert
    return true;
}
```

**Env vars:**
- `DS4_QWEN4_PLE_CACHE_GB=2` — cache size (default: 0 = disabled)
- `DS4_QWEN4_PLE_CACHE_DECODE_ONLY=1` — only cache during decode

### Option B: Concurrent Decode Reads (if reuse is low)

Like PR #1072 did for Engram — use the batched reader even for single-token decode:

```c
// Current decode path (serial):
if (count < 2) return qwen4_ngram_row(m, rows[0], out);  // 1 pread per head

// Proposed: batch all 24 heads into one concurrent read
// 24 rows × 320 bytes = 7.5 KB — fits in one batch
// dispatch_apply with 8-16 readers instead of serial pread
```

**Expected win**: ~8x faster PLE read (parallel vs serial pread), similar to PR #1072's Engram fix (+9.9%).

### Option C: Prefetch / Async I/O

Overlap PLE read with GPU compute:

```
Token N:    GPU computes layers 2-48 (non-PLE) while PLE row for token N+1 is prefetched
Token N+1:  PLE row already in cache/page-cache, GPU proceeds immediately
```

Requires:
- Predict next token's n-gram (can't know until token N is sampled)
- **Alternative**: prefetch based on MTP draft tokens (speculative)

### Option D: NAX Acceleration (compute side)

PLE has GEMV operations that could use NAX:

| Operation | Current kernel | NAX potential |
|-----------|---------------|---------------|
| Key projection | `qwen4_gemv` (generic) | ✅ GEMV → NAX tile kernel |
| Value projection | `qwen4_gemv` (generic) | ✅ GEMV → NAX tile kernel |
| Gate | `kernel_qwen4_ple_gate` | ❌ Element-wise/reductions, not GEMM |
| Conv | `kernel_qwen4_ple_conv` | ⚠️ Small (K=4), may not tile well |

**PLE key/value shapes:**
- `ple_key`: [2560, hc_dim] × [2560] → [hc_dim]
- `ple_value`: [2560, 2560] × [2560] → [2560]

hc_dim depends on `n_hc_lowrank=320`. Likely hc_dim = 2560/320 * 4 = 32? Or 2560 * 4 / 320 = 32 per stream × 4 streams = 128 total?

Actually from the code: `tensor_expect_qwen4_dense_layout(l->ple_key, 2, DS4_N_EMBD, hc_dim, 0)` where `DS4_N_EMBD=2560`. The GEMV is [2560, hc_dim] × [2560].

For NAX tiling (32 or 64 rows), hc_dim needs to be ≥ 32. Need to verify.

**NAX benefit**: Faster GEMV → less GPU time per token → I/O becomes relatively more significant (mixed blessing) OR better overlap of I/O with compute.

### Option E: OS Page Cache Hint

Instead of a custom cache, use `posix_fadvise(POSIX_FADV_WILLNEED)` to prefetch rows into the OS page cache:

```c
// After computing row IDs for prefill batch, hint the kernel
for (size_t i = 0; i < count; i++) {
    posix_fadvise(m->ngram_fd, offset(row[i]), 320, POSIX_FADV_WILLNEED);
}
```

This is simpler than a custom cache and leverages the kernel's readahead. But:
- No control over eviction
- Competes with other file I/O
- macOS page cache behavior may differ

---

## Recommended Approach

Given the **PR #1072 evidence** (22.5% reuse for Engram), I recommend:

### Phase 1: Measure (1 day)
1. Instrument `qwen4_ngram_row` to log row IDs
2. Run on representative workloads (coding agent, chat, long-context)
3. Calculate reuse rate

### Phase 2: Implement based on measurement

| Reuse rate | Recommendation |
|-----------|----------------|
| **> 40%** | Option A (LRU cache) + Option D (NAX for GEMV) |
| **20-40%** | Option B (concurrent decode reads) + Option D (NAX) |
| **< 20%** | Option B only (concurrent reads), skip cache |

### Phase 3: NAX for PLE GEMV (parallel track)

Independent of caching — NAX acceleration of key/value projections:

1. Create `kernel_qwen4_ple_key_nax` and `kernel_qwen4_ple_val_nax`
2. Based on `kernel_qwen4_moe_mm_mid_nax_t` (metal/qwen4.metal:4652)
3. BF16 weights, FP16 activations (matches current PLE precision)
4. Gate: `DS4_QWEN4_PLE_NAX=1` (default off for bit-exactness testing)

---

## Implementation Details

### A. LRU Cache (Option A)

**File**: `ds4.c` (or new `ds4_ple_cache.c`)

```c
// ds4_ple_cache.h
typedef struct ds4_ple_cache ds4_ple_cache;

ds4_ple_cache *ds4_ple_cache_create(size_t bytes);
void ds4_ple_cache_destroy(ds4_ple_cache *c);
bool ds4_ple_cache_get(ds4_ple_cache *c, uint32_t row, float *out);  // 320 bytes
bool ds4_ple_cache_put(ds4_ple_cache *c, uint32_t row, const float *in);
void ds4_ple_cache_clear(ds4_ple_cache *c);

// Hash table: open addressing, power-of-2 size
// Slot: {uint32_t key; uint32_t pad; float value[80]; uint64_t last_used;}
// Eviction: LRU (scan for oldest, or clock-sweep)
```

**Integration** (`qwen4_ngram_row` at ds4.c:57465):

```c
static ds4_ple_cache *g_ple_cache;  // global, created at model load

static bool qwen4_ngram_row(const ds4_model *m, uint32_t row, float *out) {
    // NEW: check cache first
    if (g_ple_cache && ds4_ple_cache_get(g_ple_cache, row, out))
        return true;

    // Existing pread path
    // ...

    // NEW: insert into cache
    if (g_ple_cache)
        ds4_ple_cache_put(g_ple_cache, row, out);
    return true;
}
```

### B. Concurrent Decode Reads (Option B)

**File**: `ds4.c:57542` (`qwen4_ngram_read`)

```c
static bool qwen4_ngram_read(const ds4_model *m, const uint32_t *rows,
                              size_t count, float *out) {
    // ... validation ...

    // NEW: always use batched path, even for small counts on Apple
    // Lower threshold from 256 to 8 (like PR #1072 did for Engram)
#ifdef __APPLE__
    if (count < 8) {  // was: count < 2
        // Still serial for 1-7 rows, but...
        // For 8-255 rows: use dispatch_apply with min(count, 16) readers
    }
    // Use dispatch_apply for all counts >= 8
    // ...
#endif
}
```

### C. NAX Kernels (Option D)

**File**: `metal/qwen4.metal` (new kernels after line 4951)

```metal
// Based on kernel_qwen4_moe_mm_mid_nax_t
// PLE key: [E=2560, hc_dim] × [E] → [hc_dim]
// PLE value: [E=2560, E=2560] × [E] → [E]

struct ds4_metal_args_qwen4_ple_gemv {
    uint32_t n_rows;      // hc_dim or E
    uint32_t n_cols;      // E = 2560
    uint32_t batch;       // T (1 for decode, N for prefill)
    // ...
};

kernel void kernel_qwen4_ple_key_nax(
    constant ds4_metal_args_qwen4_ple_gemv & args,
    device const char *weights,    // BF16 [n_rows, n_cols]
    device const half *activations, // [batch, n_cols]
    device float *out,             // [batch, n_rows]
    // ...
) {
    // NAX tensor op: mps_igemm or similar
    // Tile: 32 or 64 rows (same as MoE NAX kernels)
}
```

**Host wrapper** (`ds4_metal.m`):

```c
int ds4_gpu_qwen4_ple_key_nax_tensor(ds4_gpu_tensor *out,
                                     const ds4_model *m,
                                     const ds4_tensor *weight,
                                     ds4_gpu_tensor *activations,
                                     uint32_t batch) {
    // Dispatch kernel_qwen4_ple_key_nax
}
```

---

## Test Plan

### Unit tests

1. **Cache correctness** (`tests/test_ple_cache.c`):
   - Insert/get/evict cycle
   - LRU eviction order
   - Concurrent access (multi-threaded)
   - Cache hit returns identical bytes to disk read

2. **Concurrent read correctness** (`tests/test_qwen4_ngrams.c` — extend):
   - Batched read with count < 256 produces identical output to serial
   - Deduplication within batch still works

3. **NAX GEMV correctness** (`tests/test_qwen4_ple_nax.c`):
   - NAX vs reference GEMV: bit-exact or within tolerance
   - Prefill and decode shapes

### Integration tests

4. **A/B benchmark** (M5 Max 128GB, Qwen3.8 Q4):
   ```sh
   # Baseline
   ./ds4-bench -m gguf/Qwen3.8-Flash-Next-Q4.gguf \
     --prompt-file speed-bench/promessi_sposi.txt \
     --ctx-start 4096 --ctx-max 65536 --step-mul 2 --gen-tokens 512 \
     --csv /tmp/ple-baseline.csv

   # With cache
   DS4_QWEN4_PLE_CACHE_GB=2 \
   ./ds4-bench -m gguf/Qwen3.8-Flash-Next-Q4.gguf \
     --prompt-file speed-bench/promessi_sposi.txt \
     --ctx-start 4096 --ctx-max 65536 --step-mul 2 --gen-tokens 512 \
     --csv /tmp/ple-cache.csv

   # With NAX
   DS4_QWEN4_PLE_NAX=1 \
   ./ds4-bench -m gguf/Qwen3.8-Flash-Next-Q4.gguf \
     --prompt-file speed-bench/promessi_sposi.txt \
     --ctx-start 4096 --ctx-max 65536 --step-mul 2 --gen-tokens 512 \
     --csv /tmp/ple-nax.csv
   ```

5. **MTP benchmark**: Verify PLE improvements help MTP speculative decoding
   ```sh
   ./ds4 -m gguf/Qwen3.8-Flash-Next-Q4.gguf --mtp --mtp-timing \
     -p "coding prompt" -n 512
   ```

6. **Quality check**: `--quality` run to ensure no logit divergence

### Regression tests

7. **Qwen3.8 resident mode**: Verify no regression when cache disabled
8. **Qwen3.8 SSD streaming** (if PR #1047 merges): Verify cache works with expert streaming
9. **Multi-session**: Verify cache shared correctly across sessions

---

## Rollout

| Phase | Change | Risk | Prerequisite |
|-------|--------|------|--------------|
| 1 | Measure PLE row reuse | None | — |
| 2a | Concurrent decode reads (Option B) | Low | Reuse < 40% |
| 2b | LRU cache (Option A) | Medium | Reuse > 40% |
| 3 | NAX for PLE GEMV (Option D) | Medium | Bit-exactness validation |
| 4 | Document in `docs/QWEN38_FLASH_NEXT.md` | Low | — |

---

## Open Questions

1. **What is hc_dim?** Need to verify the PLE key/value GEMV dimensions for NAX tiling.
2. **Does macOS page cache already help?** `pread()` on a file that was recently read may hit the page cache. Need to measure with cold cache (`vmtouch -e` or `purge`).
3. **MTP draft tokens**: Can we prefetch PLE rows for MTP draft tokens before verification?
4. **Multi-GPU (TP)**: Does PLE row access differ across TP ranks?
5. **Vision sessions**: Do image tokens trigger PLE lookups differently?

---

## References

- **PR survey**: `gh search prs --repo antirez/ds4` (2026-10-09)
- **PR #1072**: Engram concurrency vs caching insight (22.5% reuse)
- **PR #1047**: Qwen3.8 SSD expert streaming (rejects popularity preloading)
- `ds4.c:57465` — `qwen4_ngram_row()` (current pread implementation)
- `ds4.c:57542` — `qwen4_ngram_read()` (batched reader)
- `ds4.c:57510` — `qwen4_ngram_part()` (dedup within batch)
- `ds4.c:68333` — `qwen4_ple_step()` (n-gram hash → row IDs)
- `metal/qwen4.metal:1362` — `kernel_qwen4_ple_gate` (NOT NAX)
- `metal/qwen4.metal:1450` — `kernel_qwen4_ple_conv` (NOT NAX)
- `metal/qwen4.metal:4652` — `kernel_qwen4_moe_mm_mid_nax_t` (NAX reference for PLE GEMV)
- `ds4.c:803` — `DS4_SHAPE_QWEN4_EXP` (Qwen3.8 shape: n_ple_layer=1, n_ple_ngram=3)
- `docs/QWEN38_FLASH_NEXT.md:25` — "95.37 GiB n-gram table stays on disk"
- `docs/QWEN38_FLASH_NEXT.md:20-23` — Model sizes (Q2: 41.73 GiB, Q4K: 69.74 GiB resident)
