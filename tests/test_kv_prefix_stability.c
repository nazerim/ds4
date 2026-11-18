/*
 * test_kv_prefix_stability.c — does a KV checkpoint's exact-token prefix plus a
 * standalone tokenisation of the remaining TEXT suffix reproduce the token ids
 * of tokenising the whole rendered prompt in one pass?
 *
 * REAL CALL PATH (verified in ds4_server.c)
 * ----------------------------------------
 * The production caller chain is:
 *
 *   continue_after_invalid_dsml            (ds4_server.c:14983)
 *     -> build_invalid_tool_call_error_suffix (ds4_server.c:14585)
 *          -> build_invalid_dsml_tool_error_suffix   (ds4_server.c:14499)
 *          -> build_invalid_qwen_tool_error_suffix   (ds4_server.c:14560)
 *     -> append_rendered_suffix_to_live_session   (ds4_server.c:14825)
 *          -> build_prompt_from_exact_prefix_and_text_suffix (ds4_server.c:14838)
 *               -> ds4_kvstore_build_prompt_from_exact_prefix_and_text_suffix
 *                    (ds4_kvstore.c:2539)
 *
 * So the suffix is the WHOLE string returned by build_invalid_tool_call_error_suffix,
 * appended to the live session's exact token history.  The helper copies the exact
 * prefix and tokenises ONLY the suffix text:
 *
 *   void ds4_kvstore_build_prompt_from_exact_prefix_and_text_suffix(
 *           ds4_engine *engine,
 *           const ds4_tokens *exact_prefix,
 *           const char *suffix_text,
 *           ds4_tokens *out);                       // ds4_kvstore.h:278
 *
 * (ds4_kvstore.c:2539).  Internally it calls
 *
 *   void ds4_tokenize_rendered_chat(ds4_engine *e, const char *text,
 *                                   ds4_tokens *out);  // ds4.h:376
 *
 * on `suffix_text` alone.  The server tokenises the *whole* prompt with the
 * very same entry point (ds4_server.c: `ds4_tokenize_rendered_chat(e,
 * r->prompt_text, &r->prompt)`).
 *
 * WHY THIS IS THE HAZARDOUS SHAPE
 * -------------------------------
 * - build_invalid_dsml_tool_error_suffix (14499) starts with the UNSPACED marker
 *   "<｜end▁of▁sentence｜><｜User｜><tool_result>".  Those are registered special
 *   tokens => a hard token boundary => SAFE (kept here as the control).
 * - build_invalid_qwen_tool_error_suffix (14560) starts with the SPACED marker
 *   string "< |im_end| >\n< |im_start| >user\n" followed by English prose.  These
 *   spaced strings may NOT be registered specials, i.e. they may tokenise as
 *   ordinary text => cross-boundary BPE merges are plausible.
 *   THE PRODUCTION MODEL IS QWEN SYNTAX, and this suffix is appended on EVERY
 *   malformed tool call (continue_after_invalid_dsml -> ... -> 14838).
 *
 * If A != B for the qwen-* cases below, then the appended error text is
 * tokenised differently than the same bytes inside a cold full-prompt
 * tokenisation: the model can be conditioned on a different token sequence
 * than the text implies, on a code path reachable from malformed tool calls in
 * production.  That is the question this harness settles.
 *
 * METHOD (comparison is NOT weakened)
 * -----------------------------------
 *   A = ds4_tokenize_rendered_chat(whole rendered string in one pass)
 *   B = ds4_kvstore_build_prompt_from_exact_prefix_and_text_suffix(
 *           engine, exact_prefix, suffix_text, &out)
 * where exact_prefix = A truncated at the boundary token count.  That stand-in
 * is GENEROUS to the hypothesis: a real checkpoint stores the live session's
 * tokens (the prefix's own tokenisation), but using A's truncation means that if
 * even this differs, it is a real bug (not an artefact of prefix tokenisation).
 * The harness also reports whether the prefix itself is stable
 * (A[0..k) == tokenize(prefix)) for transparency.
 *
 * FULL engine load required
 * -------------------------
 * A full engine load is mandatory.  `inspect_only` early-returns before the
 * tokenizer is initialised, so a probe built on it would compare against an
 * empty vocab and report a meaningless PASS.  Modelled on
 * tests/tokenizer_fp_probe.c, which loads the engine with ds4_engine_open()
 * and bails out when ds4_engine_vocab_size() <= 0.
 *
 * Run with the server STOPPED: the engine takes a process lock and a live
 * instance will refuse to open (Error 2).  `./ds4 --inspect` is ALSO blocked by
 * the process lock, so nothing in this area can be probed without stopping the
 * server.  Use ./ds4-server.sh stop first.
 *
 * No detokeniser in ds4.h
 * -----------------------
 * ds4.h has no `ds4_detokenize`.  The closest public helpers are
 *   const char *ds4_token_text(ds4_engine *e, int token, size_t *len);  // ds4.h:392
 * (per-token, NUL-terminated, caller must not free) and, in ds4_kvstore.h,
 *   char *ds4_kvstore_render_tokens_text(ds4_engine *engine,
 *                                        const ds4_tokens *tokens,
 *                                        size_t *out_len);          // ds4_kvstore.h:272
 * This harness uses ds4_token_text() to rebuild the decoded text around the
 * boundary (no extra link dependency, no ownership concerns).
 *
 * Corpus format
 * -------------
 * --corpus <file> reads UTF-8 text, one conversation per line.  Each line is
 * used as the *suffix* (the text that immediately follows the checkpoint cut),
 * wrapped against the QWEN prefix.  This puts real text through the standalone
 * suffix tokenisation, which is exactly the code path under test.  Blank lines
 * are skipped.
 *
 * build (fork tree, Darwin/Metal):
 *   cc -O2 -std=c11 -o /tmp/kv_prefix_stability tests/test_kv_prefix_stability.c \
 *      ds4.o ds4_help.o ds4_kvstore.o rax.o ds4_gpu_args.o ds4_image.o \
 *      ds4_distributed.o ds4_tp.o ds4_ssd.o ds4_metal.o ds4_layer_pack.o ds4_engram.o \
 *      -lm -pthread -framework Foundation -framework Metal
 *
 * NOTE (verified by linking): ds4_engram.o is REQUIRED. The list above is copied from
 * tests/tokenizer_fp_probe.c, whose list is stale -- without it the link fails on
 * _ds4_engram_hash / _ds4_engram_history_reset / _ds4_engram_layout_valid (referenced from ds4.o).
 *
 * exit codes (each outcome distinct, so an operational failure is never read as a verdict):
 *   0 PASS           all cases had a valid boundary and identical ids
 *   1 FAIL           a valid-boundary case produced different ids
 *   3 INVALID        a case had an invalid boundary (answers no question)
 *   4 usage error    bad arguments
 *   5 could not run  model/vocab/corpus unavailable -- NOT a probe result
 *   2                RESERVED: libds4 calls exit(2) directly on startup failure
 *                    (instance lock held by a running server, bad model path), so
 *                    2 is deliberately unused here -- seeing 2 means the engine
 *                    aborted and NO case ran. Verified: a bad model path exits 2.
 *
 * usage:
 *   ./kv_prefix_stability <model.gguf> [--corpus <file>] [--cpu | --backend metal|cpu|cuda]
 *
 * Exit code: 0 = clean PASS (every case had a valid boundary and equal ids),
 * 1 = FAIL (a valid-boundary case differs), 2 = INVALID (a case had an invalid
 * boundary, so the comparison answers no question; 2 takes precedence over 1).
 * Progress goes to stderr; results go to stdout.  The last stdout line is one
 * of 'PREFIX_STABILITY: PASS', 'PREFIX_STABILITY: FAIL n=<count>', or
 * 'PREFIX_STABILITY: INVALID b=<count>[ n=<count>]'.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

#include "../ds4.h"
#include "../ds4_kvstore.h"

/* ---------------------------------------------------------------------------
 * Rendered-chat markers, quoted from the real builders in ds4_server.c.
 *
 * QWEN (build_invalid_qwen_tool_error_suffix, ds4_server.c:14560):
 *   Spaced markers "< |im_end| >" / "< |im_start| >user" that may NOT be
 *   registered specials.  This is the production shape.
 *
 * DSML (build_invalid_dsml_tool_error_suffix, ds4_server.c:14499):
 *   Unspaced "<｜end▁of▁sentence｜><｜User｜><tool_result>" — registered
 *   specials => hard boundary => safe control.
 * ------------------------------------------------------------------------- */

