#include "ds4_kvstore.h"

/* Shared disk KV checkpoint file support.
 *
 * The low-level file layout and payload helpers are intentionally shared.  The
 * ds4-server still owns the automatic byte-prefix cache policy built on top of
 * this file; ds4-agent uses only the same durable format for explicit sessions,
 * with its own policy in ds4_agent.c.  Protocol-specific extras, such as the
 * server's tool-id -> exact DSML trailer, are attached through trailer hooks and
 * still live with the protocol code that owns those mappings. */

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define KV_CACHE_MAGIC0 'K'
#define KV_CACHE_MAGIC1 'V'
#define KV_CACHE_MAGIC2 'C'
#define KV_CACHE_VERSION 1u
#define KV_CACHE_VERSION2 2u
/* Header byte 20 carries the graph-payload ABI.  It is separate from the outer
 * file version because the KVC envelope can remain stable while the serialized
 * ds4_session internals become unsafe to restore across runtime changes. */
#define KV_CACHE_PAYLOAD_ABI 2u
#define KV_CACHE_DEFAULT_MIN_TOKENS 512
#define KV_CACHE_DEFAULT_COLD_MAX_TOKENS 30000
/* Tokenizers may merge text across the prompt boundary. Trimming a small tail
 * still improves the cheap token-prefix path, while text-prefix lookup handles
 * cases where canonical prompt tokenization spells the same bytes differently.
 * The 2048 alignment also matches the backend prefill chunk schedule, which
 * keeps compressor row finalization identical to a cold full prompt. */
#define KV_CACHE_DEFAULT_BOUNDARY_TRIM_TOKENS 32
#define KV_CACHE_DEFAULT_BOUNDARY_ALIGN_TOKENS 2048
#define KV_CACHE_DEFAULT_CONTINUED_INTERVAL_TOKENS 10000

typedef struct {
    char *ptr;
    size_t len;
    size_t cap;
} kv_buf;

static void kv_die(const char *msg) {
    fprintf(stderr, "ds4-kvstore: %s\n", msg);
    exit(1);
}

static void *kv_xmalloc(size_t n) {
    void *p = malloc(n ? n : 1);
    if (!p) kv_die("out of memory");
    return p;
}

static void *kv_xrealloc(void *p, size_t n) {
    p = realloc(p, n ? n : 1);
    if (!p) kv_die("out of memory");
    return p;
}

static char *kv_xstrdup(const char *s) {
    size_t n = strlen(s);
    char *p = kv_xmalloc(n + 1);
    memcpy(p, s, n + 1);
    return p;
}

static void kv_buf_reserve(kv_buf *b, size_t add) {
    if (add > SIZE_MAX - b->len - 1) kv_die("buffer overflow");
    size_t need = b->len + add + 1;
    if (need <= b->cap) return;
    size_t cap = b->cap ? b->cap * 2 : 256;
    while (cap < need) cap *= 2;
    b->ptr = kv_xrealloc(b->ptr, cap);
    b->cap = cap;
}

static void kv_buf_append(kv_buf *b, const void *p, size_t n) {
    kv_buf_reserve(b, n);
    memcpy(b->ptr + b->len, p, n);
    b->len += n;
    b->ptr[b->len] = '\0';
}

static void kv_buf_putc(kv_buf *b, char c) {
    kv_buf_append(b, &c, 1);
}

static void kv_buf_puts(kv_buf *b, const char *s) {
    kv_buf_append(b, s, strlen(s));
}

static void kv_buf_printf(kv_buf *b, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    va_list ap2;
    va_copy(ap2, ap);
    int n = vsnprintf(NULL, 0, fmt, ap);
    va_end(ap);
    if (n < 0) kv_die("vsnprintf failed");
    kv_buf_reserve(b, (size_t)n);
    vsnprintf(b->ptr + b->len, b->cap - b->len, fmt, ap2);
    va_end(ap2);
    b->len += (size_t)n;
}

static char *kv_buf_take(kv_buf *b) {
    if (!b->ptr) return kv_xstrdup("");
    char *p = b->ptr;
    memset(b, 0, sizeof(*b));
    return p;
}

static double kv_now_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1000000000.0;
}

static const char *kv_log_name(const ds4_kvstore *kc) {
    return kc && kc->log_name ? kc->log_name : "ds4";
}

static void kv_logf(ds4_kvstore *kc, ds4_kvstore_log_type type,
                    const char *fmt, ...) {
    if (!kc || !kc->log) return;
    va_list ap;
    va_start(ap, fmt);
    va_list ap2;
    va_copy(ap2, ap);
    int n = vsnprintf(NULL, 0, fmt, ap);
    va_end(ap);
    if (n < 0) {
        va_end(ap2);
        return;
    }
    char *msg = kv_xmalloc((size_t)n + 1);
    vsnprintf(msg, (size_t)n + 1, fmt, ap2);
    va_end(ap2);
    kc->log(kc->log_ud, type, msg);
    free(msg);
}

ds4_kvstore_options ds4_kvstore_default_options(void) {
    return (ds4_kvstore_options){
        .min_tokens = KV_CACHE_DEFAULT_MIN_TOKENS,
        .cold_max_tokens = KV_CACHE_DEFAULT_COLD_MAX_TOKENS,
        .continued_interval_tokens = KV_CACHE_DEFAULT_CONTINUED_INTERVAL_TOKENS,
        .boundary_trim_tokens = KV_CACHE_DEFAULT_BOUNDARY_TRIM_TOKENS,
        .boundary_align_tokens = KV_CACHE_DEFAULT_BOUNDARY_ALIGN_TOKENS,
        .anchor_step = DS4_KVSTORE_DEFAULT_ANCHOR_STEP,
        .small_dense_tokens = DS4_KVSTORE_DEFAULT_SMALL_DENSE,
        .tail_anchors = DS4_KVSTORE_DEFAULT_TAIL_ANCHORS,
        .mid_spacing_tokens = DS4_KVSTORE_DEFAULT_MID_SPACING,
        .min_anchors = DS4_KVSTORE_DEFAULT_MIN_ANCHORS,
        .max_conversations = 0,
    };
}

uint8_t ds4_kvstore_reason_code(const char *reason) {
    if (!reason) return DS4_KVSTORE_REASON_UNKNOWN;
    if (!strcmp(reason, "cold")) return DS4_KVSTORE_REASON_COLD;
    if (!strcmp(reason, "continued")) return DS4_KVSTORE_REASON_CONTINUED;
    if (!strcmp(reason, "evict")) return DS4_KVSTORE_REASON_EVICT;
    if (!strcmp(reason, "shutdown")) return DS4_KVSTORE_REASON_SHUTDOWN;
    if (!strcmp(reason, "agent-system")) return DS4_KVSTORE_REASON_AGENT_SYSTEM;
    if (!strcmp(reason, "agent-session")) return DS4_KVSTORE_REASON_AGENT_SESSION;
    return DS4_KVSTORE_REASON_UNKNOWN;
}

const char *ds4_kvstore_key_kind(uint8_t ext_flags) {
    if (ext_flags & DS4_KVSTORE_EXT_RESPONSES_VISIBLE) return "responses-visible";
    if (ext_flags & DS4_KVSTORE_EXT_THINKING_VISIBLE) return "thinking-visible";
    return "token-text";
}

void ds4_kvstore_le_put32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static void kv_le_put64(uint8_t *p, uint64_t v) {
    for (int i = 0; i < 8; i++) p[i] = (uint8_t)(v >> (8 * i));
}

uint32_t ds4_kvstore_le_get32(const uint8_t *p) {
    return (uint32_t)p[0] |
           ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

static uint64_t kv_le_get64(const uint8_t *p) {
    uint64_t v = 0;
    for (int i = 7; i >= 0; i--) v = (v << 8) | p[i];
    return v;
}

typedef struct {
    uint32_t h[5];
    uint64_t bytes;
    uint8_t block[64];
    size_t used;
} sha1_ctx;

static uint32_t rol32(uint32_t v, int n) {
    return (v << n) | (v >> (32 - n));
}

static void sha1_transform(sha1_ctx *c, const uint8_t block[64]) {
    uint32_t w[80];
    for (int i = 0; i < 16; i++) {
        w[i] = ((uint32_t)block[i * 4] << 24) |
               ((uint32_t)block[i * 4 + 1] << 16) |
               ((uint32_t)block[i * 4 + 2] << 8) |
               (uint32_t)block[i * 4 + 3];
    }
    for (int i = 16; i < 80; i++)
        w[i] = rol32(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);

    uint32_t a = c->h[0], b = c->h[1], d = c->h[3], e = c->h[4];
    uint32_t cc = c->h[2];
    for (int i = 0; i < 80; i++) {
        uint32_t f, k;
        if (i < 20) {
            f = (b & cc) | ((~b) & d);
            k = 0x5a827999u;
        } else if (i < 40) {
            f = b ^ cc ^ d;
            k = 0x6ed9eba1u;
        } else if (i < 60) {
            f = (b & cc) | (b & d) | (cc & d);
            k = 0x8f1bbcdcu;
        } else {
            f = b ^ cc ^ d;
            k = 0xca62c1d6u;
        }
        uint32_t tmp = rol32(a, 5) + f + e + k + w[i];
        e = d;
        d = cc;
        cc = rol32(b, 30);
        b = a;
        a = tmp;
    }
    c->h[0] += a;
    c->h[1] += b;
    c->h[2] += cc;
    c->h[3] += d;
    c->h[4] += e;
}

static void sha1_init(sha1_ctx *c) {
    c->h[0] = 0x67452301u;
    c->h[1] = 0xefcdab89u;
    c->h[2] = 0x98badcfeu;
    c->h[3] = 0x10325476u;
    c->h[4] = 0xc3d2e1f0u;
    c->bytes = 0;
    c->used = 0;
}

static void sha1_update(sha1_ctx *c, const void *ptr, size_t len) {
    const uint8_t *p = ptr;
    c->bytes += len;
    while (len != 0) {
        size_t n = 64 - c->used;
        if (n > len) n = len;
        memcpy(c->block + c->used, p, n);
        c->used += n;
        p += n;
        len -= n;
        if (c->used == 64) {
            sha1_transform(c, c->block);
            c->used = 0;
        }
    }
}

static void sha1_final(sha1_ctx *c, uint8_t out[20]) {
    uint64_t bits = c->bytes * 8;
    uint8_t one = 0x80;
    uint8_t zero = 0;
    sha1_update(c, &one, 1);
    while (c->used != 56) sha1_update(c, &zero, 1);
    uint8_t len[8];
    for (int i = 0; i < 8; i++) len[7 - i] = (uint8_t)(bits >> (8 * i));
    sha1_update(c, len, sizeof(len));
    for (int i = 0; i < 5; i++) {
        out[i * 4] = (uint8_t)(c->h[i] >> 24);
        out[i * 4 + 1] = (uint8_t)(c->h[i] >> 16);
        out[i * 4 + 2] = (uint8_t)(c->h[i] >> 8);
        out[i * 4 + 3] = (uint8_t)c->h[i];
    }
}

static void hex20(const uint8_t in[20], char out[41]) {
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < 20; i++) {
        out[i * 2] = hex[in[i] >> 4];
        out[i * 2 + 1] = hex[in[i] & 15];
    }
    out[40] = '\0';
}

