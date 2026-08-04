/* Standalone unit tests for the speculative-decoding rejection-sampling
 * kernel added for PLAN-DSPARK-TEMP-SPEC.md (M1).  Engine-free: no model
 * load, no GPU.  Links against ds4_cpu_test_hooks.o (NO_GPU + TEST_HOOKS).
 */
#include "../ds4.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures;

#define CHECK(cond, ...) do {                                                 \
    if (!(cond)) {                                                            \
        fprintf(stderr, "FAIL: " __VA_ARGS__);                               \
        fputc('\n', stderr);                                                  \
        failures++;                                                           \
    }                                                                         \
} while (0)

static uint64_t spec_rng_next(uint64_t *s) {
    uint64_t x = *s;
    if (x == 0) x = 0x9e3779b97f4a7c15ULL;
    x ^= x >> 12; x ^= x << 25; x ^= x >> 27;
    *s = x;
    return x;
}

static float spec_rng_f32(uint64_t *s) {
    return (float)((spec_rng_next(s) >> 40) & 0xffffffu) / 16777216.0f;
}

/* Sample a token from a normalized distribution via inverse CDF. */
static int sample_dist(const float *probs, uint32_t n, uint64_t *rng) {
    float r = spec_rng_f32(rng);
    for (uint32_t i = 0; i < n; i++) {
        r -= probs[i];
        if (r <= 0.0f) return (int)i;
    }
    for (int i = (int)n - 1; i >= 0; i--) {
        if (probs[i] > 0.0f) return i;
    }
    return 0;
}

static void test_accept_prob_basic(void) {
    CHECK(ds4_test_spec_accept_prob(0.5f, 0.25f) == 1.0f, "p>q must cap at 1");
    const float half = ds4_test_spec_accept_prob(0.2f, 0.4f);
    CHECK(half > 0.499f && half < 0.501f, "min(1,p/q) ratio wrong: %f", half);
    CHECK(ds4_test_spec_accept_prob(0.0f, 0.5f) == 0.0f, "p=0 must be 0");
    CHECK(ds4_test_spec_accept_prob(0.5f, 0.0f) == 0.0f, "q=0 must be 0");
    CHECK(ds4_test_spec_accept_prob(-0.1f, 0.5f) == 0.0f, "p<0 must be 0");
    CHECK(ds4_test_spec_accept_prob(0.5f, -0.1f) == 0.0f, "q<0 must be 0");
    const float eq = ds4_test_spec_accept_prob(0.3f, 0.3f);
    CHECK(eq > 0.999f && eq <= 1.0f, "p==q must be ~1, got %f", eq);
}

static void test_accept_frequency(void) {
    const float p = 0.20f, q = 0.50f;
    const float expect = p / q; /* 0.4 */
    uint64_t rng = 12345;
    int accepted = 0;
    const int N = 20000;
    for (int i = 0; i < N; i++) accepted += ds4_test_spec_accept_token(p, q, &rng);
    const float rate = (float)accepted / (float)N;
    CHECK(fabsf(rate - expect) < 0.02f, "acceptance rate %.4f != %.4f", rate, expect);

    int always = 0;
    for (int i = 0; i < 1000; i++) always += ds4_test_spec_accept_token(0.7f, 0.3f, &rng);
    CHECK(always == 1000, "p>=q must always accept (%d/1000)", always);

    int never = 0;
    for (int i = 0; i < 1000; i++) never += ds4_test_spec_accept_token(0.0f, 0.3f, &rng);
    CHECK(never == 0, "p=0 must never accept (%d/1000)", never);
}

static void test_residual_distribution(void) {
    /* Single positive residual -> deterministic token. */
    const float p[4] = {0.5f, 0.3f, 0.2f, 0.0f};
    const float q[4] = {0.2f, 0.4f, 0.3f, 0.1f};
    uint64_t rng = 777;
    int counts[4] = {0};
    const int N = 4000;
    for (int i = 0; i < N; i++) {
        const int t = ds4_test_spec_residual_sample(p, q, 4, &rng);
        CHECK(t >= 0 && t < 4, "residual token out of range: %d", t);
        if (t >= 0 && t < 4) counts[t]++;
    }
    CHECK(counts[0] == N, "single-residual must always pick token 0 (%d/%d)", counts[0], N);

    /* Genuine two-way split: p=[0.6,0.4], q=[0.5,0.1]
     * residual=[0.1,0.3] -> P(0)=0.25, P(1)=0.75. */
    const float p4[2] = {0.6f, 0.4f};
    const float q4[2] = {0.5f, 0.1f};
    rng = 31337;
    int ones = 0;
    const int M = 8000;
    for (int i = 0; i < M; i++) {
        if (ds4_test_spec_residual_sample(p4, q4, 2, &rng) == 1) ones++;
    }
    const float frac1 = (float)ones / (float)M;
    CHECK(fabsf(frac1 - 0.75f) < 0.03f, "residual split %.4f != 0.75", frac1);
}

static void test_softmax_full(void) {
    float logits[4] = {1.0f, 1.0f, 1.0f, 1.0f};
    float out[4] = {0};
    CHECK(ds4_test_spec_softmax_full(logits, 4, out) == 0, "uniform softmax failed");
    for (int i = 0; i < 4; i++) {
        CHECK(fabsf(out[i] - 0.25f) < 1e-5f, "uniform prob %f != 0.25", out[i]);
    }

    float logits2[3] = {10.0f, 0.0f, 0.0f};
    float out2[3] = {0};
    CHECK(ds4_test_spec_softmax_full(logits2, 3, out2) == 0, "dominant softmax failed");
    CHECK(out2[0] > 0.999f, "dominant mass %f not concentrated", out2[0]);
    const float sum = out2[0] + out2[1] + out2[2];
    CHECK(fabsf(sum - 1.0f) < 1e-4f, "softmax sum %f != 1", sum);
}

