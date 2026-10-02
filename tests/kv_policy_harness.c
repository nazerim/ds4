/* Phase-5 policy verification harness for the KV cache v2 eviction redesign
 * (PLAN-KV-REWRITE.md, phases 1-4).
 *
 * Replays realistic store/miss sequences (the observed field patterns: session
 * switches every few minutes between a few long lineages, plus divergence
 * anchors) against the new eviction policy and asserts the acceptance criteria:
 *
 *   AC-1  No `conversation-retired` for a lineage whose leaf was persisted
 *         (last_used) within retire_grace_seconds of the eviction pass.
 *   AC-2  Halving runs before retiring on recently-active lineages.
 *   AC-3  Divergence anchor requests are honored (target recorded, fired once
 *         the session reaches the target, grid-skip on continued boundaries).
 *   AC-4  A lineage active in the "slot" (touched within grace) keeps its
 *         frontier through a budget-pressure storm of the other lineages.
 *
 * This is the deterministic core of live verification: the same assertions
 * hold on a real server (log-assertable), and this harness proves the policy
 * itself without needing a model.
 *
 * Build: cc -O2 -I. -o kv_policy_harness kv_policy_harness.c \
 *           ds4_kvstore.o ds4_help.o rax.o ds4.o ds4_distributed.o \
 *           ds4_tp.o ds4_ssd.o ds4_metal.o ds4_layer_pack.o \
 *           -lm -pthread -framework Foundation -framework Metal
 */
#include "ds4_kvstore.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/stat.h>

static int g_failures = 0;
#define CHECK(cond, msg) do { \
    if (!(cond)) { \
        fprintf(stderr, "FAIL: %s (line %d)\n", msg, __LINE__); \
        g_failures++; \
    } else { \
        printf("  ok: %s\n", msg); \
    } \
} while (0)

/* Mirrors KV_DEFER_BACKOFF_PASSES in ds4_kvstore.c; keep in sync. */
#define HARNESS_DEFER_BACKOFF_PASSES 8

/* ---- deferral-backoff instrumentation ----------------------------------
 * Count "delete deferred" log lines per node text so Scenario E can assert
 * the pass-loop stops re-proposing blocked parents. */
static const char *g_defer_dir = NULL;
static const char *g_defer_texts[4];
static int g_defer_counts[4];

/* Any "delete deferred" line anywhere (Scenario I asserts a same-pass
 * chained retire produces ZERO of these — review H-2). */
static int g_any_defer_lines = 0;

/* "frontier-superseded" unlink lines (Scenario L: eager off-grid frontier
 * sweep, oMLX 0.7.0 a28e5a87 parity). */
static int g_supersede_lines = 0;

static void log_cb(void *ud, ds4_kvstore_log_type type, const char *msg) {
    (void)ud;
    if (type == DS4_KVSTORE_LOG_WARNING) fprintf(stderr, "WARN: %s\n", msg);
    if (strstr(msg, "delete deferred")) g_any_defer_lines++;
    if (strstr(msg, "frontier-superseded")) g_supersede_lines++;
    if (strstr(msg, "delete deferred") && g_defer_dir) {
        for (int i = 0; i < 4; i++) {
            if (!g_defer_texts[i]) continue;
            char sha[41];
            ds4_kvstore_sha1_bytes_hex(g_defer_texts[i],
                                       strlen(g_defer_texts[i]), sha);
            if (strstr(msg, sha)) g_defer_counts[i]++;
        }
    }
}

/* ---- stub file writer (mirrors the unit-test stub; sha-named) ---- */
static void stub_file(const char *dir, const char *text, uint64_t conv_id,
                      uint8_t reason, uint32_t tokens, uint64_t last_used,
                      uint64_t payload_bytes) {
    char sha[41];
    ds4_kvstore_sha1_bytes_hex(text, strlen(text), sha);
    char name[64];
    snprintf(name, sizeof(name), "%.40s.kv", sha);
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    FILE *fp = fopen(path, "wb");
    if (!fp) { perror("stub_file fopen"); exit(1); }
    uint8_t h[DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V2_EXTRA];
    uint32_t bucket = DS4_KVSTORE_DEFAULT_ANCHOR_STEP > 0
        ? tokens / (uint32_t)DS4_KVSTORE_DEFAULT_ANCHOR_STEP : 0;
    ds4_kvstore_fill_header_v2(h, 0, 2, reason, 0, tokens, 0, 32768,
                               100, last_used, payload_bytes,
                               conv_id, 0, bucket, 0, false);
    uint8_t tb[4];
    ds4_kvstore_le_put32(tb, (uint32_t)strlen(text));
    if (fwrite(h, 1, sizeof(h), fp) != sizeof(h) ||
        fwrite(tb, 1, sizeof(tb), fp) != sizeof(tb) ||
        fwrite(text, 1, strlen(text), fp) != strlen(text)) {
        fprintf(stderr, "stub write failed\n");
        exit(1);
    }
    for (uint64_t i = 0; i < payload_bytes; i++) fputc(0, fp);
    fclose(fp);
}

static int file_exists(const char *dir, const char *text) {
    char sha[41];
    ds4_kvstore_sha1_bytes_hex(text, strlen(text), sha);
    char name[64];
    snprintf(name, sizeof(name), "%.40s.kv", sha);
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    return access(path, F_OK) == 0;
}

static void unlink_text(const char *dir, const char *text) {
    char sha[41];
    ds4_kvstore_sha1_bytes_hex(text, strlen(text), sha);
    char name[64];
    snprintf(name, sizeof(name), "%.40s.kv", sha);
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    unlink(path);
}