void ds4_kvstore_sha1_bytes_hex(const void *ptr, size_t len, char out[41]) {
    sha1_ctx c;
    sha1_init(&c);
    sha1_update(&c, ptr, len);
    uint8_t digest[20];
    sha1_final(&c, digest);
    hex20(digest, out);
}

/* A conversation's HEAD (system + early turns) is stable across its own turns
 * and across compaction; new sessions have a different head.  Hash the text up
 * to MAX_BYTES and fold in the weight fingerprint so distinct namespaces cannot
 * collide.  Hashing [0, min(text_len, MAX_BYTES)) makes every anchor of one
 * session that is long enough to include the cap cluster to one conv_id (since
 * anchors are byte-prefixes of one another), while two sessions sharing a head
 * but diverging before MAX_BYTES get distinct conv_ids.  Short anchors
 * (< MAX_BYTES) hash their full text and fall into their own/shared conv, which
 * is fine because they are small_dense (always kept) or loadable via
 * conv-agnostic prefix matching.  Content-based, so it works for GLM and other
 * models. */
uint64_t ds4_kvstore_compute_conv_id(const char *text, size_t text_len,
                                     uint64_t model_fp) {
    size_t n = text_len;
    if (n > DS4_KVSTORE_CONV_ID_MAX_BYTES) n = DS4_KVSTORE_CONV_ID_MAX_BYTES;
    uint8_t digest[20];
    sha1_ctx c;
    sha1_init(&c);
    sha1_update(&c, text ? text : "", n);
    sha1_final(&c, digest);
    uint64_t conv = kv_le_get64(digest);
    conv ^= model_fp * 0x9E3779B97F4A7C15ull;
    /* conv_id==0 is reserved for legacy singletons; a genuine conversation that
     * hashes to exactly 0 (~2^-64) must not be mistaken for one. */
    if (conv == 0) conv = 1;
    return conv;
}

/* Weight/model fingerprint for cross-model KV routing.  The cache key must
 * reflect WEIGHTS + TOKENIZER + ARCH, NOT the protocol name/temp, so
 * byte-compatible builds (0731 vs v2) share one fingerprint and reuse KV.
 * Cheap and stable across restarts: hash the model FILE identity
 * (path + size + mtime).  A real weight swap changes size/mtime -> new fp ->
 * cross-model reject.  Returns 0 (legacy-accept mode) if the path is absent. */
uint64_t ds4_kvstore_model_fingerprint(const char *model_path) {
    if (!model_path || !model_path[0]) return 0;
    struct stat st;
    if (stat(model_path, &st) != 0) return 0;
    uint64_t fp = 0;
    for (const char *p = model_path; *p; p++) {
        fp = (fp * 0x100000001B3ull) ^ (unsigned char)*p;
    }
    fp = (fp * 0x9E3779B97F4A7C15ull) ^ (uint64_t)st.st_size;
    fp = (fp * 0x9E3779B97F4A7C15ull) ^ (uint64_t)st.st_mtime;
    return fp;
}

bool ds4_kvstore_sha_hex_name(const char *name, char sha[41]) {
    if (strlen(name) != 43 || strcmp(name + 40, ".kv")) return false;
    for (int i = 0; i < 40; i++) {
        if (!isxdigit((unsigned char)name[i])) return false;
        sha[i] = (char)tolower((unsigned char)name[i]);
    }
    sha[40] = '\0';
    return true;
}

char *ds4_kvstore_path_join(const char *dir, const char *name) {
    kv_buf b = {0};
    kv_buf_puts(&b, dir);
    if (b.len == 0 || b.ptr[b.len - 1] != '/') kv_buf_putc(&b, '/');
    kv_buf_puts(&b, name);
    return kv_buf_take(&b);
}

char *ds4_kvstore_path_for_sha(ds4_kvstore *kc, const char sha[41]) {
    char name[44];
    memcpy(name, sha, 40);
    memcpy(name + 40, ".kv", 4);
    return ds4_kvstore_path_join(kc->dir, name);
}

static bool kv_mkdir_p(const char *path) {
    if (!path || !path[0]) return false;
    char *tmp = kv_xstrdup(path);
    for (char *p = tmp + 1; *p; p++) {
        if (*p != '/') continue;
        *p = '\0';
        if (mkdir(tmp, 0700) != 0 && errno != EEXIST) {
            free(tmp);
            return false;
        }
        *p = '/';
    }
    bool ok = mkdir(tmp, 0700) == 0 || errno == EEXIST;
    free(tmp);
    return ok;
}

void ds4_kvstore_entry_free(ds4_kvstore_entry *e) {
    free(e->path);
    memset(e, 0, sizeof(*e));
}

void ds4_kvstore_clear(ds4_kvstore *kc) {
    for (int i = 0; i < kc->len; i++) ds4_kvstore_entry_free(&kc->entry[i]);
    free(kc->entry);
    kc->entry = NULL;
    kc->len = 0;
    kc->cap = 0;
}

static void kv_cache_push(ds4_kvstore *kc, ds4_kvstore_entry e) {
    if (kc->len == kc->cap) {
        kc->cap = kc->cap ? kc->cap * 2 : 16;
        kc->entry = kv_xrealloc(kc->entry, (size_t)kc->cap * sizeof(kc->entry[0]));
    }
    kc->entry[kc->len++] = e;
}

void ds4_kvstore_fill_header(uint8_t h[DS4_KVSTORE_FIXED_HEADER],
                             uint8_t model_id, uint8_t quant_bits,
                             uint8_t reason, uint8_t ext_flags,
                             uint32_t tokens, uint32_t hits, uint32_t ctx_size,
                             uint64_t created_at, uint64_t last_used,
                             uint64_t payload_bytes) {
    memset(h, 0, DS4_KVSTORE_FIXED_HEADER);
    h[0] = KV_CACHE_MAGIC0;
    h[1] = KV_CACHE_MAGIC1;
    h[2] = KV_CACHE_MAGIC2;
    h[3] = KV_CACHE_VERSION;
    h[4] = quant_bits;
    h[5] = reason;
    h[6] = ext_flags;
    h[7] = model_id;
    ds4_kvstore_le_put32(h + 8, tokens);
    ds4_kvstore_le_put32(h + 12, hits);
    ds4_kvstore_le_put32(h + 16, ctx_size);
    h[20] = KV_CACHE_PAYLOAD_ABI;
    kv_le_put64(h + 24, created_at);
    kv_le_put64(h + 32, last_used);
    kv_le_put64(h + 40, payload_bytes);
}

/* v2 header: the base 48 bytes (identical to v1) plus 24 extra bytes carrying
 * the conversation lineage, weight fingerprint, bucket, and halving level. */
void ds4_kvstore_fill_header_v2(
        uint8_t h[DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V2_EXTRA],
        uint8_t model_id, uint8_t quant_bits, uint8_t reason, uint8_t ext_flags,
        uint32_t tokens, uint32_t hits, uint32_t ctx_size,
        uint64_t created_at, uint64_t last_used, uint64_t payload_bytes,
        uint64_t conv_id, uint64_t model_fp, uint32_t bucket, uint8_t level,
        bool stale) {
    ds4_kvstore_fill_header(h, model_id, quant_bits, reason, ext_flags,
                            tokens, hits, ctx_size, created_at, last_used,
                            payload_bytes);
    /* Zero the whole 24-byte extension so the reserved bytes (70-71) are
     * deterministic on disk, not indeterminate stack content. */
    memset(h + DS4_KVSTORE_FIXED_HEADER, 0, DS4_KVSTORE_HEADER_V2_EXTRA);
    h[3] = KV_CACHE_VERSION2;
    kv_le_put64(h + 48, conv_id);
    kv_le_put64(h + 56, model_fp);
    ds4_kvstore_le_put32(h + 64, bucket);
    h[68] = level;
    h[69] = stale ? 1u : 0u;
}