/* Reconstruction of the FIXED part of the suffix returned by
 * build_invalid_qwen_tool_error_suffix (ds4_server.c:14560-14583) for a
 * non-think request, with a fixed stand-in for the variable `detail` argument
 * (the real builder inserts `": <detail>"` after "Tool error: invalid Qwen
 * tool call" at 14566-14568).  The leading "< |im_end| >\n< |im_start| >user"
 * at 14577-14578 is EXACTLY what the builder emits; the tail reproduces
 * append_qwen_tool_response (3641) + append_qwen_generation_prompt (3701).
 * The boundary test depends only on that exact leading marker. */
static const char QWEN_INVALID_TOOL_SUFFIX[] =
    "< |im_end| >\n"
    "< |im_start| >user\n"
    "<tool_response>\n"
    "Tool error: invalid Qwen tool call\n"
    "The previous assistant output was not executed because the "
    "<tool_call><function=...> syntax was malformed. Emit a new valid "
    "<tool_call>, or answer normally if no tool is needed.\n"
    "</tool_response>"
    "< |im_end| >\n"
    "< |im_start| >assistant\n"
    "<think>\n\n"
    "```\n\n";

/* Reconstruction of the FIXED part of the suffix returned by
 * build_invalid_dsml_tool_error_suffix (ds4_server.c:14499-14528) for a
 * non-think request, with a fixed stand-in for the variable `detail` argument
 * (inserted at 14505-14508).  The leading "<｜end▁of▁sentence｜><｜User｜><tool_result>"
 * at 14521 is EXACTLY what the builder emits; the body reproduces
 * append_tool_result_text (3003). */