/* ------------------------------------------------------------------ */
/* Scenario A: session-switch churn (the field pattern that caused the
 * 12:18/14:21 retirements).  Three long lineages A, B, C; each is "live" for a
 * few minutes (its frontier touched), then the next takes the slot.  Under the
 * OLD policy the just-departed lineage looked LRU (old ladder touches) and was
 * retired; under retire-grace it must survive. */
static void scenario_switch_churn(void) {
    printf("== Scenario A: session-switch churn (retire-grace) ==\n");
    char dir[] = "/tmp/kv-harness-a.XXXXXX";
    if (!mkdtemp(dir)) { perror("mkdtemp"); exit(1); }

    const uint64_t now = (uint64_t)time(NULL);
    const char *a1 = "lineage A ladder anchor 1";
    const char *a2 = "lineage A ladder anchor 2";
    const char *a3 = "lineage A ladder anchor 3 frontier";
    const char *b1 = "lineage B ladder anchor 1";
    const char *b2 = "lineage B ladder anchor 2 frontier";
    const char *c1 = "lineage C ladder anchor 1";
    const char *c2 = "lineage C ladder anchor 2 frontier";
    /* Old touches for the ladder bodies (simulates: only the frontier refresh
     * on each visit); frontiers touched recently per the switch cadence. */
    stub_file(dir, a1, 1, 1, 40960, now - 4000, 256);
    stub_file(dir, a2, 1, 1, 81920, now - 3000, 256);
    stub_file(dir, a3, 1, 1, 150000, now - 60,  256);
    stub_file(dir, b1, 2, 1, 40960, now - 4000, 256);
    stub_file(dir, b2, 2, 1, 120000, now - 120, 256);
    stub_file(dir, c1, 3, 1, 40960, now - 4000, 256);
    stub_file(dir, c2, 3, 1, 100000, now - 180, 256);

    ds4_kvstore kc = {0};
    ds4_kvstore_options opt = ds4_kvstore_default_options();
    opt.retire_grace_seconds = 3600;
    opt.min_anchors = 2;
    if (!ds4_kvstore_open(&kc, dir, 1, false, 0, opt,
                          "harness", log_cb, NULL)) {
        fprintf(stderr, "open failed\n");
        exit(1);
    }
    /* Byte-level budget: 7 stub files ~= 2100 B; force eviction of ~4-5. */
    kc.budget_bytes = 1100;
    /* Trigger an eviction with an unrelated incoming store (a 4th session). */
    ds4_kvstore_eviction_context inc = {
        .text = "session D incoming prompt text",
        .text_len = strlen("session D incoming prompt text"),
        .model_id = 0, .quant_bits = 2, .ctx_size = 32768,
        .reject_different_quant = false,
    };
    ds4_kvstore_evict(&kc, NULL, 0, &inc);

    /* AC-4: each recently-live lineage (A last 60s, B 120s, C 180s) must keep
     * its frontier under pressure; only the OLD ladder bodies may be pruned. */
    CHECK(file_exists(dir, a3), "A frontier survives (live 60s ago)");
    CHECK(file_exists(dir, b2), "B frontier survives (live 120s ago)");
    CHECK(file_exists(dir, c2), "C frontier survives (live 180s ago)");

    ds4_kvstore_close(&kc);
    const char *texts[] = {a1, a2, a3, b1, b2, c1, c2};
    for (int i = 0; i < 7; i++) unlink_text(dir, texts[i]);
    rmdir(dir);
}

/* ------------------------------------------------------------------ */
/* Scenario B: a lineage that is genuinely idle (> grace) is still retired
 * when the budget demands it. */
static void scenario_idle_retired(void) {
    printf("== Scenario B: genuinely-idle lineage still retired ==\n");
    char dir[] = "/tmp/kv-harness-b.XXXXXX";
    if (!mkdtemp(dir)) { perror("mkdtemp"); exit(1); }

    const uint64_t now = (uint64_t)time(NULL);
    const char *old1 = "idle lineage anchor 1";
    const char *old2 = "idle lineage anchor 2";
    const char *cur   = "current lineage frontier";
    stub_file(dir, old1, 10, 1, 40960, now - 100000, 256);
    stub_file(dir, old2, 10, 1, 90000,  now - 100000, 256);
    stub_file(dir, cur, 11, 1, 80000,   now - 10, 256);

    ds4_kvstore kc = {0};
    ds4_kvstore_options opt = ds4_kvstore_default_options();
    opt.retire_grace_seconds = 3600;
    opt.min_anchors = 1;
    if (!ds4_kvstore_open(&kc, dir, 1, false, 0, opt,
                          "harness", log_cb, NULL)) {
        fprintf(stderr, "open failed\n");
        exit(1);
    }
    /* Byte-level budget (like the unit tests): 3 stub files ~= 900 B; force
     * pressure so retirement must run. */
    kc.budget_bytes = 400;
    ds4_kvstore_eviction_context inc = {
        .text = cur, .text_len = strlen(cur),
        .model_id = 0, .quant_bits = 2, .ctx_size = 32768,
        .reject_different_quant = false,
    };
    ds4_kvstore_evict(&kc, NULL, 0, &inc);

    CHECK(file_exists(dir, cur), "active lineage survives");
    CHECK(!file_exists(dir, old1), "idle lineage (100000s old) retired");
    CHECK(!file_exists(dir, old2), "idle lineage anchor 2 retired");

    ds4_kvstore_close(&kc);
    const char *texts[] = {old1, old2, cur};
    for (int i = 0; i < 3; i++) unlink_text(dir, texts[i]);
    rmdir(dir);
}