bool ds4_kvstore_read_header(FILE *fp, ds4_kvstore_entry *e,
                             uint32_t *text_bytes) {
    uint8_t h[DS4_KVSTORE_FIXED_HEADER];
    if (fread(h, 1, sizeof(h), fp) != sizeof(h)) return false;
    if (h[0] != KV_CACHE_MAGIC0 || h[1] != KV_CACHE_MAGIC1 ||
        h[2] != KV_CACHE_MAGIC2) return false;
    if (h[3] != KV_CACHE_VERSION && h[3] != KV_CACHE_VERSION2) return false;
    if (h[20] != KV_CACHE_PAYLOAD_ABI) return false;
    const uint8_t version = h[3];
    e->quant_bits = h[4];
    e->reason = h[5] <= DS4_KVSTORE_REASON_AGENT_SESSION ? h[5] :
                DS4_KVSTORE_REASON_UNKNOWN;
    e->ext_flags = h[6];
    e->model_id = h[7];
    e->tokens = ds4_kvstore_le_get32(h + 8);
    e->hits = ds4_kvstore_le_get32(h + 12);
    e->ctx_size = ds4_kvstore_le_get32(h + 16);
    e->created_at = kv_le_get64(h + 24);
    e->last_used = kv_le_get64(h + 32);
    e->payload_bytes = kv_le_get64(h + 40);
    e->hdr_version = version;
    if (version == KV_CACHE_VERSION2) {
        uint8_t x[DS4_KVSTORE_HEADER_V2_EXTRA];
        if (fread(x, 1, sizeof(x), fp) != sizeof(x)) return false;
        e->conv_id = kv_le_get64(x);
        e->model_fp = kv_le_get64(x + 8);
        e->bucket = ds4_kvstore_le_get32(x + 16);
        e->level = x[20];
        e->stale = x[21] != 0;
    } else {
        /* v1 header: no conversation metadata.  conv_id==0 marks a legacy
         * singleton that is always kept (never redundant) and exits only via
         * the legacy-LRU path keyed on last_used, so bucket/level are unused and
         * left at zero rather than derived. */
        e->conv_id = 0;
        e->model_fp = 0;
        e->bucket = 0;
        e->level = 0;
        e->stale = false;
    }
    uint8_t tb[4];
    if (fread(tb, 1, sizeof(tb), fp) != sizeof(tb)) return false;
    *text_bytes = ds4_kvstore_le_get32(tb);
    e->text_bytes = *text_bytes;
    return e->tokens != 0 && (e->quant_bits == 2 || e->quant_bits == 4);
}

bool ds4_kvstore_read_entry_file(const char *path, const char sha[41],
                                 ds4_kvstore_entry *out) {
    struct stat st;
    if (stat(path, &st) != 0 ||
        st.st_size < (off_t)(DS4_KVSTORE_FIXED_HEADER + 4))
        return false;
    FILE *fp = fopen(path, "rb");
    if (!fp) return false;
    ds4_kvstore_entry e = {0};
    uint32_t text_bytes = 0;
    bool ok = ds4_kvstore_read_header(fp, &e, &text_bytes);
    fclose(fp);
    if (!ok) return false;
    const uint64_t hdr_total =
        DS4_KVSTORE_FIXED_HEADER + 4ull +
        (e.hdr_version == KV_CACHE_VERSION2 ? DS4_KVSTORE_HEADER_V2_EXTRA : 0ull);
    if (UINT64_MAX - hdr_total < (uint64_t)text_bytes ||
        UINT64_MAX - hdr_total - (uint64_t)text_bytes < e.payload_bytes)
        return false;
    const uint64_t expected = hdr_total + (uint64_t)text_bytes + e.payload_bytes;
    if ((uint64_t)st.st_size < expected) return false;
    memcpy(e.sha, sha, 41);
    e.path = kv_xstrdup(path);
    e.file_size = (uint64_t)st.st_size;
    *out = e;
    return true;
}

static void kv_cache_refresh(ds4_kvstore *kc) {
    if (!kc->enabled) return;
    ds4_kvstore_clear(kc);
    DIR *d = opendir(kc->dir);
    if (!d) return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL) {
        char sha[41];
        if (!ds4_kvstore_sha_hex_name(de->d_name, sha)) continue;
        char *path = ds4_kvstore_path_join(kc->dir, de->d_name);
        ds4_kvstore_entry e = {0};
        if (ds4_kvstore_read_entry_file(path, sha, &e)) kv_cache_push(kc, e);
        free(path);
    }
    closedir(d);
}

bool ds4_kvstore_touch_file(const char *path, uint32_t hits, bool stale,
                            uint64_t last_used) {
    FILE *fp = fopen(path, "r+b");
    if (!fp) return false;
    ds4_kvstore_entry e = {0};
    uint32_t text_bytes = 0;
    bool ok = ds4_kvstore_read_header(fp, &e, &text_bytes);
    if (ok) {
        if (last_used == 0) last_used = (uint64_t)time(NULL);
        const size_t hdr_len =
            (e.hdr_version == KV_CACHE_VERSION2)
                ? (DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V2_EXTRA)
                : DS4_KVSTORE_FIXED_HEADER;
        uint8_t h[DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V2_EXTRA];
        if (e.hdr_version == KV_CACHE_VERSION2) {
            ds4_kvstore_fill_header_v2(h, e.model_id, e.quant_bits, e.reason,
                                       e.ext_flags, e.tokens, hits, e.ctx_size,
                                       e.created_at, last_used, e.payload_bytes,
                                       e.conv_id, e.model_fp, e.bucket, e.level,
                                       stale);
        } else {
            ds4_kvstore_fill_header(h, e.model_id, e.quant_bits, e.reason,
                                    e.ext_flags, e.tokens, hits, e.ctx_size,
                                    e.created_at, last_used, e.payload_bytes);
        }
        ok = fseek(fp, 0, SEEK_SET) == 0 &&
             fwrite(h, 1, hdr_len, fp) == hdr_len;
    }
    fclose(fp);
    return ok;
}

/* ---- Conversation-scoped retention (PLAN §3, IMPLEMENTATION-PLAN B8/B9) ----
 * The keep-set/redundant/LRU helpers are O(entries) each and re-scanned per
 * eviction pass (O(entries^2) overall).  Checkpoint counts are bounded by the
 * disk budget (tens to low hundreds), so this is comfortably cheap; revisit
 * only if counts grow large. */

/* Is entry idx part of its conversation's keep-set?  Keep = dense small
 * (<= small_dense_tokens) + the frontier (always) + dense tail (frontier +
 * tail_anchors-1 below) + sparse middle (the largest anchor in each
 * mid_spacing * 2^level window).  Legacy conv_id==0 singletons are always kept
 * (never redundant). */
static bool kv_cache_conv_is_kept(ds4_kvstore *kc, int idx) {
    const ds4_kvstore_entry *e = &kc->entry[idx];
    if (e->conv_id == 0) return true;
    if (e->tokens <= (uint32_t)kc->opt.small_dense_tokens) return true;

    uint32_t frontier = 0;
    int rank = 0; /* entries in this conv with strictly more tokens */
    for (int i = 0; i < kc->len; i++) {
        const ds4_kvstore_entry *o = &kc->entry[i];
        if (o->conv_id != e->conv_id) continue;
        if (o->tokens > frontier) frontier = o->tokens;
        if (o->tokens > e->tokens) rank++;
    }
    /* The frontier is always kept, even under degenerate config
     * (tail_anchors==0 and mid_spacing==0 would otherwise leave it unprotected
     * and evictable, defeating the active-conversation guarantee). */
    if (e->tokens >= frontier) return true;
    /* Dense tail: the tail_anchors largest anchors of the conversation. */
    if (rank < kc->opt.tail_anchors) return true;
    /* Sparse middle: keep the LARGEST anchor in each mid_spacing·2^level
     * window.  Windowing (not an exact (frontier-tokens)%spacing grid) is what
     * guarantees one surviving anchor per window regardless of how the
     * continued-store interval aligns, so a deep divergence rebuilds at most
     * one window — the plan's "keep every MID_SPACING" goal. */
    uint64_t window = (uint64_t)kc->opt.mid_spacing_tokens *
                      (1ull << (e->level > 63 ? 63 : e->level));
    if (window > 0) {
        uint64_t my_win = (uint64_t)e->tokens / window;
        bool largest_in_window = true;
        for (int i = 0; i < kc->len; i++) {
            const ds4_kvstore_entry *o = &kc->entry[i];
            if (o->conv_id != e->conv_id) continue;
            if (o->tokens <= (uint32_t)kc->opt.small_dense_tokens) continue;
            if ((uint64_t)o->tokens / window == my_win && o->tokens > e->tokens) {
                largest_in_window = false;
                break;
            }
        }
        if (largest_in_window) return true;
    }
    return false;
}

/* Pick a redundant victim deterministically: the redundant entry with the
 * smallest bucket (oldest position first — plan §3.3 "oldest bucket first
 * within a conversation"), tie-broken by tokens.  Evicting smallest-first keeps
 * the larger middle anchors, which are the useful ones for deep divergences,
 * and avoids readdir-order dependence. */
static int kv_cache_find_redundant(ds4_kvstore *kc) {
    int best = -1;
    for (int i = 0; i < kc->len; i++) {
        if (kv_cache_conv_is_kept(kc, i)) continue;
        if (best < 0) { best = i; continue; }
        const ds4_kvstore_entry *b = &kc->entry[best];
        if (kc->entry[i].bucket < b->bucket ||
            (kc->entry[i].bucket == b->bucket &&
             kc->entry[i].tokens < b->tokens))
            best = i;
    }
    return best;
}

/* LRU conversation (excluding the active one and legacy conv_id==0) whose
 * member count is >= min_count.  Returns 0 if none.  A conversation's recency is
 * the MOST-recent touch among its members (max last_used); the least-recently-
 * active conversation has the smallest such recency.  Using max (not min) stops
 * a long-running conversation's old small anchor from pinning its rank to
 * creation time and getting it halved/retired ahead of a genuinely idle one. */
static uint64_t kv_cache_find_lru_conv(ds4_kvstore *kc, uint64_t active_conv,
                                       int min_count) {
    uint64_t best = 0;
    int64_t lru_recency = INT64_MAX;
    for (int i = 0; i < kc->len; i++) {
        uint64_t c = kc->entry[i].conv_id;
        if (c == 0 || c == active_conv) continue;
        bool seen = false;
        for (int j = 0; j < i; j++)
            if (kc->entry[j].conv_id == c) { seen = true; break; }
        if (seen) continue;
        int cnt = 0;
        int64_t conv_recency = 0;
        for (int j = 0; j < kc->len; j++) {
            if (kc->entry[j].conv_id != c) continue;
            cnt++;
            int64_t lu = (int64_t)(kc->entry[j].last_used ?
                                   kc->entry[j].last_used :
                                   kc->entry[j].created_at);
            if (lu > conv_recency) conv_recency = lu;
        }
        if (cnt < min_count) continue;
        if (conv_recency < lru_recency) { lru_recency = conv_recency; best = c; }
    }
    return best;
}

static int kv_cache_count_distinct_convs(ds4_kvstore *kc, uint64_t active_conv) {
    int n = 0;
    for (int i = 0; i < kc->len; i++) {
        uint64_t c = kc->entry[i].conv_id;
        if (c == 0 || c == active_conv) continue;
        bool seen = false;
        for (int j = 0; j < i; j++)
            if (kc->entry[j].conv_id == c) { seen = true; break; }
        if (!seen) n++;
    }
    return n;
}

