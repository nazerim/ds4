/* Measure the int8:fp16 cooperative-tensor matmul ratio on this machine.
 *
 * The entire INT8 MoE case in PLAN-INT8-MOE.md rests on a 3x throughput ratio
 * borrowed from oMLX's measurement of ITS kernel (42 TOP/s int8 against a 44.21
 * ceiling, versus ds4's ~13.5-14 TF/s fp16 NAX plateau). Two-term int8 costs two
 * int32 matmuls per K step where fp16 costs one, so the design only wins if the
 * integer path is meaningfully more than 2x. This measures it directly, in the
 * same register-fragment form MLX's steel/gemm/nax.h uses, so no ds4 plumbing is
 * involved and nothing in the engine is touched.
 *
 * Method: identical kernels differing only in operand and accumulator types, same
 * descriptor (16x32x16), same fragment fill, same iteration count, same grid.
 * Each op.run accumulates into the destination cooperative tensor, so the loop
 * cannot be optimized away and the work is genuinely data-dependent.
 *
 * build: clang -fobjc-arc -framework Foundation -framework Metal -o /tmp/mpp_bench /tmp/mpp_int8_bench.m
 * run:   /tmp/mpp_bench [iters]
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdlib.h>

#define HDRS \
    "#include <metal_stdlib>\n" \
    "#include <MetalPerformancePrimitives/MetalPerformancePrimitives.h>\n" \
    "using namespace metal;\n"

/* A is 16x16 (8 per lane), B is 16x32 (16 per lane), C is 16x32 (16 per lane)
 * for the 16x32x16 descriptor with a 32-lane simdgroup. */
#define BENCH(NAME, AT, BT, CT, ZERO) \
    "kernel void " NAME "(device const " AT " *A, device const " BT " *B, device " CT " *C,\n" \
    "                     constant unsigned int &iters,\n" \
    "                     ushort tid [[thread_index_in_threadgroup]],\n" \
    "                     uint2 tgpig [[threadgroup_position_in_grid]]) {\n" \
    "  constexpr auto d = mpp::tensor_ops::matmul2d_descriptor(\n" \
    "      16, 32, 16, false, true, true,\n" \
    "      mpp::tensor_ops::matmul2d_descriptor::mode::multiply_accumulate);\n" \
    "  mpp::tensor_ops::matmul2d<d, metal::execution_simdgroup> op;\n" \
    "  auto ca = op.get_left_input_cooperative_tensor<" AT ", " BT ", " CT ">();\n" \
    "  auto cb = op.get_right_input_cooperative_tensor<" AT ", " BT ", " CT ">();\n" \
    "  auto cc = op.get_destination_cooperative_tensor<\n" \
    "      metal::remove_addrspace_t<decltype(ca)>,\n" \
    "      metal::remove_addrspace_t<decltype(cb)>, " CT ">();\n" \
    "  const unsigned int o = (tgpig.y * 64u + tid) * 16u;\n" \
    "  for (short i = 0; i < 8; i++) ca[i] = (" AT ")1;\n" \
    "  for (short i = 0; i < 16; i++) { cb[i] = (" BT ")1; cc[i] = " ZERO "; }\n" \
    "  for (unsigned int it = 0u; it < iters; it++) op.run(ca, cb, cc);\n" \
    "  for (short i = 0; i < 16; i++) C[o + i] = cc[i];\n" \
    "}\n"

static const char *SRC = HDRS
    BENCH("bench_half", "half", "half", "float", "0.0h")
    BENCH("bench_i8", "signed char", "signed char", "int", "0");