static const char DSML_INVALID_TOOL_SUFFIX[] =
    "<｜end▁of▁sentence｜><｜User｜><tool_result>"
    "Tool error: invalid DSML tool call\n"
    "The previous assistant output was not executed because the DSML syntax "
    "was malformed. Emit a new valid DSML tool call, or answer normally if no "
    "tool is needed."
    "</tool_result><｜Assistant｜>"
    "```";

/* ---------------------------------------------------------------------------
 * Prefixes: a realistic multi-turn rendered conversation ending where a
 * checkpoint would cut (right after the model's malformed tool-call output,
 * before the error suffix is appended).  QWEN matches append_qwen_* (3701);
 * DSML matches render_deepseek_chat_prompt_text (3250).
 * ------------------------------------------------------------------------- */
static const char QWEN_PREFIX[] =
    "< |im_start| >system\n"
    "You are a helpful assistant."
    "< |im_end| >\n"
    "< |im_start| >user\n"
    "What is the capital of France?"
    "< |im_end| >\n"
    "< |im_start| >assistant\n"
    "<think>\n\n"
    "The capital of France is Paris.\n"
    "< |im_end| >\n"
    "< |im_start| >user\n"
    "What is 2+2?"
    "< |im_end| >\n"
    "< |im_start| >assistant\n"
    "<think>\n\n"
    "<tool_call><function=get_capital({\"city\": \"Paris\"})";