/* Scenario D: delta-chain GC.  A v3 node keeps only rows [delta_from,
 * tokens); deleting its parent would silently corrupt the chain, so eviction
 * must DEFER parents while children exist, and reclaim them once the children
 * die. */
static void stub_file_v3(const char *dir, const char *text,
                         const char *parent_text, uint8_t reason,
                         uint32_t tokens, uint32_t delta_from,
                         uint64_t last_used, uint64_t payload_bytes) {
    char psha[41];
    ds4_kvstore_sha1_bytes_hex(parent_text, strlen(parent_text), psha);
    char sha[41];
    ds4_kvstore_sha1_bytes_hex(text, strlen(text), sha);
    char name[64];
    snprintf(name, sizeof(name), "%.40s.kv", sha);
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    FILE *fp = fopen(path, "wb");
    if (!fp) { perror("stub_file_v3 fopen"); exit(1); }
    uint8_t h[DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V3_EXTRA];
    uint32_t bucket = DS4_KVSTORE_DEFAULT_ANCHOR_STEP > 0
        ? tokens / (uint32_t)DS4_KVSTORE_DEFAULT_ANCHOR_STEP : 0;
    ds4_kvstore_fill_header_v3(h, 0, 2, reason, 0, tokens, 0, 32768,
                               100, last_used, payload_bytes,
                               7, 0, bucket, 0, false, psha, delta_from);
    uint8_t tb[4];
    ds4_kvstore_le_put32(tb, (uint32_t)strlen(text));
    if (fwrite(h, 1, sizeof(h), fp) != sizeof(h) ||
        fwrite(tb, 1, sizeof(tb), fp) != sizeof(tb) ||
        fwrite(text, 1, strlen(text), fp) != strlen(text)) {
        fprintf(stderr, "stub v3 write failed\n");
        exit(1);
    }
    for (uint64_t i = 0; i < payload_bytes; i++) fputc(0, fp);
    fclose(fp);
}

static void scenario_delta_parent_deferred(void) {
    printf("== Scenario D: delta-parent eviction deferral ==\n");
    char dir[] = "/tmp/kv-harness-d.XXXXXX";
    if (!mkdtemp(dir)) { perror("mkdtemp"); exit(1); }

    const uint64_t now = (uint64_t)time(NULL);
    const char *x1 = "delta chain anchor text one (root)";
    const char *x2 = "delta chain anchor text two (child of one)";
    const char *x3 = "delta chain frontier text three (child of two)";

    stub_file(dir, x1, 7, 1, 40960, now - 100000, 4000);
    stub_file_v3(dir, x2, x1, 2, 81920, 40960, now - 100000, 4000);
    stub_file_v3(dir, x3, x2, 2, 122880, 81920, now - 10, 4000);

    ds4_kvstore kc = {0};
    ds4_kvstore_options opt = ds4_kvstore_default_options();
    opt.retire_grace_seconds = 0;
    opt.min_anchors = 1;
    if (!ds4_kvstore_open(&kc, dir, 1, false, 0, opt,
                          "harness", log_cb, NULL)) {
        fprintf(stderr, "open failed\n");
        exit(1);
    }
    /* Total ~13k bytes; force pressure.  x1/x2 are the stale victims the pass
     * must reach first (x3 is a fresh leaf) — both must be DEFERRED because
     * they still have live children. */
    kc.budget_bytes = 6000;
    ds4_kvstore_eviction_context inc = {
        .text = "incoming unrelated request text",
        .text_len = 31,
        .model_id = 0, .quant_bits = 2, .ctx_size = 32768,
        .reject_different_quant = false,
    };
    ds4_kvstore_evict(&kc, NULL, 0, &inc);
    CHECK(file_exists(dir, x1), "chain root deferred while a child chain is alive");
    CHECK(file_exists(dir, x2), "chain middle deferred while the frontier child is alive");

    /* Frontier x3 dies.  Rescan + evict: x2 (now childless) can go; x1 clears
     * on the next pass once the scan sees x2 gone. */
    unlink_text(dir, x3);
    ds4_kvstore_close(&kc);
    ds4_kvstore_open(&kc, dir, 1, false, 0, opt, "harness", log_cb, NULL);
    kc.budget_bytes = 4000;
    ds4_kvstore_evict(&kc, NULL, 0, &inc);
    CHECK(!file_exists(dir, x2), "former middle evicted once it became a leaf");

    ds4_kvstore_close(&kc);
    ds4_kvstore_open(&kc, dir, 1, false, 0, opt, "harness", log_cb, NULL);
    kc.budget_bytes = 3000;
    ds4_kvstore_evict(&kc, NULL, 0, &inc);
    CHECK(!file_exists(dir, x1), "root evicted after its children died");

    ds4_kvstore_close(&kc);
    const char *texts[] = {x1, x2, x3};
    for (int i = 0; i < 3; i++) unlink_text(dir, texts[i]);
    rmdir(dir);
}

/* ------------------------------------------------------------------ */
/* Scenario C: divergence anchor decision logic end-to-end at the kvstore
 * level (target set -> grid-skip -> fired when the session reaches it).
 * The store itself needs a live session; here we validate the target
 * lifecycle and that grid-aligned targets are skipped (continued covers). */