static int64_t kv_cache_entry_last_used(const ds4_kvstore_entry *e) {
    return (int64_t)(e->last_used ? e->last_used : e->created_at);
}

/* Most-recent last_used across a conversation's members (its last activity). */
static int64_t kv_cache_conv_recency(ds4_kvstore *kc, uint64_t conv_id) {
    int64_t recency = 0;
    for (int i = 0; i < kc->len; i++) {
        if (kc->entry[i].conv_id != conv_id) continue;
        int64_t lu = kv_cache_entry_last_used(&kc->entry[i]);
        if (lu > recency) recency = lu;
    }
    return recency;
}

/* Can halving this conversation still make a kept middle anchor redundant?
 * Halving doubles the middle window; it only merges windows (and thus creates
 * redundancy) while more than one window spans the conversation.  Once
 * window >= frontier everything sits in one window and halving is pointless —
 * retire instead.  This bounds PHASE B to ~log2(frontier/mid_spacing) bumps
 * rather than up to level 255. */
static bool kv_cache_conv_can_halve(ds4_kvstore *kc, uint64_t conv_id) {
    if (kc->opt.mid_spacing_tokens <= 0) return false;
    uint32_t frontier = 0;
    uint8_t level = 0;
    bool has_middle = false;
    for (int i = 0; i < kc->len; i++) {
        if (kc->entry[i].conv_id != conv_id) continue;
        if (kc->entry[i].tokens > frontier) frontier = kc->entry[i].tokens;
        if (kc->entry[i].level > level) level = kc->entry[i].level;
        if (kc->entry[i].tokens > (uint32_t)kc->opt.small_dense_tokens)
            has_middle = true;
    }
    if (!has_middle) return false;
    uint64_t window = (uint64_t)kc->opt.mid_spacing_tokens *
                      (1ull << (level > 63 ? 63 : level));
    return window < frontier;
}

/* Index of the least-recently-used legacy singleton (conv_id==0), or -1. */
static int kv_cache_find_lru_legacy(ds4_kvstore *kc) {
    int best = -1;
    int64_t oldest = INT64_MAX;
    for (int i = 0; i < kc->len; i++) {
        if (kc->entry[i].conv_id != 0) continue;
        int64_t lu = kv_cache_entry_last_used(&kc->entry[i]);
        if (lu < oldest || (lu == oldest && best < 0)) { oldest = lu; best = i; }
    }
    return best;
}

static void kv_cache_unlink_entry(ds4_kvstore *kc, int victim,
                                  uint64_t *total, const char *reason) {
    ds4_kvstore_entry e = kc->entry[victim];
    bool freed = unlink(e.path) == 0;
    if (!freed && errno == ENOENT) freed = true; /* already gone: space is free */
    if (freed) {
        kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                "%s: kv cache evicted reason=%s conv=%llu level=%u tokens=%u hits=%u size=%.2f MiB file=%s",
                kv_log_name(kc), reason,
                (unsigned long long)e.conv_id, (unsigned)e.level,
                e.tokens, e.hits,
                (double)e.file_size / (1024.0 * 1024.0),
                e.path ? e.path : "?");
        if (*total >= e.file_size) *total -= e.file_size;
        else *total = 0;
    } else {
        /* Could not remove the file (permissions, etc.).  Do NOT zero the whole
         * budget total — that aborted the pass even when far over budget.  Drop
         * the in-memory entry so this pass does not re-pick it (a directory
         * refresh re-adds it); the file stays on disk until then. */
        kv_logf(kc, DS4_KVSTORE_LOG_WARNING,
                "%s: kv cache failed to evict reason=%s file=%s: %s (left on disk)",
                kv_log_name(kc), reason, e.path ? e.path : "?", strerror(errno));
    }
    ds4_kvstore_entry_free(&e);
    memmove(kc->entry + victim, kc->entry + victim + 1,
            (size_t)(kc->len - victim - 1) * sizeof(kc->entry[0]));
    kc->len--;
}

static void kv_cache_retire_conv(ds4_kvstore *kc, uint64_t conv_id,
                                 uint64_t *total) {
    for (int i = 0; i < kc->len; ) {
        if (kc->entry[i].conv_id == conv_id) {
            kv_cache_unlink_entry(kc, i, total, "conversation-retired");
        } else {
            i++;
        }
    }
}

void ds4_kvstore_evict(ds4_kvstore *kc, const ds4_tokens *live,
                       uint64_t extra_bytes,
                       const ds4_kvstore_eviction_context *incoming) {
    (void)live;
    if (!kc->enabled || kc->budget_bytes == 0) return;
    if (extra_bytes > kc->budget_bytes) return;
    kv_cache_refresh(kc);
    uint64_t total = 0;
    for (int i = 0; i < kc->len; i++) total += kc->entry[i].file_size;
    const uint64_t target = kc->budget_bytes - extra_bytes;

    uint64_t active_conv = 0;
    if (incoming && incoming->text)
        active_conv = ds4_kvstore_compute_conv_id(incoming->text,
                                                  incoming->text_len,
                                                  kc->model_fp);

    /* Over-cap retirement: retire the LRU conversation whenever the distinct
     * non-active conversation count exceeds the configured cap. */
    if (kc->opt.max_conversations > 0) {
        while (kv_cache_count_distinct_convs(kc, active_conv) >
               kc->opt.max_conversations) {
            uint64_t lru = kv_cache_find_lru_conv(kc, active_conv, 1);
            if (lru == 0) break;
            kv_cache_retire_conv(kc, lru, &total);
        }
    }

    while (total > target && kc->len > 0) {
        /* PHASE A: drop redundant (applies to all convs incl. the active one —
         * its frontier+tail+small are always in the keep-set, so only its
         * redundant middle/compaction-collapse is dropped here).  The stale
         * layer is decommissioned and inert. */
        int victim = kv_cache_find_redundant(kc);
        if (victim >= 0) {
            kv_cache_unlink_entry(kc, victim, &total, "redundant");
            continue;
        }
        /* PHASE B: nothing redundant left — halve the LRU non-active
         * conversation's large-middle spacing (double mid_spacing via level).
         * Only halve a conversation that actually has large-middle anchors;
         * one with only small-dense + tail is already at its floor and falls
         * through to retirement. */
        int halve_min_count = kc->opt.min_anchors >= INT_MAX
            ? INT_MAX : kc->opt.min_anchors + 1;
        uint64_t conv = kv_cache_find_lru_conv(kc, active_conv, halve_min_count);
        if (conv != 0 && kv_cache_conv_can_halve(kc, conv)) {
            bool bumped = false;
            for (int i = 0; i < kc->len; i++) {
                if (kc->entry[i].conv_id != conv) continue;
                /* Small-dense anchors are kept regardless of level; bumping them
                 * would only waste a header rewrite. */
                if (kc->entry[i].tokens <= (uint32_t)kc->opt.small_dense_tokens)
                    continue;
                if (kc->entry[i].level < 255) {
                    kc->entry[i].level++;
                    bumped = true;
                    ds4_kvstore_touch_file(kc->entry[i].path,
                                           kc->entry[i].hits,
                                           kc->entry[i].stale,
                                           kc->entry[i].last_used);
                }
            }
            if (bumped) continue; /* next pass evicts newly-redundant middle */
            /* all anchors already at max level -> fall through to retire */
        }
        /* PHASE C: retire the globally least-recently-used non-active
         * conversation at its floor, or a legacy singleton — whichever is
         * older.  Legacy conv_id==0 files are never redundant, but they must
         * still yield under budget pressure (migration one-time cost). */
        conv = kv_cache_find_lru_conv(kc, active_conv, 1);
        int64_t conv_recency = conv ? kv_cache_conv_recency(kc, conv) : INT64_MAX;
        int legacy = kv_cache_find_lru_legacy(kc);
        int64_t legacy_lu = legacy >= 0 ?
            kv_cache_entry_last_used(&kc->entry[legacy]) : INT64_MAX;
        if (conv == 0 && legacy < 0) break; /* PHASE D: active at floor -> stop */
        if (legacy >= 0 && (conv == 0 || legacy_lu <= conv_recency)) {
            kv_cache_unlink_entry(kc, legacy, &total, "legacy-lru");
            continue;
        }
        kv_cache_retire_conv(kc, conv, &total);
        continue;
    }
}

static int kv_cache_continued_step(const ds4_kvstore *kc);

/* Crash-orphaned store temps (<sha>.kv.tmp.<pid>) never match the <sha>.kv
 * name filter in kv_cache_refresh, so they would accumulate forever.  Reap the
 * old ones at open; an age guard keeps a concurrent writer's in-flight temp
 * untouched. */
static void kv_cache_sweep_orphan_temps(ds4_kvstore *kc) {
    DIR *d = opendir(kc->dir);
    if (!d) return;
    const uint64_t now = (uint64_t)time(NULL);
    struct dirent *de;
    while ((de = readdir(d)) != NULL) {
        if (!strstr(de->d_name, ".kv.tmp.")) continue;
        char *path = ds4_kvstore_path_join(kc->dir, de->d_name);
        struct stat st;
        if (stat(path, &st) == 0 && st.st_mtime > 0 &&
            (uint64_t)st.st_mtime + 3600ull <= now) {
            if (unlink(path) == 0)
                kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                        "%s: kv cache reaped orphaned temp %s",
                        kv_log_name(kc), path);
        }
        free(path);
    }
    closedir(d);
}