static const char DSML_PREFIX[] =
    "<｜begin▁of▁sentence｜>"
    "You are a helpful assistant."
    "<｜User｜>What is the capital of France?"
    "<｜Assistant｜>```"
    "The capital of France is Paris."
    "<｜end▁of▁sentence｜>"
    "<｜User｜>What is 2+2?"
    "<｜Assistant｜>```"
    "<tool_call><function=get_capital><parameter=city>Paris";

/* ---------------------------------------------------------------------------
 * Cases.  The suffix is the exact text a real builder returns (qwen-invalid-
 * tool-suffix, dsml-invalid-tool-suffix) or a boundary-adjacent variant that
 * probes the same byte pairs (newline+space, plain prose, digit, hyphen,
 * multibyte).  control-marker-led is built at runtime from a genuine control
 * token id (safe control).
 * ------------------------------------------------------------------------- */
typedef enum {
    PREFIX_QWEN,
    PREFIX_DSML,
} prefix_kind;

typedef struct {
    const char *name;
    prefix_kind kind;
    const char *suffix;   /* NULL => control-marker-led, built at runtime */
} kv_prefix_case;

static const kv_prefix_case builtin_cases[] = {
    { "qwen-invalid-tool-suffix",
      PREFIX_QWEN, QWEN_INVALID_TOOL_SUFFIX },
    { "qwen-suffix-leading-newline-space",
      PREFIX_QWEN, "\n The tool call was invalid." },
    { "qwen-suffix-leading-prose",
      PREFIX_QWEN, "The tool call was invalid." },
    { "qwen-suffix-leading-digit",
      PREFIX_QWEN, "3 tool calls were malformed." },
    { "qwen-suffix-leading-hyphen",
      PREFIX_QWEN, "- the tool call was invalid" },
    { "qwen-suffix-leading-multibyte",
      PREFIX_QWEN,
      "\xc3\xa9" "clair \xe4\xbd\xa0\xe5\xa5\xbd \xf0\x9f\x8e\x89 tool call invalid" },
    { "dsml-invalid-tool-suffix",
      PREFIX_DSML, DSML_INVALID_TOOL_SUFFIX },
    { "control-marker-led",
      PREFIX_QWEN, NULL },   /* runtime: genuine user control token text */
};

static const size_t builtin_case_count =
    sizeof(builtin_cases) / sizeof(builtin_cases[0]);

/* Radius (in tokens) of the decode window printed around the boundary. */
#define WINDOW_RADIUS 12

static void usage(const char *argv0) {
    fprintf(stderr,
            "usage: %s <model.gguf> [--corpus <file>] "
            "[--cpu | --backend metal|cpu|cuda]\n",
            argv0);
}

static const char *prefix_for_kind(prefix_kind kind) {
    return kind == PREFIX_DSML ? DSML_PREFIX : QWEN_PREFIX;
}

/* Rebuild the decoded text for tokens [start, end) into buf (NUL-terminated). */
static void decode_window(ds4_engine *e, const ds4_tokens *toks,
                          int start, int end, char *buf, size_t cap) {
    if (!buf || cap == 0) return;
    buf[0] = '\0';
    if (start < 0) start = 0;
    if (end > toks->len) end = toks->len;
    size_t off = 0;
    for (int i = start; i < end && off + 1 < cap; i++) {
        size_t len = 0;
        const char *piece = ds4_token_text(e, toks->v[i], &len);
        if (!piece || len == 0) continue;
        if (len > cap - 1 - off) len = cap - 1 - off;
        memcpy(buf + off, piece, len);
        off += len;
        buf[off] = '\0';
    }
}

/* Print token ids in [start, end), clamped to the array, with a label. */
static void print_token_range(const char *label, const ds4_tokens *toks,
                              int start, int end) {
    if (start < 0) start = 0;
    if (end > toks->len) end = toks->len;
    printf("  %s[%d..%d):", label, start, end);
    for (int i = start; i < end; i++) printf(" %d", toks->v[i]);
    printf("\n");
}

