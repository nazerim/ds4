# PLAN: Decode-Biased Expert Hotlist

**Status**: Draft
**Date**: 2026-10-09
**Author**: nazerim + opencode
**Target**: ds4 fork (nazerim/ds4), V4.1 Flash SSD streaming on M5 Max 128GB

---

## Problem

Runtime expert hotness tracking and profiling aggregate **prefill + decode** expert selections without distinction. For agent workloads (50k prefill + 5–10k decode), prefill dominates by ~6.7x in selection count:

```
Prefill:  50k tokens × 6 experts × 40 layers = 12.0M selections
Decode:    7.5k tokens × 6 experts × 40 layers =  1.8M selections
         ─────────────────────────────────────────────────────
Total:    13.8M selections, 87% from prefill
```

This biases:
- **LFU eviction**: prefill-hot experts survive, decode-hot experts get evicted
- **Profiler hotlist**: generated hotlist dominated by prefill experts
- **Cache efficiency**: cache optimized for prefill (latency-tolerant) instead of decode (latency-exposed)

### Why decode matters more

| Phase | Cache miss impact | Why |
|-------|-------------------|-----|
| Prefill | Low | Batched, parallel, SSD latency hidden by compute |
| Decode | **High** | Sequential, one token at a time, SSD latency fully exposed |

From `docs/SSD_STREAMING.md`: *"Generation is usually more sensitive to cache misses than prefill."*

---

## Solution: Gate hotness tracking on decode phase

Only increment `g_stream_expert_cache_route_hotness` during **decode** phase. Prefill expert usage is handled separately by the existing prefill seeding mechanism.

### Design decisions

| Aspect | Choice | Rationale |
|--------|--------|-----------|
| **Mechanism** | Gate on `ds4_gpu_decode_phase()` | Already exists (ds4_metal.m:1345), used for GPU busy profiling |
| **Default** | Keep current behavior (backward compatible) | `DS4_STREAM_HOTNESS_DECODE_ONLY` env var to opt-in |
| **Prefill experts** | Existing prefill seeding handles them | `metal_graph_seed_streaming_expert_cache_from_prefill` (ds4.c:33739) |
| **Profiler** | Separate env var `DS4_EXPERT_PROFILE_DECODE_ONLY` | Generate decode-biased hotlist files |

---

## Implementation

### 1. Runtime hotness gating (ds4_metal.m)

**File**: `ds4_metal.m`
**Function**: `ds4_gpu_stream_expert_cache_note_selected_hotness` (~line 14497)

```c
// Current (biased):
void ds4_gpu_stream_expert_cache_note_selected_hotness(
        uint32_t layer, uint32_t expert, ...) {
    g_stream_expert_cache_route_hotness[layer][expert]++;
    // ...
}

// Proposed (decode-gated):
static bool g_hotness_decode_only = false;  // cached env check

void ds4_gpu_stream_expert_cache_note_selected_hotness(
        uint32_t layer, uint32_t expert, ...) {
    // Lazy env check
    if (!g_hotness_decode_only) {
        static int cached = -1;
        if (cached < 0)
            cached = getenv("DS4_STREAM_HOTNESS_DECODE_ONLY") != NULL;
        g_hotness_decode_only = cached;
    }
    // Gate: skip during prefill if decode-only mode
    if (g_hotness_decode_only && !ds4_gpu_decode_phase())
        return;
    g_stream_expert_cache_route_hotness[layer][expert]++;
    // ...
}
```

**Alternative: weighted approach** (softer, keeps prefill signal):

```c
// Env: DS4_STREAM_HOTNESS_DECODE_WEIGHT=10 (default: 1 = current behavior)
const int weight = ds4_gpu_decode_phase() ? decode_weight : 1;
g_stream_expert_cache_route_hotness[layer][expert] += weight;
```

### 2. Profiler gating (ds4.c)

**File**: `ds4.c`
**Function**: `ds4_expert_profile_record` (line 1687)

```c
static void ds4_expert_profile_record(
        uint32_t      il,
        uint32_t      pos,
        const int32_t selected[DS4_MAX_EXPERT_USED],
        const float   weights[DS4_MAX_EXPERT_USED],
        bool          is_hash) {
    ds4_expert_profile *p = &g_expert_profile;
    if (!p->active || il >= p->n_layer) return;

    // NEW: Skip prefill records if decode-only profiling
    if (!ds4_gpu_decode_phase()) {
        static int decode_only = -1;
        if (decode_only < 0)
            decode_only = getenv("DS4_EXPERT_PROFILE_DECODE_ONLY") != NULL;
        if (decode_only) return;
    }

    // ... existing recording code
}
```

### 3. Hotlist seeding from decode-only profile

**File**: `ds4.c`
**Function**: `metal_graph_streaming_expert_hotlist_load` (line 22956)

No changes needed — the hotlist file format is unchanged. Just generate it with decode-only profiling:

```sh
# Generate decode-biased hotlist
DS4_EXPERT_PROFILE_DECODE_ONLY=1 \
DS4_EXPERT_HOTLIST=/tmp/v41-decode-hotlist.txt \
  ./ds4 -m gguf/DeepSeek-V4.1-Flash-Q2.gguf --ssd-streaming \
  -p "short prompt" -n 2048  # decode-heavy run

# Use it
DS4_STREAM_HOTNESS_DECODE_ONLY=1 \
DS4_METAL_STREAMING_EXPERT_HOTLIST=/tmp/v41-decode-hotlist.txt \
  ./ds4 -m gguf/DeepSeek-V4.1-Flash-Q2.gguf --ssd-streaming --ctx 65536
```

### 4. V4.1 built-in hotlist

**File**: New `ds4_streaming_hotlist_flash41.inc`
**Function**: `metal_graph_streaming_expert_hotlist_load_default` (ds4.c:22907)

Add V4.1 case:

```c
// ds4.c:22919
if (g_ds4_shape.variant == DS4_VARIANT_PRO) {
    hotlist = ds4_default_streaming_hotlist_pro;
    hotlist_count = ...;
} else if (g_ds4_shape.variant == DS4_VARIANT_FLASH) {
    hotlist = ds4_default_streaming_hotlist_flash;
    hotlist_count = ...;
} else if (g_ds4_shape.variant == DS4_VARIANT_FLASH41) {  // NEW
    hotlist = ds4_default_streaming_hotlist_flash41;        // NEW
    hotlist_count = ds4_default_streaming_hotlist_flash41_count;  // NEW
} else if (g_ds4_shape.variant == DS4_VARIANT_GLM52) {
    // ...
}
```

Generate the built-in hotlist from decode-only profiling of representative workloads.

---

## Engram / N-gram Table Interaction

### What Engram tables are (V4.1)

| Property | Value |
|----------|-------|
| Location | `ds4_engram.c`, `ds4_engram.h` |
| Size | 189 GiB (disk-only, never cached) |
| Layers | 2 (layers 1 and 14 in V4.1) — `ds41_engram_layer()` at ds4.c:1392 |
| Purpose | N-gram hash → KV cache row lookup for attention |
| Access | `pread()` on demand, batched with concurrent readers |

From `ds4_engram.h:47`:
> *"A separate uncached file descriptor, never an mmap or Metal model view."*

### Engram vs Expert Routing — they are DIFFERENT

| Aspect | Engram Tables | Routed Experts |
|--------|-------------|----------------|
| **What** | Attention KV cache rows | FFN expert weights |
| **Size** | 189 GiB total | 152 GiB (Q2) / 294 GiB (Q4) main weights |
| **Location** | Always disk-only | SSD streaming cache (RAM + SSD) |
| **Selection** | N-gram hash of recent tokens | Learned router (`ffn_gate_inp`) |
| **V4.1 layers** | 1, 14 | All 40 layers |
| **Hash-based?** | Yes (n-gram hash) | No (learned routing) |

### Does decode-biased hotlist affect Engram?

**Directly: NO.** The Engram tables are:
- Not expert weights — they don't go in the expert cache
- Always disk-only — never cached in RAM regardless of hotlist
- Read via `pread()` — independent of the expert cache eviction policy

**Indirectly: YES, in two ways:**

#### 1. Engram layers still have routed experts

Layers 1 and 14 in V4.1 have **both** Engram attention **and** routed experts (384 experts each). The decode-biased hotlist applies to these experts just like any other layer.

```
Layer 1 (Engram layer):
  ├── Attention: Engram n-gram lookup (disk) + standard attention
  └── FFN: 384 routed experts ← hotlist applies here
```

#### 2. SSD I/O contention

Both Engram reads and expert cache misses hit the same SSD:

```
Decode token:
  ├── Layer 1:  Engram read (if n-gram match)     ← SSD I/O
  ├── Layer 1:  Expert cache lookup/miss          ← SSD I/O if miss
  ├── Layer 2:  Expert cache lookup/miss          ← SSD I/O if miss
  ├── ...
  └── Layer 40: Expert cache lookup/miss          ← SSD I/O if miss
```

A decode-biased hotlist **reduces expert cache misses during decode**, which:
- Frees SSD bandwidth for Engram reads
- Reduces overall decode latency

### Optional: Engram row caching (future work)

The Engram reader (`ds4_engram_read_batch`, ds4_engram.c:242) reads rows on every token. A small LRU cache of recently-accessed Engram rows could help, especially during decode where the same n-gram patterns recur.

However:
- Engram rows are small (264 bytes each)
- 189 GiB table is too large to cache meaningfully
- The OS page cache may already handle this
- **Not part of this plan** — measure first

---

## V4 Flash/Pro Hash Layers (different mechanism)

V4 Flash and V4 Pro have `n_hash_layer = 3` — the first 3 layers use `ffn_gate_tid2eid` for **hash-based expert routing** (token ID → expert ID lookup table).

| Model | Hash layers | Mechanism |
|-------|-------------|-----------|
| V4 Flash | 0, 1, 2 | `ffn_gate_tid2eid` (in-GGUF table) |
| V4 Pro | 0, 1, 2 | `ffn_gate_tid2eid` (in-GGUF table) |
| V4.1 Flash | None | Learned routing only; Engram is for attention, not routing |