bool ds4_kvstore_open(ds4_kvstore *kc, const char *dir, uint64_t budget_mb,
                      bool reject_different_quant, uint64_t model_fp,
                      ds4_kvstore_options opt,
                      const char *log_name,
                      void (*log)(void *ud, ds4_kvstore_log_type type, const char *msg),
                      void *log_ud) {
    memset(kc, 0, sizeof(*kc));
    if (!dir) return false;
    kc->log_name = log_name;
    kc->log = log;
    kc->log_ud = log_ud;
    if (!kv_mkdir_p(dir)) {
        kv_logf(kc, DS4_KVSTORE_LOG_DEFAULT,
                "%s: failed to create KV cache directory %s: %s",
                kv_log_name(kc), dir, strerror(errno));
        return false;
    }
    kc->enabled = true;
    kc->dir = kv_xstrdup(dir);
    if (budget_mb == 0) budget_mb = DS4_KVSTORE_DEFAULT_MB;
    kc->budget_bytes = budget_mb * 1024ull * 1024ull;
    kc->reject_different_quant = reject_different_quant;
    kc->model_fp = model_fp;
    kc->opt = opt;
    if (kc->opt.anchor_step < 0) {
        kv_logf(kc, DS4_KVSTORE_LOG_WARNING,
                "%s: kv cache anchor_step=%d is negative; using continued_interval fallback",
                kv_log_name(kc), kc->opt.anchor_step);
        kc->opt.anchor_step = 0;
    }
    if (kc->opt.anchor_step == 0)
        kv_logf(kc, DS4_KVSTORE_LOG_WARNING,
                "%s: kv cache anchor_step=0; continued checkpoints fall back to continued_interval spacing (dense-small anchors may not land)",
                kv_log_name(kc));
    if (kc->opt.mid_spacing_tokens == 0)
        kv_logf(kc, DS4_KVSTORE_LOG_WARNING,
                "%s: kv cache mid_spacing=0; all large-middle anchors are treated as redundant",
                kv_log_name(kc));
    if (kc->opt.tail_anchors == 0)
        kv_logf(kc, DS4_KVSTORE_LOG_WARNING,
                "%s: kv cache tail_anchors=0; only the frontier anchor is kept dense",
                kv_log_name(kc));
    if ((unsigned)kc->opt.min_tokens < 512)
        kv_logf(kc, DS4_KVSTORE_LOG_WARNING,
                "%s: kv cache min_tokens=%d; anchors shorter than min_tokens are never cached",
                kv_log_name(kc), kc->opt.min_tokens);
    kv_cache_sweep_orphan_temps(kc);
    ds4_kvstore_evict(kc, NULL, 0, NULL);
    kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
            "%s: KV disk cache %s (budget=%llu MiB, cross-quant=%s, min=%d, cold_max=%d, continued=%d, trim=%d, align=%d, hit_half_life=%llus)",
            kv_log_name(kc),
            kc->dir,
            (unsigned long long)(kc->budget_bytes / (1024ull * 1024ull)),
            reject_different_quant ? "reject" : "accept",
            kc->opt.min_tokens,
            kc->opt.cold_max_tokens,
            kc->opt.continued_interval_tokens,
            kc->opt.boundary_trim_tokens,
            kc->opt.boundary_align_tokens,
            (unsigned long long)DS4_KVSTORE_HIT_HALF_LIFE_SECONDS);
    kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
            "%s: KV retention anchor_step=%d continued_step=%d prefill_chunk=%d small_dense=%d tail=%d mid_spacing=%d min_anchors=%d max_conversations=%d model_fp=%llu",
            kv_log_name(kc),
            kc->opt.anchor_step,
            kv_cache_continued_step(kc),
            kc->opt.prefill_chunk,
            kc->opt.small_dense_tokens,
            kc->opt.tail_anchors,
            kc->opt.mid_spacing_tokens,
            kc->opt.min_anchors,
            kc->opt.max_conversations,
            (unsigned long long)kc->model_fp);
    return true;
}

void ds4_kvstore_close(ds4_kvstore *kc) {
    ds4_kvstore_clear(kc);
    free(kc->dir);
    memset(kc, 0, sizeof(*kc));
}

char *ds4_kvstore_render_tokens_text(ds4_engine *engine,
                                     const ds4_tokens *tokens,
                                     size_t *out_len) {
    kv_buf b = {0};
    for (int i = 0; i < tokens->len; i++) {
        size_t len = 0;
        char *piece = ds4_token_text(engine, tokens->v[i], &len);
        kv_buf_append(&b, piece, len);
        free(piece);
    }
    if (out_len) *out_len = b.len;
    return kv_buf_take(&b);
}

bool ds4_kvstore_byte_prefix_match(const char *text, size_t text_len,
                                   const char *prefix, size_t prefix_len) {
    return prefix_len <= text_len &&
           (prefix_len == 0 || memcmp(text, prefix, prefix_len) == 0);
}

void ds4_kvstore_tokens_copy_prefix(ds4_tokens *dst, const ds4_tokens *src, int n) {
    dst->len = 0;
    if (!src) return;
    if (n > src->len) n = src->len;
    for (int i = 0; i < n; i++) ds4_tokens_push(dst, src->v[i]);
}

static void tokens_append(ds4_tokens *dst, const ds4_tokens *src) {
    if (!dst || !src) return;
    for (int i = 0; i < src->len; i++) ds4_tokens_push(dst, src->v[i]);
}

void ds4_kvstore_build_prompt_from_exact_prefix_and_text_suffix(
        ds4_engine *engine,
        const ds4_tokens *exact_prefix,
        const char *suffix_text,
        ds4_tokens *out) {
    ds4_tokens_copy(out, exact_prefix);

    ds4_tokens suffix = {0};
    /* The suffix may start with DS4 chat markers such as <｜User｜> or
     * </think>, so use the rendered-chat tokenizer, not plain text BPE. */
    ds4_tokenize_rendered_chat(engine, suffix_text ? suffix_text : "", &suffix);
    tokens_append(out, &suffix);
    ds4_tokens_free(&suffix);
}

int ds4_kvstore_store_len(const ds4_kvstore *kc, int tokens) {
    const int trim = kc->opt.boundary_trim_tokens;
    const int align = kc->opt.boundary_align_tokens;
    if (tokens > kc->opt.min_tokens + trim) {
        int stable = tokens - trim;
        if (align > 0) stable -= stable % align;
        if (stable >= kc->opt.min_tokens) return stable;
    }
    return tokens;
}

int ds4_kvstore_chat_anchor_pos(const ds4_kvstore *kc,
                                const ds4_tokens *prompt,
                                int user_token_id,
                                int assistant_token_id) {
    if (!prompt || user_token_id < 0 || assistant_token_id < 0) return -1;

    /* Cold checkpoints maximize reuse across independent agent sessions.  The
     * stable rendered chat prefix is everything before the user message that
     * asks this specific task.  Some clients put stable user-role scaffolding
     * first, so use the last user marker before the first assistant marker. */
    int last_user = -1;
    for (int i = 0; i < prompt->len; i++) {
        const int token = prompt->v[i];
        if (token == assistant_token_id) break;
        if (token == user_token_id) last_user = i;
    }
    return last_user >= kc->opt.min_tokens ? last_user : -1;
}

static int kv_lcm(int a, int b) {
    if (a <= 0) return b > 0 ? b : 0;
    if (b <= 0) return a;
    int x = a, y = b;
    while (y) { int t = x % y; x = y; y = t; }
    long long l = (long long)(a / x) * b;
    return l > INT_MAX ? INT_MAX : (int)l;
}

static int kv_cache_continued_step(const ds4_kvstore *kc) {
    if (!kc->enabled || kc->opt.continued_interval_tokens <= 0) return 0;
    /* Continued checkpoints are the retention anchors, so write them on the
     * anchor_step grid instead of the legacy continued_interval; this populates
     * the dense-small ladder (<= small_dense).  continued_interval stays the
     * enable gate and the fallback when anchor_step is unset. */
    int step = kc->opt.anchor_step;
    if (step <= 0) step = kc->opt.continued_interval_tokens;
    /* The store only fires when live_tokens % step == 0 AND live_tokens sits on
     * a prefill-chunk boundary, so step MUST be a multiple of the chunk or the
     * effective spacing silently becomes lcm(step, chunk) — the original bug
     * (step 10240, chunk 4096 -> anchors every 20480, none <= small_dense).
     * Align to lcm(boundary_align, prefill_chunk); anchor_step (8192) is already
     * a multiple of every default chunk, and this keeps user-set values safe. */
    int align = kc->opt.boundary_align_tokens;
    if (kc->opt.prefill_chunk > 0) align = kv_lcm(align, kc->opt.prefill_chunk);
    if (align > 0) {
        /* Round up in wide arithmetic: align can reach INT_MAX (kv_lcm caps it),
         * so step + align - 1 would overflow a signed int (UB). */
        long long s = (long long)step;
        s = ((s + align - 1) / align) * align;
        step = s > INT_MAX ? INT_MAX : (int)s;
        if (step <= 0) step = align;
    }
    return step;
}

int ds4_kvstore_continued_store_target(const ds4_kvstore *kc, int live_tokens) {
    const int step = kv_cache_continued_step(kc);
    if (step <= 0) return 0;
    if (live_tokens < kc->opt.min_tokens) return 0;
    if (live_tokens % step != 0) return 0;
    if (live_tokens <= kc->continued_last_store_tokens) return 0;
    return live_tokens;
}

void ds4_kvstore_note_store(ds4_kvstore *kc, int tokens) {
    if (tokens > kc->continued_last_store_tokens) {
        kc->continued_last_store_tokens = tokens;
    }
}

int ds4_kvstore_suppress_continued_store(ds4_kvstore *kc, int tokens) {
    if (ds4_kvstore_continued_store_target(kc, tokens) != tokens) return -1;
    int old = kc->continued_last_store_tokens;
    ds4_kvstore_note_store(kc, tokens);
    return old;
}

void ds4_kvstore_restore_suppressed_continued(ds4_kvstore *kc,
                                              int old_tokens,
                                              int suppressed_tokens) {
    if (old_tokens >= 0 && kc->continued_last_store_tokens == suppressed_tokens) {
        kc->continued_last_store_tokens = old_tokens;
    }
}

static bool kv_cache_file_size_bytes(uint64_t text_bytes,
                                     uint64_t payload_bytes,
                                     uint64_t trailer_bytes,
                                     uint64_t *file_bytes) {
    /* New stores write the v2 header (48 + 24 extra + 4 text-bytes). */
    const uint64_t fixed = DS4_KVSTORE_FIXED_HEADER +
                           DS4_KVSTORE_HEADER_V2_EXTRA + 4ull;
    if (UINT64_MAX - fixed < text_bytes ||
        UINT64_MAX - fixed - text_bytes < payload_bytes ||
        UINT64_MAX - fixed - text_bytes - payload_bytes < trailer_bytes)
        return false;
    if (file_bytes) *file_bytes = fixed + text_bytes + payload_bytes + trailer_bytes;
    return true;
}

