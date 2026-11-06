/* Exercise the C entry points without an Objective-C caller's autorelease pool. */
#include "ds4_gpu.h"
#include <mach/mach.h>
#include <malloc/malloc.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#define CHECK(x) do { if (!(x)) { \
    fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #x); goto done; \
} } while (0)

static int exchange(void *ud, uint32_t layer, uint32_t gate, uint64_t seq) {
    (void)layer; (void)gate;
    return seq == __atomic_add_fetch((uint64_t *)ud, 1, __ATOMIC_RELAXED);
}

static int batch_exchange(void *ud, uint32_t layer, uint32_t rows, uint64_t seq) {
    return rows == 1 && exchange(ud, layer, 1, seq);
}

static int big_exchange(void *ud, uint32_t layer, uint64_t seq,
                         const void *out, void *in, uint64_t bytes) {
    if (bytes != 1024 || !exchange(ud, layer, 1, seq)) return 0;
    memcpy(in, out, (size_t)bytes);
    return 1;
}

/* C callers have no outer autorelease pool. Releasing a CPU-touched tensor
 * must release its Metal allocation too, rather than retaining it until the
 * thread exits. Heap statistics alone miss these large shared buffers. */
static int check_tensor_memory(void) {
    ds4_gpu_tensor *tensor = NULL;
    const uint64_t bytes = 16u * 1024u * 1024u;
    uint64_t baseline = 0, footprint = 0;
    int rc = 1;
    CHECK(ds4_gpu_init());
    for (int i = 0; i < 40; i++) {
        tensor = ds4_gpu_tensor_alloc(bytes);
        CHECK(tensor);
        unsigned char *data = ds4_gpu_tensor_contents(tensor);
        CHECK(data);
        memset(data, i, (size_t)bytes);
        const uint32_t value = (uint32_t)i + 1234u;
        uint32_t readback = 0;
        CHECK(ds4_gpu_tensor_write(tensor, bytes - sizeof(value), &value, sizeof(value)));
        CHECK(ds4_gpu_tensor_read(tensor, bytes - sizeof(value), &readback, sizeof(readback)));
        CHECK(readback == value && data[0] == (unsigned char)i);
        ds4_gpu_tensor_free(tensor);
        tensor = NULL;
        if (i == 7 || i == 39) {
            task_vm_info_data_t info = {0};
            mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
            CHECK(task_info(mach_task_self(), TASK_VM_INFO,
                            (task_info_t)&info, &count) == KERN_SUCCESS);
            footprint = info.phys_footprint;
            if (i == 7) baseline = footprint;
        }
    }
    fprintf(stderr, "Metal CPU-touched buffers: footprint %.3f -> %.3f MiB\n",
            baseline / 1048576.0, footprint / 1048576.0);
    /* A leaked buffer per iteration adds 512 MiB; allow bounded driver
     * bookkeeping without requiring exact allocator reuse. */
    CHECK(footprint <= baseline + 32u * 1024u * 1024u);
    puts("Metal CPU-touched buffer lifetime: PASS");
    rc = 0;
done:
    ds4_gpu_tensor_free(tensor);
    ds4_gpu_cleanup();
    return rc;
}

static int check_tp_memory(int mode) {
    ds4_gpu_tensor *a = NULL, *slab = NULL, *out = NULL, *in = NULL;
    uint64_t exchanges = 0;
    int rc = 1;
    CHECK(ds4_gpu_init());
    a = ds4_gpu_tensor_alloc(1024);
    in = ds4_gpu_tensor_alloc(1024);
    slab = ds4_gpu_tensor_alloc(8192);
    CHECK(a && in && slab);
    CHECK(ds4_gpu_tensor_fill_f32(slab, 0, 2048));
    out = ds4_gpu_tensor_view(slab, 4096, 1024);
    CHECK(out);
    CHECK(ds4_gpu_tp_init(0, slab, 0, 4096, 1024, exchange, &exchanges));
    if (mode == 1) ds4_gpu_tp_set_session_batch_mode(1);
    ds4_gpu_tp_set_batch_exchange(batch_exchange);
    ds4_gpu_tp_set_big_exchange(big_exchange);
    float input[256], output[256];
    for (int j = 0; j < 256; j++) input[j] = j / 4.0f;
    CHECK(ds4_gpu_tensor_write(a, 0, input, sizeof(input)));
    size_t baseline = 0;
    for (int i = 0; i < 8192; i++) {
        CHECK(ds4_gpu_begin_commands());
        CHECK(ds4_gpu_add_tensor(out, a, a, 256));
        if (mode == 3) CHECK(ds4_gpu_tp_big_gate_encode(0, 1, out, in, 1024));
        else if (mode == 2) CHECK(ds4_gpu_tp_batch_gate_encode(0, 1));
        else CHECK(ds4_gpu_tp_gate_encode(0, 0));
        CHECK(ds4_gpu_end_commands());
        CHECK(!ds4_gpu_tp_failed());
        CHECK(ds4_gpu_tensor_read(mode == 3 ? in : out, 0, output, sizeof(output)));
        for (int j = 0; j < 256; j++) CHECK(output[j] == 2 * input[j]);
        if (i % 1024 == 1023) {
            malloc_statistics_t stats;
            malloc_zone_statistics(NULL, &stats);
            if (!baseline) baseline = stats.size_in_use;
            fprintf(stderr, "Metal TP mode %d: %d gates, heap %.3f MiB\n",
                    mode, i + 1, stats.size_in_use / 1048576.0);
            CHECK(stats.size_in_use <= baseline + 8u * 1024u * 1024u);
        }
    }
    CHECK(__atomic_load_n(&exchanges, __ATOMIC_RELAXED) == 8192);
    puts("Metal TP payloads, ordered loopback exchanges and bounded command heap: PASS");
    rc = 0;
done:
    if (ds4_gpu_commands_active()) ds4_gpu_end_commands();
    ds4_gpu_tp_shutdown();
    ds4_gpu_tensor_free(out);
    ds4_gpu_tensor_free(slab);
    ds4_gpu_tensor_free(in);
    ds4_gpu_tensor_free(a);
    ds4_gpu_cleanup();
    return rc;
}