/* Print the 12-token window around the boundary (existing reporting). */
static void print_token_window(const char *label, const ds4_tokens *toks,
                               int center, int radius) {
    print_token_range(label, toks, center - radius, center + radius);
}

/*
 * Run one case.  A case is PASS only when the boundary is valid (A[0..k) ==
 * tokenize(prefix)) AND A == B.  A case with an invalid boundary is INVALID
 * (the comparison is meaningless) and never counts as a PASS; a valid boundary
 * with A != B is FAIL.  *fail_count and *invalid_count are updated in place.
 *
 * A = ds4_tokenize_rendered_chat(prefix ++ suffix)            (one pass)
 * B = ds4_kvstore_build_prompt_from_exact_prefix_and_text_suffix(
 *         engine, exact_prefix, suffix, &out)                 (server)
 * exact_prefix = A truncated at k = tokenize_rendered_chat(prefix).len.
 */
static void run_case(ds4_engine *e, const char *name,
                     prefix_kind kind, const char *suffix_in,
                     int *fail_count, int *invalid_count) {
    const char *prefix = prefix_for_kind(kind);
    char *owned_suffix = NULL;
    const char *suffix = suffix_in;

    if (!suffix) {
        /* control-marker-led: start the suffix with a genuine control token's
         * surface text (ds4_token_user), so the tokenizer sees a registered
         * special at the boundary — the safe control. */
        size_t tlen = 0;
        const char *utext = ds4_token_text(e, ds4_token_user(e), &tlen);
        size_t cap = tlen + 64;
        owned_suffix = malloc(cap);
        if (!owned_suffix) {
            fprintf(stderr, "out of memory building control suffix '%s'\n", name);
            if (fail_count) (*fail_count)++;
            printf("case=%s |A|=NA |B|=NA equal=no (oom)\n", name);
            return;
        }
        snprintf(owned_suffix, cap, "%.*s leading prose after control marker",
                 (int)tlen, utext);
        suffix = owned_suffix;
    }

    size_t prefix_len = strlen(prefix);
    size_t suffix_len = strlen(suffix);
    char *full = malloc(prefix_len + suffix_len + 1);
    if (!full) {
        fprintf(stderr, "out of memory building full string for '%s'\n", name);
        free(owned_suffix);
        if (fail_count) (*fail_count)++;
        printf("case=%s |A|=NA |B|=NA equal=no (oom)\n", name);
        return;
    }
    memcpy(full, prefix, prefix_len);
    memcpy(full + prefix_len, suffix, suffix_len);
    full[prefix_len + suffix_len] = '\0';

    fprintf(stderr,
            "case '%s': prefix=%zu bytes, suffix=%zu bytes, full=%zu bytes\n",
            name, prefix_len, suffix_len, prefix_len + suffix_len);

    /* A = one-pass tokenisation of the whole rendered prompt (server path). */
    ds4_tokens A = {0};
    ds4_tokenize_rendered_chat(e, full, &A);

    /* Stand-in for the exact token frontier a restored checkpoint supplies:
     * A truncated at the boundary token count k = |tokenize(prefix)|.  This is
     * GENEROUS to the hypothesis (see header): if even this differs, it is a
     * real bug. */
    ds4_tokens P = {0};
    ds4_tokenize_rendered_chat(e, prefix, &P);
    int k = P.len;
    if (k > A.len) k = A.len;
    if (k < 0) k = 0;

    ds4_tokens exact_prefix = {0};
    for (int i = 0; i < k; i++) ds4_tokens_push(&exact_prefix, A.v[i]);

    /* B = the function under test, verbatim. */
    ds4_tokens B = {0};
    ds4_kvstore_build_prompt_from_exact_prefix_and_text_suffix(
        e, &exact_prefix, suffix, &B);

    /* Compare A vs B. */
    bool equal = (A.len == B.len);
    int first_diff = -1;
    int n = A.len < B.len ? A.len : B.len;
    for (int i = 0; i < n; i++) {
        if (A.v[i] != B.v[i]) { first_diff = i; break; }
    }
    if (equal && first_diff >= 0) equal = false;
    if (!equal && first_diff < 0) first_diff = n;

    printf("case=%s |A|=%d |B|=%d equal=%s\n",
           name, A.len, B.len, equal ? "yes" : "no");

    /* Explicit boundary view: last 8 prefix tokens, first 8 A/B tokens after
     * the boundary — this is where a merge across the cut becomes visible. */
    print_token_range("prefix_last8(A)", &A, k - 8, k);
    print_token_range("A_after8", &A, k, k + 8);
    print_token_range("B_after8", &B, k, k + 8);

    /* Boundary self-check: the A-vs-B comparison is only meaningful if the
     * prefix slice A[0..k) actually equals tokenize(prefix).  If it does not,
     * A and B do not share a boundary, the comparison answers no question, and
     * the case is INVALID — it can never count as a PASS. */
    bool prefix_stable = (P.len == k);
    if (prefix_stable) {
        for (int i = 0; i < k; i++) {
            if (A.v[i] != P.v[i]) { prefix_stable = false; break; }
        }
    }
    fprintf(stderr, "  prefix_slice_matches_prefix_tokenization=%s (k=%d)\n",
            prefix_stable ? "yes" : "no", k);
    if (!prefix_stable) {
        printf("  boundary_valid=no\n");
        if (invalid_count) (*invalid_count)++;
    }

    if (!equal) {
        /* Only a valid-boundary mismatch is a FAIL; an invalid boundary is
         * INVALID regardless of A/B (the comparison is meaningless). */
        if (prefix_stable) {
            if (fail_count) (*fail_count)++;
        }
        printf("  first_differing_index=%d\n", first_diff);
        print_token_window("A", &A, k, WINDOW_RADIUS);
        print_token_window("B", &B, k, WINDOW_RADIUS);
        char buf[512];
        decode_window(e, &A, k - WINDOW_RADIUS, k + WINDOW_RADIUS,
                      buf, sizeof(buf));
        printf("  A_decoded_around_boundary=\"%s\"\n", buf);
        decode_window(e, &B, k - WINDOW_RADIUS, k + WINDOW_RADIUS,
                      buf, sizeof(buf));
        printf("  B_decoded_around_boundary=\"%s\"\n", buf);
    }

    ds4_tokens_free(&A);
    ds4_tokens_free(&P);
    ds4_tokens_free(&exact_prefix);
    ds4_tokens_free(&B);
    free(full);
    free(owned_suffix);
}