static void scenario_divergence_logic(void) {
    printf("== Scenario C: divergence anchor decision logic ==\n");
    ds4_kvstore kc = {0};
    ds4_kvstore_options opt = ds4_kvstore_default_options();
    kc.enabled = true;
    kc.opt = opt;
    kc.opt.min_tokens = 512;
    kc.opt.max_divergence_anchors = 8;

    ds4_kvstore_set_divergence_target(&kc, 23420);
    CHECK(kc.divergence_target_tokens == 23420,
          "divergence target recorded at common=23420");

    ds4_kvstore_set_divergence_target(&kc, 100);
    CHECK(kc.divergence_target_tokens == 23420,
          "below-min_tokens target rejected");

    kc.opt.max_divergence_anchors = 0;
    ds4_kvstore_set_divergence_target(&kc, 30000);
    CHECK(kc.divergence_target_tokens == 23420,
          "feature-disabled (cap 0) rejects target");
    kc.opt.max_divergence_anchors = 8;

    kc.divergence_target_tokens = 0;
    ds4_kvstore_set_divergence_target(&kc, 16384);
    CHECK(kc.divergence_target_tokens == 16384,
          "grid-aligned target recorded (fired only via continued dedup)");
    ds4_kvstore_close(&kc);
}

/* Scenario E: deferral backoff.  A delta parent whose delete was deferred
 * (live children) stays ON disk, and kv_cache_refresh() re-scans disk at the
 * top of every eviction pass — so without backoff the same blocked node is
 * re-proposed every pass (production: 2,065 deferrals / 228 files / hour).
 * The fix must (1) skip re-proposals while the node's state is unchanged,
 * (2) unblock immediately when the state DOES change (child died), and
 * (3) expire after HARNESS_DEFER_BACKOFF_PASSES passes so nothing becomes
 * permanently unreclaimable.  Same chain layout as scenario D. */
static void scenario_deferral_backoff(void) {
    printf("== Scenario E: deferral backoff (no re-proposal spam) ==\n");
    char dir[] = "/tmp/kv-harness-e.XXXXXX";
    if (!mkdtemp(dir)) { perror("mkdtemp"); exit(1); }

    const uint64_t now = (uint64_t)time(NULL);
    const char *y1 = "backoff chain anchor text one (root)";
    const char *y2 = "backoff chain anchor text two (child of one)";
    const char *y3 = "backoff chain frontier text three (child of two)";

    stub_file(dir, y1, 7, 1, 40960, now - 100000, 4000);
    stub_file_v3(dir, y2, y1, 2, 81920, 40960, now - 100000, 4000);
    stub_file_v3(dir, y3, y2, 2, 122880, 81920, now - 10, 4000);

    g_defer_dir = dir;
    g_defer_texts[0] = y1; g_defer_texts[1] = y2;
    g_defer_counts[0] = g_defer_counts[1] = g_defer_counts[2] = g_defer_counts[3] = 0;

    ds4_kvstore kc = {0};
    ds4_kvstore_options opt = ds4_kvstore_default_options();
    if (!ds4_kvstore_open(&kc, dir, 1, false, 0, opt,
                          "harness", log_cb, NULL)) {
        fprintf(stderr, "open failed\n");
        exit(1);
    }
    kc.budget_bytes = 6000;
    ds4_kvstore_eviction_context inc = {
        .text = "incoming unrelated request text",
        .text_len = 31,
        .model_id = 0, .quant_bits = 2, .ctx_size = 32768,
        .reject_different_quant = false,
    };
    ds4_kvstore_evict(&kc, NULL, 0, &inc);
    CHECK(g_defer_counts[0] == 1, "root deferred exactly once on first pass");
    CHECK(file_exists(dir, y1), "root file survives its deferral");

    /* Storm: repeated budget-pressure passes with the chain state FROZEN.
     * Each evict() is one pass (the in-pass main loop terminates at PHASE D).
     * Without backoff every pass re-proposes y1/y2: counts would climb to 5.
     * With backoff the counts stay flat until expiry. */
    for (int i = 0; i < 2; i++) ds4_kvstore_evict(&kc, NULL, 0, &inc);
    CHECK(g_defer_counts[0] == 1, "no re-proposal of the blocked root across 2 frozen passes");

    /* Touch is NOT a deletability change.  A store/hit rewrites a blocked
     * parent's header (last_used), which must not unblock its backoff row:
     * the node is still undeletable while children > 0, and every touch
     * re-proposing it is the wall the fix exists to stop (field: one 65536
     * rung deferred 413x/day).  Only children==0 or file removal may drop
     * the row early; the pass expiry remains the safety valve. */
    /* Touch with a STALE-but-different last_used: any value within the
     * retire-grace window would pin the node and confound the later
     * reclaim assertions.  The predicate under test must ignore it
     * regardless. */
    { char sha[41], path[512];
      ds4_kvstore_sha1_bytes_hex(y1, strlen(y1), sha);
      snprintf(path, sizeof(path), "%s/%.40s.kv", dir, sha);
      CHECK(ds4_kvstore_touch_file(path, 2, false, 0, now - 99000),
            "harness can touch the blocked parent header"); }
    for (int i = 0; i < 2; i++) ds4_kvstore_evict(&kc, NULL, 0, &inc);
    CHECK(g_defer_counts[0] == 1, "a touch of the blocked parent does not re-propose it");

    /* Expiry path: enough passes to expire HARNESS_DEFER_BACKOFF_PASSES rows.
     * The node MUST become proposable again — nothing is permanently
     * unreclaimable — and eventually the whole chain is reclaimed once the
     * frontier dies. */
    for (int i = 0; i < HARNESS_DEFER_BACKOFF_PASSES + 2; i++)
        ds4_kvstore_evict(&kc, NULL, 0, &inc);
    CHECK(g_defer_counts[0] >= 2, "blocked root re-proposed after backoff expiry (nothing permanently unreclaimable)");
    unlink_text(dir, y3);
    /* After y2/y3 die, only y1 remains; under its old budget it FITS (total
     * < target) and is correctly kept.  Tighten the budget so reclamation is
     * actually required: y1 must now flow through the same backoff expiry
     * into retirement. */
    kc.budget_bytes = 3000;
    for (int i = 0; i < HARNESS_DEFER_BACKOFF_PASSES + 4; i++)
        ds4_kvstore_evict(&kc, NULL, 0, &inc);
    CHECK(!file_exists(dir, y1), "root reclaimed via expiry-unblock after frontier death");
    CHECK(!file_exists(dir, y2), "middle reclaimed after frontier death");

    ds4_kvstore_close(&kc);
    g_defer_dir = NULL;
    const char *texts[] = {y1, y2, y3};
    for (int i = 0; i < 3; i++) unlink_text(dir, texts[i]);
    rmdir(dir);
}