static bool kv_cache_budget_required(uint64_t file_bytes,
                                     uint64_t *required_bytes) {
    /* The serialized size is deterministic for one snapshot, including the
     * optional trailer.  Reserve 1% headroom so filesystem/accounting surprises
     * cannot produce a file that is immediately removed by the budget pass. */
    uint64_t slack = file_bytes / 100u;
    if (file_bytes % 100u) slack++;
    if (UINT64_MAX - file_bytes < slack) return false;
    if (required_bytes) *required_bytes = file_bytes + slack;
    return true;
}

bool ds4_kvstore_file_size_fits(const ds4_kvstore *kc,
                                uint64_t text_bytes,
                                uint64_t payload_bytes,
                                uint64_t trailer_bytes,
                                uint64_t *file_bytes_out,
                                uint64_t *required_bytes_out) {
    uint64_t file_bytes = 0;
    if (!kv_cache_file_size_bytes(text_bytes, payload_bytes, trailer_bytes,
                                  &file_bytes))
        return false;
    if (file_bytes_out) *file_bytes_out = file_bytes;
    if (!kc || kc->budget_bytes == 0) return true;
    uint64_t required = 0;
    if (!kv_cache_budget_required(file_bytes, &required)) return false;
    if (required_bytes_out) *required_bytes_out = required;
    return required <= kc->budget_bytes;
}

static bool kv_cache_file_text_matches(const char *path, const char sha[41],
                                       const char *text, size_t text_len) {
    if (text_len > UINT32_MAX) return false;
    FILE *fp = fopen(path, "rb");
    if (!fp) return false;

    ds4_kvstore_entry hdr = {0};
    uint32_t text_bytes = 0;
    bool ok = ds4_kvstore_read_header(fp, &hdr, &text_bytes) &&
              text_bytes == (uint32_t)text_len;
    char *stored = NULL;
    if (ok) {
        stored = kv_xmalloc((size_t)text_bytes + 1);
        ok = fread(stored, 1, text_bytes, fp) == text_bytes;
    }
    fclose(fp);
    if (!ok) {
        free(stored);
        return false;
    }

    char stored_sha[41];
    ds4_kvstore_sha1_bytes_hex(stored, text_bytes, stored_sha);
    ok = !strcmp(stored_sha, sha) &&
         (text_len == 0 || memcmp(stored, text, text_len) == 0);
    free(stored);
    return ok;
}

bool ds4_kvstore_existing_compatible(ds4_kvstore *kc, const char *path,
                                     const char sha[41],
                                     const char *text, size_t text_len,
                                     int model_id, int quant_bits, int ctx_size) {
    if (access(path, F_OK) != 0) return false;
    ds4_kvstore_entry e = {0};
    if (!ds4_kvstore_read_entry_file(path, sha, &e)) return false;
    /* model_fp must match the load-side guard (find_text_prefix_skip): after a
     * weight swap a same-text file carries a stale fingerprint; the load path
     * rejects it, so reusing it here would silently disable disk caching for
     * this prefix until budget pressure happened to evict the zombie. */
    bool compatible = e.model_id == (uint8_t)model_id &&
                      (!e.model_fp || e.model_fp == kc->model_fp) &&
                      (!kc->reject_different_quant ||
                       e.quant_bits == (uint8_t)quant_bits) &&
                      e.ctx_size <= (uint32_t)ctx_size &&
                      kv_cache_file_text_matches(path, sha, text, text_len);
    if (compatible) {
        /* Refresh hits/recency so an actively-revalidated checkpoint is not
         * ranked as idle by the LRU/halving passes. */
        ds4_kvstore_touch_file(path, e.hits + 1, e.stale, (uint64_t)time(NULL));
    }
    ds4_kvstore_entry_free(&e);
    if (!compatible) {
        if (unlink(path) == 0) {
            kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                    "%s: kv cache replaced incompatible file %s",
                    kv_log_name(kc), path);
        }
        return false;
    }
    return true;
}

static bool kv_trailer_serialized_size(const ds4_kvstore_trailer_hooks *hooks,
                                       const char *text,
                                       uint64_t *bytes_out) {
    if (bytes_out) *bytes_out = 0;
    if (!hooks || !hooks->serialized_size) return true;
    return hooks->serialized_size(hooks->ud, text, bytes_out);
}

static bool kv_trailer_write(const ds4_kvstore_trailer_hooks *hooks,
                             FILE *fp, const char *text,
                             uint64_t *written_bytes) {
    if (written_bytes) *written_bytes = 0;
    if (!hooks || !hooks->write) return true;
    return hooks->write(hooks->ud, fp, text, written_bytes);
}

static void kv_cache_rewrite_trailer(ds4_kvstore *kc, const char *path,
                                     const char *text,
                                     const ds4_kvstore_trailer_hooks *hooks) {
    uint64_t trailer_est = 0;
    if (!hooks || !hooks->write || !hooks->serialized_size ||
        !kv_trailer_serialized_size(hooks, text, &trailer_est) ||
        trailer_est == 0)
    {
        return;
    }
    FILE *fp = fopen(path, "r+b");
    if (!fp) return;
    ds4_kvstore_entry hdr = {0};
    uint32_t text_bytes = 0;
    bool ok = ds4_kvstore_read_header(fp, &hdr, &text_bytes);
    uint64_t hdr_total = DS4_KVSTORE_FIXED_HEADER + 4ull +
        (hdr.hdr_version == KV_CACHE_VERSION2 ? DS4_KVSTORE_HEADER_V2_EXTRA : 0ull);
    uint64_t end = hdr_total + (uint64_t)text_bytes + hdr.payload_bytes;
    if (ok && end <= (uint64_t)INT64_MAX &&
        fseeko(fp, (off_t)end, SEEK_SET) == 0 &&
        ftruncate(fileno(fp), (off_t)end) == 0)
    {
        uint64_t ignored = 0;
        ok = kv_trailer_write(hooks, fp, text, &ignored) && fflush(fp) == 0;
        if (ok && ignored > 0) {
            uint8_t h[DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V2_EXTRA];
            uint64_t now = (uint64_t)time(NULL);
            if (hdr.hdr_version == KV_CACHE_VERSION2) {
                ds4_kvstore_fill_header_v2(h, hdr.model_id, hdr.quant_bits,
                                           hdr.reason,
                                           (uint8_t)(hdr.ext_flags | hooks->ext_flag),
                                           hdr.tokens, hdr.hits, hdr.ctx_size,
                                           hdr.created_at, now, hdr.payload_bytes,
                                           hdr.conv_id, hdr.model_fp, hdr.bucket,
                                           hdr.level, hdr.stale);
                ok = fseeko(fp, 0, SEEK_SET) == 0 &&
                     fwrite(h, 1,
                            DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V2_EXTRA,
                            fp) == (DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V2_EXTRA) &&
                     fflush(fp) == 0;
            } else {
                ds4_kvstore_fill_header(h, hdr.model_id, hdr.quant_bits, hdr.reason,
                                        (uint8_t)(hdr.ext_flags | hooks->ext_flag),
                                        hdr.tokens, hdr.hits, hdr.ctx_size,
                                        hdr.created_at, now, hdr.payload_bytes);
                ok = fseeko(fp, 0, SEEK_SET) == 0 &&
                     fwrite(h, 1, sizeof(h), fp) == sizeof(h) &&
                     fflush(fp) == 0;
            }
        }
    }
    fclose(fp);
    (void)kc;
    (void)ok;
}