/* Run every built-in case.  *fail_count and *invalid_count are updated in
 * place; the return value is the number of failing cases (for diagnostics). */
static int run_builtin_cases(ds4_engine *e, int *fail_count, int *invalid_count) {
    for (size_t i = 0; i < builtin_case_count; i++) {
        const kv_prefix_case *c = &builtin_cases[i];
        run_case(e, c->name, c->kind, c->suffix, fail_count, invalid_count);
    }
    return *fail_count;
}

/* Run one case per non-blank line of a corpus file.  *fail_count and
 * *invalid_count are updated in place.  Returns 0 on success, -1 if the file
 * cannot be read. */
static int run_corpus(ds4_engine *e, const char *path,
                      int *fail_count, int *invalid_count) {
    FILE *fp = fopen(path, "rb");
    if (!fp) {
        fprintf(stderr, "cannot open corpus '%s': %s\n", path, strerror(errno));
        return -1;
    }
    char line[65536];
    int lineno = 0;
    int cases = 0;
    while (fgets(line, sizeof(line), fp)) {
        lineno++;
        size_t len = strlen(line);
        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r'))
            line[--len] = '\0';
        if (len == 0) continue;
        if (len == sizeof(line) - 1 && line[len - 1] != '\n') {
            fprintf(stderr,
                    "corpus line %d exceeds %zu bytes, skipping\n",
                    lineno, sizeof(line));
            int c;
            while ((c = fgetc(fp)) != EOF && c != '\n') { /* drain */ }
            continue;
        }
        char name[128];
        snprintf(name, sizeof(name), "corpus:%d", lineno);
        run_case(e, name, PREFIX_QWEN, line, fail_count, invalid_count);
        cases++;
    }
    fclose(fp);
    fprintf(stderr, "corpus: ran %d case(s) from '%s'\n", cases, path);
    return 0;
}