/* ---- Scenario G: trailer rewrite must not corrupt the envelope ----------
 * Full-engine independent review found kv_cache_rewrite_trailer (the reuse
 * path: same sha already on disk, tool-map trailer refreshed) truncating v3
 * files 68 bytes short (cutting the pos3 payload tail), downgrading the
 * header v3->v1 (destroying parent link -> cascade child deletion), and
 * writing 72 bytes over v1 files (clobbering the text-size word + text
 * prefix with 24 uninitialized stack bytes).  Both were probe-reproduced.
 * This scenario pins the contract: payload + text + identity untouched,
 * trailer replaced (not appended twice), per version. */
static bool g6_size(void *ud, const char *text, uint64_t *out) {
    (void)ud; (void)text; *out = 64; return true;
}
static bool g6_write(void *ud, FILE *fp, const char *text, uint64_t *wb) {
    (void)ud; (void)text;
    uint8_t sec[64];
    memcpy(sec, "KVTM", 4);
    memset(sec + 4, 0xAB, 60);
    if (fwrite(sec, 1, 64, fp) != 64) return false;
    *wb = 64;
    return true;
}

static uint64_t g6_payload_cksum(const uint8_t *p, uint64_t n) {
    uint64_t h = 1469598103934665603ull;
    for (uint64_t i = 0; i < n; i++) { h ^= p[i]; h *= 1099511628211ull; }
    return h;
}
#define G6_PAY 4096
static void g6_write_pattern(FILE *fp, uint64_t n) {
    for (uint64_t i = 0; i < n; i++) fputc((int)((i * 31u + 7u) & 0xFFu), fp);
}

static uint64_t g6_file_size(const char *path) {
    struct stat st;
    return stat(path, &st) == 0 ? (uint64_t)st.st_size : 0;
}

static void g6_payload_cksum_at(const char *path, uint64_t off, uint64_t *out) {
    FILE *fp = fopen(path, "rb");
    if (!fp) { *out = 0; return; }
    uint8_t *buf = malloc(G6_PAY);
    fseeko(fp, (off_t)off, SEEK_SET);
    size_t got = fread(buf, 1, G6_PAY, fp);
    *out = got == G6_PAY ? g6_payload_cksum(buf, G6_PAY) : (uint64_t)-1;
    free(buf);
    fclose(fp);
}