static double run_one(id<MTLDevice> dev, id<MTLCommandQueue> q, const char *kname,
                      unsigned int iters, unsigned int grid_y, id<MTLBuffer> out) {
    id<MTLLibrary> lib = nil;
    NSError *err = nil;
    /* The whole source defines all three kernels; pick one entry point. */
    static id<MTLLibrary> cached = nil;
    if (!cached) {
        cached = [dev newLibraryWithSource:[NSString stringWithUTF8String:SRC]
                                   options:[MTLCompileOptions new] error:&err];
        if (!cached) {
            printf("compile failed: %s\n", err.localizedDescription.UTF8String);
            return -1.0;
        }
    }
    lib = cached;
    id<MTLFunction> fn = [lib newFunctionWithName:[NSString stringWithUTF8String:kname]];
    if (!fn) { printf("%s: entry point missing\n", kname); return -1.0; }
    id<MTLComputePipelineState> pso = [dev newComputePipelineStateWithFunction:fn error:&err];
    if (!pso) { printf("%s: pipeline failed: %s\n", kname, err.localizedDescription.UTF8String); return -1.0; }

    id<MTLBuffer> ibuf = [dev newBufferWithBytes:&iters length:4
                                        options:MTLResourceStorageModeShared];
    id<MTLCommandBuffer> cmd = [q commandBuffer];
    id<MTLComputeCommandEncoder> enc = [cmd computeCommandEncoder];
    [enc setComputePipelineState:pso];
    [enc setBuffer:out offset:0 atIndex:0];
    [enc setBuffer:out offset:0 atIndex:1];
    [enc setBuffer:out offset:0 atIndex:2];
    [enc setBuffer:ibuf offset:0 atIndex:3];
    [enc dispatchThreadgroups:MTLSizeMake(1, grid_y, 1)
        threadsPerThreadgroup:MTLSizeMake(32, 1, 1)];
    [enc endEncoding];
    [cmd commit];
    [cmd waitUntilCompleted];
    if (cmd.status != MTLCommandBufferStatusCompleted) {
        printf("%s: command buffer status %lu\n", kname, (unsigned long)cmd.status);
        return -1.0;
    }
    return cmd.GPUStartTime > 0 ? (cmd.GPUEndTime - cmd.GPUStartTime) : 0.0;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        const unsigned int iters = argc > 1 ? (unsigned int)atoi(argv[1]) : 20000u;
        const unsigned int grid_y = 512u;              /* 512 * 32 lanes */
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (!dev) { printf("no Metal device\n"); return 3; }
        id<MTLCommandQueue> q = [dev newCommandQueue];
        /* generous output buffer; the kernels only touch a small prefix */
        id<MTLBuffer> out = [dev newBufferWithLength:1u << 22 options:MTLResourceStorageModeShared];
        printf("device: %s\niters=%u grid=(1,%u,1) x 32 lanes, tile 16x32x16\n\n",
               dev.name.UTF8String, iters, grid_y);

        const double macs = 16.0 * 32.0 * 16.0 * (double)iters * (double)grid_y;
        const char *names[2] = {"bench_half", "bench_i8"};
        const char *labels[2] = {"half x half -> f32 ", "int8 x int8 -> i32 "};
        const int REPS = 7;
        double samples[2][REPS];
        /* Warm the clocks up first: an unwarmed run measured 2.7 ms where the
         * steady state is ~1.0, so cold samples are worthless. */
        for (int w = 0; w < 3; w++) {
            run_one(dev, q, names[0], iters / 4, grid_y, out);
            run_one(dev, q, names[1], iters / 4, grid_y, out);
        }
        /* Interleave the two kernels within every rep so neither one gets a
         * systematically cooler or hotter clock state. */
        for (int rep = 0; rep < REPS; rep++)
            for (int k = 0; k < 2; k++)
                samples[k][rep] = run_one(dev, q, names[k], iters, grid_y, out);
        double secs[2];
        for (int k = 0; k < 2; k++) {
            double v[REPS];
            for (int i = 0; i < REPS; i++) v[i] = samples[k][i];
            for (int i = 1; i < REPS; i++)
                for (int j = i; j > 0 && v[j] < v[j - 1]; j--) {
                    double t = v[j]; v[j] = v[j - 1]; v[j - 1] = t;
                }
            secs[k] = v[REPS / 2];   /* median */
            printf("%s  median %8.3f ms  (min %7.3f max %7.3f)  %7.2f TOP/s\n",
                   labels[k], secs[k] * 1e3, v[0] * 1e3, v[REPS - 1] * 1e3,
                   macs * 2.0 / secs[k] / 1e12);
        }
        if (secs[0] > 0 && secs[1] > 0) {
            printf("\nratio int8/half = %.2fx   (two-term int8 needs 2 MMAs, so it wins only above 2.00x)\n",
                   secs[0] / secs[1]);
            printf("two-term net on the MoE bucket: %+.1f%% vs fp16\n",
                   100.0 * (secs[0] / (2.0 * secs[1]) - 1.0));
        }
        return 0;
    }
}