The `is_hash` flag in the profiler (ds4.c:1692) tracks hash-routed layers. For V4.1, `is_hash` is always false (no `ffn_gate_tid2eid`).

**Decode-biased hotlist applies to hash-routed layers too** — the hotness gating is independent of routing mechanism.

---

## Test Plan

### Unit tests

1. **Hotness gating test** (`tests/test_decode_hotness.c`):
   - Mock `ds4_gpu_decode_phase()` to return 0 (prefill) then 1 (decode)
   - Verify hotness only increments during decode when `DS4_STREAM_HOTNESS_DECODE_ONLY=1`
   - Verify backward compatibility (default behavior unchanged)

2. **Profiler gating test** (`tests/test_decode_profile.c`):
   - Run with `DS4_EXPERT_PROFILE_DECODE_ONLY=1`
   - Verify prefill records are excluded from histogram
   - Verify hotlist output only contains decode-selected experts

### Integration tests

3. **A/B benchmark** (M5 Max 128GB, V4.1 Q2 SSD streaming):
   ```sh
   # Baseline (current behavior)
   ./ds4-bench -m gguf/DeepSeek-V4.1-Flash-Q2.gguf --ssd-streaming \
     --prompt-file speed-bench/promessi_sposi.txt --ctx-start 4096 --ctx-max 65536 \
     --step-mul 2 --gen-tokens 512 --csv /tmp/baseline.csv

   # Decode-biased
   DS4_STREAM_HOTNESS_DECODE_ONLY=1 \
   DS4_METAL_STREAMING_EXPERT_HOTLIST=/tmp/v41-decode-hotlist.txt \
   ./ds4-bench -m gguf/DeepSeek-V4.1-Flash-Q2.gguf --ssd-streaming \
     --prompt-file speed-bench/promessi_sposi.txt --ctx-start 4096 --ctx-max 65536 \
     --step-mul 2 --gen-tokens 512 --csv /tmp/decode-biased.csv
   ```
   - Compare generation t/s at each context size
   - Compare cache hit rates (from `DS4_METAL_STREAM_EXPERT_CACHE_STATS`)

4. **Agent workload simulation** (50k prefill + 10k decode):
   ```sh
   # Create 50k token prompt (codebase context)
   # Measure: prefill time, first-token latency, decode t/s
   ```
   - Verify first-token latency improves (decode-hot experts in cache)
   - Verify sustained decode t/s improves

### Regression tests

5. **V4 Flash / V4 Pro / GLM 5.2**: Verify no regression with default settings
6. **Resident mode** (no SSD streaming): Verify hotness gating doesn't break anything
7. **Two-Mac TP**: Verify hotness tracking works across ranks

---

## Rollout

| Phase | Change | Risk |
|-------|--------|------|
| 1 | Add `DS4_STREAM_HOTNESS_DECODE_ONLY` env var (default: off) | Low — opt-in |
| 2 | Add `DS4_EXPERT_PROFILE_DECODE_ONLY` env var (default: off) | Low — opt-in |
| 3 | Generate V4.1 built-in hotlist from decode-only profile | Medium — affects default behavior |
| 4 | Make decode-only the default for V4.1 | Medium — changes default |
| 5 | Document in `docs/SSD_STREAMING.md` | Low |

**Recommended**: Ship phases 1–2 first, measure, then decide on 3–4.

---

## Open Questions

1. **Should prefill seeding also be decode-biased?** Currently seeds from prefill-selected experts. Could seed from decode-predicted experts instead (e.g., based on prompt content analysis).

2. **Weighted vs gated?** Gated (discard prefill) is simpler but loses prefill signal. Weighted (e.g., 10:1 decode:prefill) keeps some prefill information. Which is better for mixed workloads?

3. **Multi-turn conversations**: In a long agent conversation, should hotness accumulate across turns, or reset per turn? Current: accumulates (with decay).

4. **Engram row caching**: Should we add a small LRU cache for Engram rows? Measure first.

---

## References

- `ds4_metal.m:1345` — `ds4_gpu_set_decode_phase()` / `ds4_gpu_decode_phase()`
- `ds4_metal.m:14444` — `g_stream_expert_cache_route_hotness` declaration
- `ds4_metal.m:14497` — `ds4_gpu_stream_expert_cache_note_selected_hotness`
- `ds4_metal.m:15601` — LFU+LRU eviction victim selection
- `ds4.c:1687` — `ds4_expert_profile_record`
- `ds4.c:33739` — `metal_graph_seed_streaming_expert_cache_from_prefill`
- `ds4.c:33814` — `metal_graph_seed_streaming_expert_cache_from_hotlist`
- `ds4.c:22907` — `metal_graph_streaming_expert_hotlist_load_default`
- `ds4.c:1392` — `ds41_engram_layer` (V4.1 Engram layers: 1, 14)
- `ds4_engram.h` — Engram table interface
- `ds4_engram.c:242` — `ds4_engram_read_batch` (concurrent pread readers)
- `docs/SSD_STREAMING.md` — SSD streaming design doc