static void scenario_trailer_rewrite(void) {
    printf("== Scenario G: trailer rewrite envelope integrity ==\n");
    char dir[] = "/tmp/kv-harness-g.XXXXXX";
    if (!mkdtemp(dir)) { perror("mkdtemp"); exit(1); }

    const char *ptext = "scenario G parent text";
    const char *v3text = "scenario G v3 delta frontier text";
    const char *v2text = "scenario G v2 checkpoint text";
    const char *v1text = "scenario G v1 legacy text";
    const char *texts[3] = { v3text, v2text, v1text };
    /* layout per version: hdr(48+extra) + tb(4) + text + payload(4096) */
    const uint64_t hdrs[3] = {
        DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V3_EXTRA + 4,
        DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V2_EXTRA + 4,
        DS4_KVSTORE_FIXED_HEADER + 4,
    };

    char paths[3][512];
    uint64_t cksums[3];
    for (int v = 0; v < 3; v++) {
        char sha[41];
        ds4_kvstore_sha1_bytes_hex(texts[v], strlen(texts[v]), sha);
        snprintf(paths[v], sizeof(paths[v]), "%s/%.40s.kv", dir, sha);
        FILE *fp = fopen(paths[v], "wb");
        if (!fp) { perror("scenario G stub"); exit(1); }
        uint8_t h[DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V3_EXTRA];
        uint32_t tlen = (uint32_t)strlen(texts[v]);
        uint8_t tb[4];
        ds4_kvstore_le_put32(tb, tlen);
        if (v == 0) {
            char psha[41];
            ds4_kvstore_sha1_bytes_hex(ptext, strlen(ptext), psha);
            ds4_kvstore_fill_header_v3(h, 0, 2, 2, 0, 8192, 3, 32768,
                                       100, 100, G6_PAY, 7, 0, 1, 0, false,
                                       psha, 4096);
            fwrite(h, 1, 116, fp);
        } else if (v == 1) {
            ds4_kvstore_fill_header_v2(h, 0, 2, 2, 0, 8192, 3, 32768,
                                       100, 100, G6_PAY, 7, 0, 1, 0, false);
            fwrite(h, 1, DS4_KVSTORE_FIXED_HEADER + DS4_KVSTORE_HEADER_V2_EXTRA, fp);
        } else {
            ds4_kvstore_fill_header(h, 0, 2, 2, 0, 8192, 3, 32768,
                                    100, 100, G6_PAY);
            fwrite(h, 1, DS4_KVSTORE_FIXED_HEADER, fp);
        }
        fwrite(tb, 1, 4, fp);
        fwrite(texts[v], 1, strlen(texts[v]), fp);
        g6_write_pattern(fp, G6_PAY);
        fclose(fp);
        g6_payload_cksum_at(paths[v], hdrs[v] + strlen(texts[v]), &cksums[v]);
    }

    ds4_kvstore kc = {0};
    ds4_kvstore_options opt = ds4_kvstore_default_options();
    if (!ds4_kvstore_open(&kc, dir, 1, false, 0, opt, "harness", log_cb, NULL)) {
        fprintf(stderr, "open failed\n");
        exit(1);
    }
    ds4_kvstore_trailer_hooks hooks = {0};
    hooks.serialized_size = g6_size;
    hooks.write = g6_write;
    hooks.ext_flag = DS4_KVSTORE_EXT_TOOL_MAP;

    const uint64_t sizes[3] = {
        hdrs[0] + strlen(v3text) + G6_PAY + 64,
        hdrs[1] + strlen(v2text) + G6_PAY + 64,
        hdrs[2] + strlen(v1text) + G6_PAY + 64,
    };
    for (int round = 0; round < 2; round++) {
        for (int v = 0; v < 3; v++)
            ds4_kvstore_rewrite_trailer(&kc, paths[v], texts[v], &hooks);
        /* Idempotence is the point of round 2: trailer replaced, not
         * appended twice. */
        for (int v = 0; v < 3; v++) {
            char lbl[64];
            snprintf(lbl, sizeof(lbl), "v%d", 3 - v);
            FILE *fp = fopen(paths[v], "rb");
            if (!fp) { printf("  FAIL: %s reopened\n", lbl); exit(1); }
            uint8_t hdr[128];
            size_t got = fread(hdr, 1, 128, fp);
            fclose(fp);
            (void)got;
            uint64_t ck;
            g6_payload_cksum_at(paths[v], hdrs[v] + strlen(texts[v]), &ck);
            printf("  %s round %d: version=%u size=%llu (want %llu) payload %s\n",
                   lbl, round, hdr[3],
                   (unsigned long long)g6_file_size(paths[v]),
                   (unsigned long long)sizes[v],
                   ck == cksums[v] ? "INTACT" : "CLOBBERED");
            CHECK(hdr[3] == 3 - v, "header version preserved through trailer rewrite");
            CHECK(g6_file_size(paths[v]) == sizes[v],
                  "file size = envelope + one trailer (replaced, not appended)");
            CHECK(ck == cksums[v], "payload region byte-identical after trailer rewrite");
            uint32_t tb = ds4_kvstore_le_get32(hdr + hdrs[v] - 4);
            CHECK(tb == (uint32_t)strlen(texts[v]), "text-size word intact");
            if (v == 0) {
                char psha[41];
                ds4_kvstore_sha1_bytes_hex(ptext, strlen(ptext), psha);
                CHECK(memcmp(hdr + 72, psha, 40) == 0, "v3 parent_sha intact");
                CHECK(ds4_kvstore_le_get32(hdr + 112) == 4096, "v3 delta_from intact");
            }
            CHECK(hdr[6] & DS4_KVSTORE_EXT_TOOL_MAP, "ext flag recorded");
        }
    }
    ds4_kvstore_close(&kc);
    for (int v = 0; v < 3; v++) unlink(paths[v]);
    rmdir(dir);
}

/* ---- Scenario H: incompatible reuse-path unlink must not orphan a chain
 * child (review M-4).  existing_compatible replaced an on-disk file it
 * judged incompatible without checking the v3 dependents that reference it
 * by parent_sha — a child loads only through its parent, so the unlink
 * silently killed the child's span and left a dead, budget-charged file on
 * disk until eviction noticed.  The file must instead be kept for the
 * in-place overwrite (the store renames onto the same sha path). */
static void scenario_incompatible_dependents(void) {
    printf("== Scenario H: incompatible unlink respects chain dependents ==\n");
    char dir[] = "/tmp/kv-harness-h.XXXXXX";
    if (!mkdtemp(dir)) { perror("mkdtemp"); exit(1); }
    const uint64_t now = (uint64_t)time(NULL);
    const char *p = "scenario H incompatible parent text";
    const char *c = "scenario H dependent child text";
    stub_file(dir, p, 7, 1, 40960, now - 1000, 4000);
    stub_file_v3(dir, c, p, 2, 81920, 40960, now - 1000, 4000);

    ds4_kvstore kc = {0};
    ds4_kvstore_options opt = ds4_kvstore_default_options();
    if (!ds4_kvstore_open(&kc, dir, 1024, false, 0, opt,
                          "harness", log_cb, NULL)) {
        fprintf(stderr, "open failed\n"); exit(1);
    }
    char psha[41];
    ds4_kvstore_sha1_bytes_hex(p, strlen(p), psha);
    char ppath[512];
    snprintf(ppath, sizeof(ppath), "%s/%.40s.kv", dir, psha);
    /* Requested ctx 16384 < stored ctx 32768: incompatible. */
    CHECK(!ds4_kvstore_existing_compatible(&kc, ppath, psha, p, strlen(p),
                                           0, 2, 16384),
          "parent judged incompatible at smaller requested ctx");
    CHECK(file_exists(dir, p),
          "incompatible parent KEPT while a v3 dependent references it");
    CHECK(file_exists(dir, c), "dependent child untouched");

    unlink_text(dir, c);
    CHECK(!ds4_kvstore_existing_compatible(&kc, ppath, psha, p, strlen(p),
                                           0, 2, 16384),
          "still incompatible after the dependent died");
    CHECK(!file_exists(dir, p),
          "parent unlinked once no dependent references it");
    ds4_kvstore_close(&kc);
    unlink_text(dir, c); /* idempotent cleanup */
    rmdir(dir);
}