bool ds4_kvstore_store_live_prefix_text(ds4_kvstore *kc,
                                        ds4_engine *engine,
                                        ds4_session *session,
                                        const ds4_tokens *tokens,
                                        int store_len,
                                        const char *reason,
                                        const char *cache_text_override,
                                        uint8_t cache_text_ext,
                                        const char *cache_text_key,
                                        const ds4_kvstore_trailer_hooks *hooks,
                                        char *err,
                                        size_t err_len) {
    if (!kc->enabled) return false;
    if (!tokens || store_len < kc->opt.min_tokens) return false;
    const int original_len = tokens->len;

    ds4_tokens store_tokens = {0};
    ds4_kvstore_tokens_copy_prefix(&store_tokens, tokens, store_len);

    const int quant_bits = ds4_engine_routed_quant_bits(engine);
    if (quant_bits != 2 && quant_bits != 4) {
        ds4_tokens_free(&store_tokens);
        return false;
    }
    const int model_id = ds4_engine_model_id(engine);

    char save_err[160] = {0};
    const ds4_tokens *live_tokens = ds4_session_tokens(session);
    if (!live_tokens ||
        live_tokens->len != store_tokens.len ||
        !ds4_tokens_starts_with(live_tokens, &store_tokens))
    {
        kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                "%s: kv cache skipped tokens=%d reason=%s because live checkpoint is at %d",
                kv_log_name(kc),
                store_tokens.len,
                reason,
                live_tokens ? live_tokens->len : -1);
        ds4_tokens_free(&store_tokens);
        return false;
    }

    size_t text_len = 0;
    char *text = NULL;
    const bool text_override = cache_text_override && cache_text_override[0];
    if (text_override) {
        text = kv_xstrdup(cache_text_override);
        text_len = strlen(text);
    } else {
        text = ds4_kvstore_render_tokens_text(engine, &store_tokens, &text_len);
    }
    if (text_len > UINT32_MAX) {
        kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                "%s: kv cache skipped tokens=%d because rendered text is too large",
                kv_log_name(kc), store_tokens.len);
        free(text);
        ds4_tokens_free(&store_tokens);
        return false;
    }

    uint64_t trailer_est_bytes = 0;
    if (!kv_trailer_serialized_size(hooks, text, &trailer_est_bytes)) {
        kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                "%s: kv cache skipped tokens=%d reason=%s because tool map size overflowed",
                kv_log_name(kc), store_tokens.len, reason);
        free(text);
        ds4_tokens_free(&store_tokens);
        return false;
    }
    char sha[41];
    ds4_kvstore_sha1_bytes_hex(text, text_len, sha);
    char *path = ds4_kvstore_path_for_sha(kc, sha);
    const uint8_t reason_code = ds4_kvstore_reason_code(reason);

    if (ds4_kvstore_existing_compatible(kc, path, sha, text, text_len,
                                        model_id,
                                        quant_bits, ds4_session_ctx(session))) {
        kv_cache_rewrite_trailer(kc, path, text, hooks);
        free(text);
        free(path);
        ds4_tokens_free(&store_tokens);
        return true;
    }

    ds4_session_payload_file staged = {0};
    if (ds4_session_stage_payload(session, &staged,
                                  save_err, sizeof(save_err)) != 0) {
        kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                "%s: kv cache skipped tokens=%d reason=%s because KV payload staging failed: %s",
                kv_log_name(kc),
                store_tokens.len,
                reason,
                save_err[0] ? save_err : "unknown error");
        if (err && err_len) snprintf(err, err_len, "%s",
                                     save_err[0] ? save_err : "unknown error");
        free(text);
        free(path);
        ds4_tokens_free(&store_tokens);
        return false;
    }
    uint64_t payload_bytes = staged.bytes;

    uint64_t est_file_bytes = 0, est_required_bytes = 0;
    if (!ds4_kvstore_file_size_fits(kc, (uint64_t)text_len, payload_bytes,
                                    trailer_est_bytes,
                                    &est_file_bytes, &est_required_bytes)) {
        kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                "%s: kv cache skipped tokens=%d reason=%s because estimated file size %.2f MiB (%.2f MiB with safety) exceeds budget %.2f MiB",
                kv_log_name(kc),
                store_tokens.len,
                reason,
                (double)est_file_bytes / (1024.0 * 1024.0),
                (double)est_required_bytes / (1024.0 * 1024.0),
                (double)kc->budget_bytes / (1024.0 * 1024.0));
        ds4_session_payload_file_free(&staged);
        free(text);
        free(path);
        ds4_tokens_free(&store_tokens);
        return false;
    }

    ds4_kvstore_eviction_context incoming = {
        .text = text,
        .text_len = text_len,
        .model_id = (uint8_t)model_id,
        .quant_bits = (uint8_t)quant_bits,
        .ctx_size = (uint32_t)ds4_session_ctx(session),
        .reject_different_quant = kc->reject_different_quant,
    };
    ds4_kvstore_evict(kc, live_tokens, est_file_bytes, &incoming);

    kv_buf tmpb = {0};
    kv_buf_printf(&tmpb, "%s.tmp.%ld", path, (long)getpid());
    char *tmp = kv_buf_take(&tmpb);
    const double save_t0 = kv_now_sec();
    FILE *fp = fopen(tmp, "wb");
    if (!fp) {
        kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                "%s: kv cache failed to create %s: %s save=%.1f ms",
                kv_log_name(kc), tmp, strerror(errno),
                (kv_now_sec() - save_t0) * 1000.0);
        ds4_session_payload_file_free(&staged);
        free(tmp);
        free(text);
        free(path);
        ds4_tokens_free(&store_tokens);
        return false;
    }

    const uint64_t now = (uint64_t)time(NULL);
    const uint64_t conv_id =
        ds4_kvstore_compute_conv_id(text, text_len, kc->model_fp);
    /* Inherit the conversation's current halving level so a new anchor stays
     * consistent with the keep-set windowing of its siblings; a fresh level=0
     * anchor in a level-L conversation would mix window sizes. */
    uint8_t level = 0;
    if (conv_id != 0) {
        for (int i = 0; i < kc->len; i++)
            if (kc->entry[i].conv_id == conv_id && kc->entry[i].level > level)
                level = kc->entry[i].level;
    }
    const uint32_t bucket = kc->opt.anchor_step > 0
        ? (uint32_t)(store_tokens.len / (uint32_t)kc->opt.anchor_step)
        : 0u;
    uint8_t h[DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V2_EXTRA];
    uint8_t ext_flags = trailer_est_bytes > 0 && hooks ? hooks->ext_flag : 0;
    if (text_override) ext_flags |= cache_text_ext;
    ds4_kvstore_fill_header_v2(h, (uint8_t)model_id, (uint8_t)quant_bits,
                               reason_code, ext_flags,
                               (uint32_t)store_tokens.len, 0,
                               (uint32_t)ds4_session_ctx(session),
                               now, now, payload_bytes,
                               conv_id, kc->model_fp, bucket, level, false);
    uint8_t tb[4];
    ds4_kvstore_le_put32(tb, (uint32_t)text_len);
    uint64_t trailer_bytes = 0;
    errno = 0;
    bool ok = fwrite(h, 1, sizeof(h), fp) == sizeof(h) &&
              fwrite(tb, 1, sizeof(tb), fp) == sizeof(tb) &&
              fwrite(text, 1, text_len, fp) == text_len &&
              ds4_session_write_staged_payload(&staged, fp,
                                               save_err, sizeof(save_err)) == 0 &&
              kv_trailer_write(hooks, fp, text, &trailer_bytes) &&
              fflush(fp) == 0;
    int saved_errno = errno;
    if (fclose(fp) != 0) {
        if (!saved_errno) saved_errno = errno;
        ok = false;
    }
    uint64_t final_file_bytes = 0, final_required_bytes = 0;
    bool final_size_over_budget = false;
    if (ok && !ds4_kvstore_file_size_fits(kc, (uint64_t)text_len, payload_bytes,
                                          trailer_bytes,
                                          &final_file_bytes,
                                          &final_required_bytes))
    {
        final_size_over_budget = true;
        ok = false;
    }
    if (ok && rename(tmp, path) != 0) {
        saved_errno = errno;
        ok = false;
    }
    const double save_ms = (kv_now_sec() - save_t0) * 1000.0;
    if (!ok) {
        if (final_size_over_budget) {
            kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                    "%s: kv cache skipped tokens=%d reason=%s because final file size %.2f MiB (%.2f MiB with safety) exceeds budget %.2f MiB save=%.1f ms",
                    kv_log_name(kc),
                    store_tokens.len,
                    reason,
                    (double)final_file_bytes / (1024.0 * 1024.0),
                    (double)final_required_bytes / (1024.0 * 1024.0),
                    (double)kc->budget_bytes / (1024.0 * 1024.0),
                    save_ms);
        } else {
            kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                    "%s: kv cache store failed (%s): %s save=%.1f ms",
                    kv_log_name(kc),
                    reason,
                    saved_errno ? strerror(saved_errno) :
                    (save_err[0] ? save_err : "unknown error"),
                    save_ms);
        }
        if (err && err_len) {
            snprintf(err, err_len, "%s",
                     saved_errno ? strerror(saved_errno) :
                     (save_err[0] ? save_err : "unknown error"));
        }
        unlink(tmp);
    } else {
        kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                "%s: kv cache stored tokens=%d trimmed=%d reason=%s key=%s size=%.2f MiB save=%.1f ms",
                kv_log_name(kc),
                store_tokens.len,
                original_len - store_tokens.len,
                reason,
                text_override ? (cache_text_key ? cache_text_key : "visible-transcript") : "token-text",
                (double)(DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V2_EXTRA +
                         4ull + text_len + payload_bytes + trailer_bytes) / (1024.0 * 1024.0),
                save_ms);
    }
    ds4_session_payload_file_free(&staged);
    free(tmp);
    free(text);
    free(path);
    ds4_tokens_free(&store_tokens);
    return ok;
}

bool ds4_kvstore_store_live_prefix(ds4_kvstore *kc,
                                   ds4_engine *engine,
                                   ds4_session *session,
                                   const ds4_tokens *tokens,
                                   int store_len,
                                   const char *reason,
                                   const ds4_kvstore_trailer_hooks *hooks,
                                   char *err,
                                   size_t err_len) {
    return ds4_kvstore_store_live_prefix_text(kc, engine, session, tokens,
                                              store_len, reason, NULL, 0, NULL,
                                              hooks, err, err_len);
}

bool ds4_kvstore_maybe_store_continued(ds4_kvstore *kc,
                                       ds4_engine *engine,
                                       ds4_session *session,
                                       const ds4_kvstore_trailer_hooks *hooks,
                                       char *err,
                                       size_t err_len) {
    const ds4_tokens *tokens = ds4_session_tokens(session);
    if (!tokens) return false;
    const int target = ds4_kvstore_continued_store_target(kc, tokens->len);
    if (target == 0) return false;
    if (ds4_kvstore_store_live_prefix(kc, engine, session, tokens, target,
                                      "continued", hooks, err, err_len))
    {
        ds4_kvstore_note_store(kc, target);
        return true;
    }
    return false;
}

static int kv_cache_find_text_prefix_skip(ds4_kvstore *kc, const char *prompt_text,
                                          int model_id, int quant_bits, int ctx_size,
                                          const int *skip, int skip_len) {
    if (!prompt_text) return -1;
    const size_t prompt_bytes = strlen(prompt_text);
    int best = -1;
    for (int i = 0; i < kc->len; i++) {
        ds4_kvstore_entry *e = &kc->entry[i];
        bool skipped = false;
        for (int s = 0; s < skip_len; s++)
            if (skip[s] == i) { skipped = true; break; }
        if (skipped) continue;
        if (e->text_bytes > prompt_bytes) continue;
        /* Payload-less (agent /strip'd) files pass header/text verification but
         * can never load a session; skipping them here keeps them from
         * terminating the fallback chain in front of a valid smaller anchor. */
        if (e->payload_bytes == 0) continue;
        if ((int)e->tokens < kc->opt.min_tokens) continue;
        if (e->model_id != (uint8_t)model_id) continue;
        if (e->model_fp && e->model_fp != kc->model_fp) continue;
        if ((uint32_t)ctx_size < e->ctx_size) continue;
        if (kc->reject_different_quant && e->quant_bits != (uint8_t)quant_bits) continue;
        if (best >= 0) {
            ds4_kvstore_entry *b = &kc->entry[best];
            if (e->text_bytes < b->text_bytes) continue;
            if (e->text_bytes == b->text_bytes && e->tokens <= b->tokens) continue;
        }
        char sha[41];
        ds4_kvstore_sha1_bytes_hex(prompt_text, (size_t)e->text_bytes, sha);
        if (!strcmp(sha, e->sha)) best = i;
    }
    return best;
}