/* Cross-thread use of the batched command buffer.
 *
 * Reproduction for the 00:23:14 ds4-server abort (see
 * .codebase-memory/PLAN-VISION-BUFFER-RACE notes): the Metal layer caches ONE
 * open encoder per batch section (g_batch_enc, ds4_metal.m:1296-1320) and hands
 * the batch command buffer to any caller while a section is open
 * (ds4_gpu_command_buffer(), 1285).  Nothing inside ds4_metal.m serializes
 * encoding - g_tensor_mu only guards tensor bookkeeping - so the design assumes
 * exactly one thread is in GPU code at a time.  The server does not enforce
 * that: generation holds model_mu (ds4_server.c:17412) while multimodal request
 * parsing encodes images under inference_mu (4002, inside
 * request_tokenize_multimodal_prompt).  Two locks, one command buffer.
 *
 * The crash is the loud outcome; a second encoder coalescing onto a buffer that
 * still has one open.  The quiet outcome is the other thread joining the live
 * g_batch_enc and dispatching into someone else's batch.
 *
 * Thread A mirrors the decode path (spec_frontier_copy -> ds4_gpu_tensor_copy,
 * and add_tensor leaving the batch encoder open across the section).  Thread B
 * mirrors the vision encode (compute dispatch mid-section).  Run as a mode, not
 * in the default suite: `./tests/test_metal_command_memory race`.  Returns 0
 * only if the child survives; today it aborts, and that is reported as a defect
 * rather than hidden.
 */
static pthread_t th_a, th_b;
static ds4_gpu_tensor *race_a, *race_b;
static volatile int race_iters = 3000;
static volatile int race_stop = 0;

static void *race_thread_decode(void *arg) {
    (void)arg;
    float in[256];
    for (int i = 0; i < 256; i++) in[i] = (float)i;
    for (int i = 0; i < race_iters && !race_stop; i++) {
        if (!ds4_gpu_begin_commands()) { usleep(5); continue; }
        (void)ds4_gpu_tensor_write(race_a, 0, in, sizeof(in));
        (void)ds4_gpu_add_tensor(race_b, race_a, race_a, 256);   /* opens g_batch_enc */
        (void)ds4_gpu_tensor_copy(race_b, 0, race_a, 0, sizeof(in));
        /* No sleep here on purpose: the section is left open while thread B
         * works, exactly as a long decode batch does. */
        (void)ds4_gpu_end_commands();
    }
    return NULL;
}

static void *race_thread_vision(void *arg) {
    (void)arg;
    float in[256];
    for (int i = 0; i < 256; i++) in[i] = (float)(2 * i);
    for (int i = 0; i < race_iters && !race_stop; i++) {
        /* The vision encode does not open a batch section: it just does GPU work,
         * and ds4_gpu_command_buffer() hands it whoever else's buffer is live. */
        (void)ds4_gpu_tensor_write(race_a, 512, in, sizeof(in));
        (void)ds4_gpu_add_tensor(race_b, race_a, race_a, 256);
        (void)ds4_gpu_tensor_copy(race_b, 1024, race_a, 512, sizeof(in));
        usleep(2);
    }
    return NULL;
}