/* ---- Scenario I: one-pass convergence of a chained-ladder retirement
 * (review H-2).  retire_leaf unlinked victims in array-index order, so a
 * chain parent picked BEFORE its (still-live, also-victim) child was
 * DEFERRED: the file stayed, freed nothing, and the ladder drained only
 * ~1 file per refresh pass.  Victims must unlink leaf-ward (descending
 * delta depth) so a parent whose every child is in the victim set finds
 * children==0 when its turn comes and is freed in the SAME pass. */
static void scenario_retire_convergence(void) {
    printf("== Scenario I: chained-ladder retire converges in one pass ==\n");
    char dir[] = "/tmp/kv-harness-i.XXXXXX";
    if (!mkdtemp(dir)) { perror("mkdtemp"); exit(1); }
    const uint64_t now = (uint64_t)time(NULL);
    char z1[128], z2[128], z3[128], z4[128];
    snprintf(z1, sizeof(z1), "scenario I converged chain root");
    snprintf(z2, sizeof(z2), "%s rung two", z1);
    snprintf(z3, sizeof(z3), "%s rung three", z2);
    snprintf(z4, sizeof(z4), "%s rung four", z3);
    const char *w = "scenario I sibling singleton text";
    stub_file(dir, z1, 9, 1, 40960, now - 100000, 4000);
    stub_file_v3(dir, z2, z1, 2, 81920, 40960, now - 100000, 4000);
    stub_file_v3(dir, z3, z2, 2, 122880, 81920, now - 100000, 4000);
    stub_file_v3(dir, z4, z3, 2, 163840, 122880, now - 100000, 4000);
    stub_file(dir, w, 9, 1, 40960, now - 50000, 4000);

    ds4_kvstore kc = {0};
    ds4_kvstore_options opt = ds4_kvstore_default_options();
    opt.max_conversations = 0; /* cap engaged only for our pass */
    if (!ds4_kvstore_open(&kc, dir, 1024, false, 0, opt,
                          "harness", log_cb, NULL)) {
        fprintf(stderr, "open failed\n"); exit(1);
    }
    kc.opt.max_conversations = 1;
    g_any_defer_lines = 0;
    ds4_kvstore_eviction_context inc = {
        .text = "incoming unrelated request text",
        .text_len = sizeof("incoming unrelated request text") - 1,
        .model_id = 0, .quant_bits = 2, .ctx_size = 32768,
        .reject_different_quant = false,
    };
    /* Two non-active lineages (the 4-rung chain, the singleton) over a cap
     * of 1: the older leaf (chain frontier) retires.  Budget is ample, so
     * the over-cap path is the only eviction at work. */
    ds4_kvstore_evict(&kc, NULL, 0, &inc);
    CHECK(!file_exists(dir, z1), "chain root freed in the same retire pass");
    CHECK(!file_exists(dir, z2), "rung two freed in the same retire pass");
    CHECK(!file_exists(dir, z3), "rung three freed in the same retire pass");
    CHECK(!file_exists(dir, z4), "frontier rung freed");
    CHECK(file_exists(dir, w), "sibling lineage kept (cap met)");
    CHECK(g_any_defer_lines == 0, "same-pass retire produced zero deferrals");
    ds4_kvstore_close(&kc);
    const char *texts[] = { z1, z2, z3, z4, w };
    for (int i = 0; i < 5; i++) unlink_text(dir, texts[i]);
    rmdir(dir);
}

/* ---- Scenario J: disk-budget input hardening (review M-2) ---- */
static void scenario_budget_hardening(void) {
    printf("== Scenario J: budget clamp (no wrap), zero = documented default ==\n");
    char dir[] = "/tmp/kv-harness-j.XXXXXX";
    if (!mkdtemp(dir)) { perror("mkdtemp"); exit(1); }
    ds4_kvstore kc = {0};
    ds4_kvstore_options opt = ds4_kvstore_default_options();
    /* 2^44 MiB overflows the MiB→bytes multiply to EXACTLY zero before the
     * clamp: budget_bytes==0 short-circuits every eviction path, silently
     * disabling reclamation forever. */
    CHECK(ds4_kvstore_open(&kc, dir, (1ull << 44), false, 0, opt,
                           "harness", log_cb, NULL),
          "open with absurd budget succeeds");
    CHECK(kc.budget_bytes ==
          (UINT64_MAX / (1024ull * 1024ull)) * (1024ull * 1024ull),
          "absurd budget clamps to the largest representable value (never wraps)");
    ds4_kvstore_close(&kc);
    CHECK(ds4_kvstore_open(&kc, dir, 0, false, 0, opt,
                           "harness", log_cb, NULL),
          "open with budget 0 succeeds");
    CHECK(kc.budget_bytes == (uint64_t)DS4_KVSTORE_DEFAULT_MB * 1024ull * 1024ull,
          "budget 0 means the documented default (4 GiB), never disable");
    ds4_kvstore_close(&kc);
    rmdir(dir);
}

/* ---- Scenario K: store gate mirrors the load gate (review M-3) ---- */
static void scenario_quant_gates(void) {
    printf("== Scenario K: quantization gate mirroring ==\n");
    CHECK(ds4_kvstore_quant_bits_loadable(2) && ds4_kvstore_quant_bits_loadable(4),
          "loadable set keeps {2,4}");
    CHECK(!ds4_kvstore_quant_bits_loadable(0) &&
          !ds4_kvstore_quant_bits_loadable(1) &&
          !ds4_kvstore_quant_bits_loadable(3) &&
          !ds4_kvstore_quant_bits_loadable(5) &&
          !ds4_kvstore_quant_bits_loadable(6) &&
          !ds4_kvstore_quant_bits_loadable(8) &&
          !ds4_kvstore_quant_bits_loadable(16),
          "everything else is not loadable (store must not write q5+)");
    CHECK(ds4_kvstore_quant_bits_supported(5) &&
          ds4_kvstore_quant_bits_supported(6) &&
          ds4_kvstore_quant_bits_supported(8),
          "file-visibility set unchanged (q5+ files stay indexed, budget-charged)");
}