int main(int argc, char **argv) {
    const char *model_path = NULL;
    const char *corpus_path = NULL;
    ds4_backend backend = DS4_BACKEND_METAL; /* ds4.h:20 — zero value */

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--corpus") && i + 1 < argc) {
            corpus_path = argv[++i];
        } else if (!strcmp(argv[i], "--cpu")) {
            backend = DS4_BACKEND_CPU;
        } else if (!strcmp(argv[i], "--backend") && i + 1 < argc) {
            const char *b = argv[++i];
            if (!strcmp(b, "cpu"))         backend = DS4_BACKEND_CPU;
            else if (!strcmp(b, "metal"))  backend = DS4_BACKEND_METAL;
            else if (!strcmp(b, "cuda"))   backend = DS4_BACKEND_CUDA;
            else {
                fprintf(stderr, "invalid --backend value: %s\n", b);
                usage(argv[0]);
                return 4;   /* usage error, not a verdict */
            }
        } else if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {
            usage(argv[0]);
            return 0;
        } else if (argv[i][0] != '-' && !model_path) {
            model_path = argv[i];
        } else {
            fprintf(stderr, "unknown argument: %s\n", argv[i]);
            usage(argv[0]);
            return 4;   /* usage error, not a verdict */
        }
    }
    if (!model_path) {
        usage(argv[0]);
        return 4;   /* usage error, not a verdict */
    }

    fprintf(stderr, "kv_prefix_stability: model=%s backend=%s\n",
            model_path, ds4_backend_name(backend));

    /* FULL engine load (see header): inspect_only would leave the tokenizer
     * uninitialised and every comparison would be against an empty vocab. */
    ds4_engine_options opt = {
        .model_path = model_path,
        .backend    = backend,
        .n_threads  = 1,
    };
    ds4_engine *e = NULL;
    if (ds4_engine_open(&e, &opt) != 0) {
        fprintf(stderr, "engine open failed\n");
        return 5;   /* could not load a model: no verdict was produced */
    }
    int v = ds4_engine_vocab_size(e);
    if (v <= 0) {
        fprintf(stderr, "tokenizer not loaded (vocab=%d)\n", v);
        ds4_engine_close(e);
        return 5;   /* could not run: an empty vocab is not a verdict */
    }
    fprintf(stderr, "engine open: vocab=%d model=%s\n",
            v, ds4_engine_model_name(e));

    int fail_count = 0;
    int invalid_count = 0;
    if (corpus_path) {
        if (run_corpus(e, corpus_path, &fail_count, &invalid_count) < 0) {
            ds4_engine_close(e);
            return 5;   /* could not read the corpus: no verdict was produced */
        }
    } else {
        run_builtin_cases(e, &fail_count, &invalid_count);
    }

    /* Verdict precedence: INVALID (an invalid boundary answers no question)
     * beats FAIL, which beats PASS. */
    if (invalid_count > 0) {
        if (fail_count > 0)
            printf("PREFIX_STABILITY: INVALID b=%d n=%d\n",
                   invalid_count, fail_count);
        else
            printf("PREFIX_STABILITY: INVALID b=%d\n", invalid_count);
        ds4_engine_close(e);
        return 3;   /* INVALID -- 2 belongs to the engine */
    }
    if (fail_count == 0) {
        printf("PREFIX_STABILITY: PASS\n");
        ds4_engine_close(e);
        return 0;
    }
    printf("PREFIX_STABILITY: FAIL n=%d\n", fail_count);
    ds4_engine_close(e);
    return 1;
}