int ds4_kvstore_find_text_prefix(ds4_kvstore *kc, const char *prompt_text,
                                 int model_id, int quant_bits, int ctx_size) {
    kv_cache_refresh(kc);
    return kv_cache_find_text_prefix_skip(kc, prompt_text, model_id, quant_bits,
                                          ctx_size, NULL, 0);
}

/* Decommissioned: the stale layer overrode keep-set protection (evicting
 * frontiers) and was the cross-session poison mechanism.  Redundant (keep-set),
 * halving, and LRU-retire already provide pruning and budget control, so stale
 * marking is inert.  Signature retained because tests and exports reference it. */
void ds4_kvstore_mark_stale_at_load(ds4_kvstore *kc, const char *prompt_text,
                                    int model_id, int quant_bits, int ctx_size) {
    (void)kc; (void)prompt_text; (void)model_id;
    (void)quant_bits; (void)ctx_size;
    return;
}

/* Attempt to load one candidate checkpoint (entry idx).  Returns the loaded
 * token count (0 on failure).  Sets *retryable when the failure happened BEFORE
 * the session payload was touched (header/text-hash/prefix mismatch), so the
 * caller may safely fall back to the next-largest candidate.  Once the payload
 * load is attempted the session is mutated, so any failure there is terminal
 * (retryable=false) to avoid replaying onto partially-restored state. */
static int kv_cache_try_load_one(ds4_kvstore *kc, ds4_engine *engine,
                                 ds4_session *session, const char *prompt_text,
                                 size_t prompt_bytes, int idx,
                                 ds4_tokens *effective_prompt,
                                 ds4_kvstore_load_result *result,
                                 const ds4_kvstore_trailer_hooks *hooks,
                                 bool responses_protocol, bool *retryable) {
    *retryable = false;
    const int model_id = ds4_engine_model_id(engine);
    ds4_kvstore_entry e = kc->entry[idx];
    char *path = kv_xstrdup(e.path);
    const double load_t0 = kv_now_sec();
    FILE *fp = fopen(path, "rb");
    if (!fp) {
        /* fopen failure is a pre-payload failure (session untouched), so let the
         * caller fall back to the next-largest candidate instead of forcing a
         * full prefill. */
        *retryable = true;
        free(path);
        return 0;
    }
    uint32_t text_bytes = 0;
    ds4_kvstore_entry hdr = {0};
    const char *fail_reason = "invalid header";
    bool header_ok = ds4_kvstore_read_header(fp, &hdr, &text_bytes);
    char *cached_text = NULL;
    if (header_ok) {
        if (hdr.model_id != (uint8_t)model_id) {
            header_ok = false;
            fail_reason = "cached checkpoint was written for a different model";
        } else if ((uint64_t)text_bytes > prompt_bytes) {
            header_ok = false;
            fail_reason = "cached text is longer than prompt";
        } else {
            cached_text = kv_xmalloc((size_t)text_bytes + 1);
            if (fread(cached_text, 1, text_bytes, fp) != text_bytes) {
                header_ok = false;
                fail_reason = "truncated cached text";
            } else {
                cached_text[text_bytes] = '\0';
                char text_sha[41];
                ds4_kvstore_sha1_bytes_hex(cached_text, text_bytes, text_sha);
                if (strcmp(text_sha, e.sha)) {
                    header_ok = false;
                    fail_reason = "cached text hash mismatch";
                } else if (!ds4_kvstore_byte_prefix_match(prompt_text, prompt_bytes,
                                                          cached_text, text_bytes)) {
                    header_ok = false;
                    fail_reason = "cached text prefix mismatch";
                }
            }
        }
    }
    if (!header_ok) {
        /* Session untouched: a larger corrupt/mismatched checkpoint must not
         * shadow a valid smaller one — let the caller try the next candidate. */
        *retryable = true;
        kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                "%s: kv cache load failed%s%s %s: %s load=%.1f ms",
                kv_log_name(kc),
                responses_protocol ? " " : "",
                responses_protocol ? "RESPPROTO" : "",
                path, fail_reason, (kv_now_sec() - load_t0) * 1000.0);
        fclose(fp);
        free(cached_text);
        free(path);
        return 0;
    }

    char err[160] = {0};
    int loaded = 0;
    if (ds4_session_load_payload(session, fp, hdr.payload_bytes, err,
                                 sizeof(err)) == 0)
    {
        const ds4_tokens *loaded_tokens = ds4_session_tokens(session);
        if (loaded_tokens && loaded_tokens->len == (int)hdr.tokens) {
            loaded = (int)hdr.tokens;
            if (effective_prompt) {
                /* The cache lookup was by bytes, but the graph state is still
                 * the exact token history stored in the payload.  Build the
                 * prompt from that exact history and tokenize only the text
                 * suffix after the byte prefix. */
                ds4_kvstore_build_prompt_from_exact_prefix_and_text_suffix(
                    engine, loaded_tokens, prompt_text + text_bytes,
                    effective_prompt);
            }
            if (hooks && hooks->load && (hdr.ext_flags & hooks->ext_flag)) {
                if (hooks->load(hooks->ud, fp, hooks->load_wanted) < 0) {
                    /* The KV payload itself restored fine; only the trailer
                     * (e.g. tool map) is corrupt.  Degrade to canonical
                     * re-render, but say so — otherwise a corrupt checkpoint is
                     * indistinguishable from an ordinary miss. */
                    kv_logf(kc, DS4_KVSTORE_LOG_WARNING,
                            "%s: kv cache trailer corrupt, continuing without it %s",
                            kv_log_name(kc), path);
                }
            }
        } else {
            ds4_session_invalidate(session);
            unlink(path);
            kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                    "%s: kv cache discarded corrupt text-prefix payload%s%s %s",
                    kv_log_name(kc),
                    responses_protocol ? " " : "",
                    responses_protocol ? "RESPPROTO" : "",
                    path);
        }
    } else {
        ds4_session_invalidate(session);
        kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                "%s: kv cache load failed%s%s %s: %s load=%.1f ms",
                kv_log_name(kc),
                responses_protocol ? " " : "",
                responses_protocol ? "RESPPROTO" : "",
                path, err, (kv_now_sec() - load_t0) * 1000.0);
    }
    fclose(fp);

    if (loaded > 0) {
        const double load_ms = (kv_now_sec() - load_t0) * 1000.0;
        kc->continued_last_store_tokens = loaded;
        const char *key_kind = ds4_kvstore_key_kind(hdr.ext_flags);
        bool consumed = false;
        if (kc->opt.cold_max_tokens > 0 && loaded > kc->opt.cold_max_tokens) {
            unlink(path);
            consumed = true;
            kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                    "%s: kv cache hit text%s%s tokens=%d text=%u quant=%u key=%s load=%.1f ms consumed file=%s",
                    kv_log_name(kc),
                    responses_protocol ? " " : "",
                    responses_protocol ? "RESPPROTO" : "",
                    loaded, text_bytes, hdr.quant_bits, key_kind, load_ms, path);
        } else {
            ds4_kvstore_touch_file(path, hdr.hits + 1, hdr.stale,
                                   (uint64_t)time(NULL));
            kv_logf(kc, DS4_KVSTORE_LOG_KVCACHE,
                    "%s: kv cache hit text%s%s tokens=%d text=%u quant=%u key=%s load=%.1f ms file=%s",
                    kv_log_name(kc),
                    responses_protocol ? " " : "",
                    responses_protocol ? "RESPPROTO" : "",
                    loaded, text_bytes, hdr.quant_bits, key_kind, load_ms, path);
        }
        if (result) {
            result->tokens = loaded;
            result->text_bytes = text_bytes;
            result->quant_bits = hdr.quant_bits;
            result->ext_flags = hdr.ext_flags;
            result->load_ms = load_ms;
            result->consumed = consumed;
            result->path = kv_xstrdup(path);
        }
    }
    free(cached_text);
    free(path);
    return loaded;
}

int ds4_kvstore_try_load_text(ds4_kvstore *kc,
                              ds4_engine *engine,
                              ds4_session *session,
                              const char *prompt_text,
                              ds4_tokens *effective_prompt,
                              ds4_kvstore_load_result *result,
                              const ds4_kvstore_trailer_hooks *hooks,
                              bool responses_protocol) {
    if (result) memset(result, 0, sizeof(*result));
    if (effective_prompt) effective_prompt->len = 0;
    if (!kc->enabled || !prompt_text) return 0;
    const int quant_bits = ds4_engine_routed_quant_bits(engine);
    if (quant_bits != 2 && quant_bits != 4) return 0;
    const int model_id = ds4_engine_model_id(engine);
    const size_t prompt_bytes = strlen(prompt_text);
    const int ctx_size = ds4_session_ctx(session);

    kv_cache_refresh(kc);
    /* Stale marking was decommissioned (see ds4_kvstore_mark_stale_at_load);
     * keep-set, halving, and LRU-retire handle pruning and budget control. */

    /* Try candidates largest-first; if the selected checkpoint fails pre-payload
     * verification (corrupt body, hash/prefix mismatch), fall back to the next
     * largest valid candidate instead of forcing a full prefill. */
    int skip[16];
    int skip_len = 0;
    int loaded = 0;
    for (;;) {
        const int idx = kv_cache_find_text_prefix_skip(kc, prompt_text, model_id,
                                                       quant_bits, ctx_size,
                                                       skip, skip_len);
        if (idx < 0) break;
        bool retryable = false;
        loaded = kv_cache_try_load_one(kc, engine, session, prompt_text,
                                       prompt_bytes, idx, effective_prompt,
                                       result, hooks, responses_protocol,
                                       &retryable);
        if (loaded > 0 || !retryable) break;
        if (effective_prompt) effective_prompt->len = 0;
        if (skip_len < (int)(sizeof(skip) / sizeof(skip[0])))
            skip[skip_len++] = idx;
        else
            break;
    }
    return loaded;
}

void ds4_kvstore_load_result_free(ds4_kvstore_load_result *result) {
    if (!result) return;
    free(result->path);
    memset(result, 0, sizeof(*result));
}
