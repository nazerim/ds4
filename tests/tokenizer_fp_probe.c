/*
 * tokenizer_fp_probe.c — print the behavioral tokenizer fingerprint of a
 * GGUF (and vocab stats) from a FULL engine load. Two runs on the same
 * gguf+binary MUST print the same fp (cross-process stability regression,
 * the class of bug that the first pointer-hash version shipped with); two
 * different ggufs MUST print different fps.
 *
 * FULL engine load (minutes, tens of GB - inspect_only early-returns before
 * the tokenizer is initialized, and a fingerprint of an empty vocab is a
 * lie). Stop the server first (the instance lock will refuse anyway;
 * DS4_LOCK_FILE=/tmp/... only for deliberate bypasses).
 *
 * build (fork tree):
 *   cc -O2 -std=c11 -o /tmp/tokfp_probe tests/tokenizer_fp_probe.c \
 *      ds4.o ds4_help.o ds4_kvstore.o rax.o ds4_gpu_args.o ds4_image.o \
 *      ds4_distributed.o ds4_tp.o ds4_ssd.o ds4_metal.o ds4_layer_pack.o \
 *      -lm -pthread -framework Foundation -framework Metal
 * run:
 *   ./tokenizer_fp_probe <model.gguf>
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../ds4.h"

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <model.gguf>\n", argv[0]); return 1; }
    ds4_engine_options opt = {
        .model_path = argv[1],
        .n_threads = 1,
    };
    ds4_engine *e = NULL;
    if (ds4_engine_open(&e, &opt) != 0) {
        fprintf(stderr, "engine open failed\n");
        return 2;
    }
    int v = ds4_engine_vocab_size(e);
    if (v <= 0) {
        fprintf(stderr, "tokenizer not loaded (vocab=%d)\n", v);
        ds4_engine_close(e);
        return 3;
    }
    printf("vocab=%d fp=%016llx\n", v,
           (unsigned long long)ds4_engine_tokenizer_fingerprint(e));
    ds4_engine_close(e);
    return 0;
}