/* ---- Scenario L: eager frontier-supersede of off-grid snapshots ----
 * oMLX 0.7.0 a28e5a87 parity (.codebase-memory/omlx-v070-cache-policy.md
 * §3.1).  Evict/shutdown frontier snapshots each carry the full fixed GDN
 * [state][hist] floor (~140 MiB on Qwen3.8, FLOOR-20261001.md); once the
 * lineage frontier moved on, snapshots older than the newest tail_anchors
 * are dead weight — and with the 128 GiB default budget the pressure pass
 * effectively never runs, so nothing ever reclaims them.  The budget-
 * independent sweep (at open and ahead of every store's eviction pass)
 * must drop them, keep the newest tail_anchors per byte-prefix lineage,
 * and never touch continued ladder rungs, cold divergence anchors, delta
 * parents (children>0) or legacy files.  RED before the fix: the sweep
 * did not exist, so s1 survived the open. */
static void scenario_frontier_supersede(void) {
    printf("== Scenario L: eager frontier-supersede sweep ==\n");
    char dir[] = "/tmp/kv-harness-l.XXXXXX";
    if (!mkdtemp(dir)) { perror("mkdtemp"); exit(1); }

    const uint64_t now = (uint64_t)time(NULL);
    /* Byte-prefix chain: each snapshot text extends the previous one, so
     * "newer same-lineage snapshot" == strict text-prefix extension. */
    const char *root = "scenario L lineage prompt root";
    char s1[160], s2[160], s3[160], s4[160];
    snprintf(s1, sizeof(s1), "%s evict snapshot one", root);
    snprintf(s2, sizeof(s2), "%s shutdown snapshot two", s1);
    snprintf(s3, sizeof(s3), "%s evict snapshot three", s2);
    snprintf(s4, sizeof(s4), "%s shutdown snapshot four", s3);

    stub_file(dir, root, 7, DS4_KVSTORE_REASON_CONTINUED, 16384, now - 5000, 4000);
    stub_file(dir, s1, 7, DS4_KVSTORE_REASON_EVICT, 17032, now - 4000, 4000);
    stub_file(dir, s2, 7, DS4_KVSTORE_REASON_SHUTDOWN, 17780, now - 3000, 4000);
    stub_file(dir, s3, 7, DS4_KVSTORE_REASON_EVICT, 18444, now - 2000, 4000);

    g_supersede_lines = 0;
    g_any_defer_lines = 0;
    ds4_kvstore kc = {0};
    ds4_kvstore_options opt = ds4_kvstore_default_options();
    opt.retire_grace_seconds = 3600;
    /* ~97 GiB budget vs ~16 KB of stubs: no pressure pass can fire; only a
     * budget-independent sweep may delete anything here. */
    if (!ds4_kvstore_open(&kc, dir, 100000, false, 0, opt,
                          "harness", log_cb, NULL)) {
        fprintf(stderr, "open failed\n");
        exit(1);
    }
    CHECK(!file_exists(dir, s1),
          "superseded evict snapshot dropped at open under zero budget pressure");
    CHECK(file_exists(dir, s2), "second-newest snapshot kept (fallback window)");
    CHECK(file_exists(dir, s3), "newest snapshot kept (live frontier)");
    CHECK(file_exists(dir, root), "continued ladder rung untouched by the sweep");
    CHECK(g_supersede_lines == 1, "exactly one frontier-superseded unlink logged");
    CHECK(g_any_defer_lines == 0, "children==0-only sweep produced zero deferrals");

    /* Next snapshot in the same lineage: the window advances and s2 — now
     * two snapshots back — drops on the next open-time sweep. */
    stub_file(dir, s4, 7, DS4_KVSTORE_REASON_SHUTDOWN, 19200, now - 10, 4000);
    g_supersede_lines = 0;
    ds4_kvstore_close(&kc);
    if (!ds4_kvstore_open(&kc, dir, 100000, false, 0, opt,
                          "harness", log_cb, NULL)) {
        fprintf(stderr, "reopen failed\n");
        exit(1);
    }
    CHECK(!file_exists(dir, s2), "window advanced: s2 dropped once two newer snapshots exist");
    CHECK(file_exists(dir, s3) && file_exists(dir, s4), "newest two snapshots kept");
    CHECK(g_supersede_lines == 1, "exactly one further supersede logged");

    ds4_kvstore_close(&kc);
    const char *texts[] = { root, s1, s2, s3, s4 };
    for (int i = 0; i < 5; i++) unlink_text(dir, texts[i]);
    rmdir(dir);
}

int main(void) {
    scenario_switch_churn();
    scenario_idle_retired();
    scenario_delta_parent_deferred();
    scenario_divergence_logic();
    scenario_deferral_backoff();
    scenario_trailer_rewrite();
    scenario_incompatible_dependents();
    scenario_retire_convergence();
    scenario_budget_hardening();
    scenario_quant_gates();
    scenario_frontier_supersede();
    if (g_failures) {
        fprintf(stderr, "kv_policy_harness: %d failure(s)\n", g_failures);
        return 1;
    }
    printf("kv_policy_harness: all scenarios passed\n");
    return 0;
}