static int race_child_body(void) {
    if (!ds4_gpu_init()) return 3;
    race_a = ds4_gpu_tensor_alloc(4096);
    race_b = ds4_gpu_tensor_alloc(4096);
    if (!race_a || !race_b) return 3;
    if (pthread_create(&th_a, NULL, race_thread_decode, NULL) != 0) return 3;
    if (pthread_create(&th_b, NULL, race_thread_vision, NULL) != 0) return 3;
    pthread_join(th_a, NULL);
    race_stop = 1;
    pthread_join(th_b, NULL);
    if (ds4_gpu_commands_active()) ds4_gpu_end_commands();
    fprintf(stderr, "race child: completed %d iterations per thread without abort\n",
            race_iters);
    ds4_gpu_cleanup();
    return 0;
}

static int check_batch_cross_thread(const char *self) {
    /* A forked-but-not-exec'd child cannot reach MTLCompilerService (observed:
     * "Unable to reach MTLCompilerService ... Connection init failed"), so the
     * reproduction runs as a real child process.  Its death is the finding: a
     * driver assertion is a SIGABRT, so it cannot be reported from inside. */
    fflush(stdout);
    fflush(stderr);
    pid_t child = fork();
    if (child < 0) { perror("fork"); return 2; }
    if (child == 0) {
        setenv("DS4_RACE_CHILD", "1", 1);
        char *av[] = {(char *)self, (char *)"race", NULL};
        execvp(self, av);
        perror("execvp");
        _exit(2);
    }
    int status = 0;
    if (waitpid(child, &status, 0) < 0) { perror("waitpid"); return 2; }
    if (WIFSIGNALED(status)) {
        fprintf(stderr,
            "DEFECT REPRODUCED: child killed by signal %d (%s) - cross-thread use"
            " of the batched command buffer is fatal.\n",
            WTERMSIG(status), strsignal(WTERMSIG(status)));
        return 1;
    }
    if (WEXITSTATUS(status) != 0) {
        fprintf(stderr, "race child exited %d (setup failure, not a verdict)\n",
                WEXITSTATUS(status));
        return 2;
    }
    puts("two threads, one batched command buffer: child survived");
    return 0;
}

int main(int argc, char **argv) {
    if (argc == 2) {
        if (!strcmp(argv[1], "buffers")) return check_tensor_memory();
        const char *modes[] = {"row", "session", "batch", "big"};
        for (int i = 0; i < 4; i++)
            if (!strcmp(argv[1], modes[i])) return check_tp_memory(i);
        if (!strcmp(argv[1], "race")) {
            if (getenv("DS4_RACE_CHILD")) return race_child_body();
            return check_batch_cross_thread(argv[0]);
        }
    }
    if (argc != 1) {
        fprintf(stderr, "usage: %s [buffers|row|session|batch|big|race]\n", argv[0]);
        return 2;
    }
    ds4_gpu_tensor *a = NULL, *b = NULL;
    int rc = 1;
    CHECK(ds4_gpu_init());
    a = ds4_gpu_tensor_alloc(1024);
    b = ds4_gpu_tensor_alloc(1024);
    CHECK(a && b);
    float input[256], output[256];
    for (int i = 0; i < 256; i++) input[i] = (float)i / 4.0f;
    CHECK(ds4_gpu_tensor_write(a, 0, input, sizeof(input)));
    size_t baseline = 0;
    for (int i = 0; i < 8192; i++) {
        CHECK(ds4_gpu_begin_commands());
        for (int j = 0; j < 8; j++) {
            CHECK(ds4_gpu_add_tensor(b, a, a, 256));
            CHECK(ds4_gpu_tensor_copy(b, 0, a, 0, sizeof(input)));
            if (j == 3 && i % 2) CHECK(ds4_gpu_flush_commands());
        }
        CHECK(ds4_gpu_add_tensor(b, a, a, 256));
        CHECK(ds4_gpu_end_commands());
        CHECK(ds4_gpu_tensor_read(b, 0, output, sizeof(output)));
        for (int j = 0; j < 256; j++) CHECK(output[j] == 2.0f * input[j]);
        if (i % 1024 == 1023) {
            malloc_statistics_t stats;
            malloc_zone_statistics(NULL, &stats);
            if (!baseline) baseline = stats.size_in_use;
            fprintf(stderr, "Metal copies: %d batches, heap %.3f MiB\n",
                    i + 1, stats.size_in_use / 1048576.0);
            /* Leave room for driver caches, but not per-copy retained objects.
             * Before the fix this grows by about 35 MiB after warmup. */
            CHECK(stats.size_in_use <= baseline + 8u * 1024u * 1024u);
        }
    }
    puts("Metal copy contents, asynchronous flush and bounded command heap: PASS");
    rc = 0;
done:
    if (ds4_gpu_commands_active()) ds4_gpu_end_commands();
    ds4_gpu_tensor_free(a);
    ds4_gpu_tensor_free(b);
    ds4_gpu_cleanup();
    return rc;
}