static void test_target_dist_matches_sampler(void) {
    /* Differential test: ds4_spec_target_dist must equal the distribution
     * that ds4_test_sample_logits (sample_top_p_min_p) draws from.  Keep
     * n_vocab <= 512 so the fast top-p path keeps every candidate. */
    const uint32_t n_vocab = 200;
    float *logits = malloc((size_t)n_vocab * sizeof(logits[0]));
    float *p_ref = malloc((size_t)n_vocab * sizeof(p_ref[0]));
    float *scratch = malloc((size_t)n_vocab * sizeof(scratch[0]));
    int *emp = malloc((size_t)n_vocab * sizeof(emp[0]));
    CHECK(logits && p_ref && scratch && emp, "alloc failed");
    if (!logits || !p_ref || !scratch || !emp) return;

    uint64_t gen = 20260804;
    for (uint32_t i = 0; i < n_vocab; i++) {
        logits[i] = (float)((int)(spec_rng_next(&gen) % 1000) / 100.0);
    }

    struct cfg { float temp; int top_k; float top_p; float min_p; };
    const struct cfg cfgs[] = {
        {1.0f, 0, 1.0f, 0.0f},
        {1.0f, 0, 0.95f, 0.05f},
        {0.7f, 0, 0.9f, 0.1f},
        {1.0f, 50, 0.95f, 0.05f},
        {0.5f, 20, 1.0f, 0.0f},
    };

    for (size_t c = 0; c < sizeof(cfgs) / sizeof(cfgs[0]); c++) {
        const struct cfg cf = cfgs[c];
        const int support = ds4_spec_target_dist(logits, n_vocab, cf.temp,
                                                 cf.top_k, cf.top_p, cf.min_p,
                                                 p_ref);
        CHECK(support > 0, "cfg %zu: empty support", c);
        float ref_sum = 0.0f;
        for (uint32_t i = 0; i < n_vocab; i++) ref_sum += p_ref[i];
        CHECK(fabsf(ref_sum - 1.0f) < 1e-3f, "cfg %zu: ref sum %f", c, ref_sum);

        memset(emp, 0, (size_t)n_vocab * sizeof(emp[0]));
        uint64_t rng = 987654321 + c;
        const int N = 20000;
        for (int i = 0; i < N; i++) {
            const int t = ds4_test_sample_logits(logits, n_vocab, cf.temp,
                                                 cf.top_k, cf.top_p, cf.min_p,
                                                 &rng, scratch);
            CHECK(t >= 0 && (uint32_t)t < n_vocab, "cfg %zu: bad sample %d", c, t);
            if (t >= 0 && (uint32_t)t < n_vocab) emp[t]++;
        }
        for (uint32_t i = 0; i < n_vocab; i++) {
            const float e = (float)emp[i] / (float)N;
            const float r = p_ref[i];
            const float sigma = sqrtf((r > 0 ? r : 1e-6f) * (1.0f - (r > 0 ? r : 0.0f)) / (float)N);
            const float tol = 5.0f * sigma + 0.002f;
            CHECK(fabsf(e - r) <= tol,
                  "cfg %zu token %u: empirical %.4f vs ref %.4f (tol %.4f)",
                  c, i, e, r, tol);
        }
    }

    const int gs = ds4_spec_target_dist(logits, n_vocab, 0.0f, 0, 1.0f, 0.0f, p_ref);
    CHECK(gs == 1, "greedy support size %d != 1", gs);
    int best = 0;
    for (uint32_t i = 1; i < n_vocab; i++) if (logits[i] > logits[best]) best = (int)i;
    CHECK(p_ref[best] == 1.0f, "greedy delta not at argmax %d", best);

    free(logits); free(p_ref); free(scratch); free(emp);
}

static void test_one_step_identity(void) {
    /* Rejection-sampling lemma: draw x~q, accept with min(1,p(x)/q(x)),
     * else sample norm(max(0,p-q)); the result must follow p exactly. */
    const uint32_t n = 8;
    const float p[8] = {0.28f, 0.02f, 0.15f, 0.05f, 0.20f, 0.10f, 0.12f, 0.08f};
    const float q[8] = {0.10f, 0.20f, 0.05f, 0.15f, 0.10f, 0.15f, 0.15f, 0.10f};
    uint64_t rng = 555;
    int counts[8] = {0};
    const int N = 40000;
    for (int i = 0; i < N; i++) {
        const int x = sample_dist(q, n, &rng);
        int out;
        if (ds4_test_spec_accept_token(p[x], q[x], &rng)) {
            out = x;
        } else {
            out = ds4_test_spec_residual_sample(p, q, n, &rng);
        }
        CHECK(out >= 0 && out < (int)n, "identity token out of range: %d", out);
        if (out >= 0 && out < (int)n) counts[out]++;
    }
    for (uint32_t i = 0; i < n; i++) {
        const float e = (float)counts[i] / (float)N;
        const float sigma = sqrtf(p[i] * (1.0f - p[i]) / (float)N);
        const float tol = 5.0f * sigma + 0.004f;
        CHECK(fabsf(e - p[i]) <= tol,
              "identity token %u: empirical %.4f vs p %.4f (tol %.4f)",
              i, e, p[i], tol);
    }
}

int main(void) {
    test_accept_prob_basic();
    test_accept_frequency();
    test_residual_distribution();
    test_softmax_full();
    test_target_dist_matches_sampler();
    test_one_step_identity();
    if (failures) {
        fprintf(stderr, "test_spec_rejection: %d failure(s)\n", failures);
        return 1;
    }
    puts("test_spec_rejection: ok");
    return 0;
}
