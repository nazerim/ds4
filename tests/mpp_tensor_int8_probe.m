/* Feasibility probe v3: int8 in ds4's OWN tensor pattern.
 *
 * ds4_metal.m's capability probe compiles a kernel that takes
 * tensor<device half, dextents<int32_t,2>> buffer arguments, slices them, runs
 * matmul2d<matmul2d_descriptor(16,16,dynamic_extent), execution_simdgroups<4>>,
 * and stores the cooperative tensor - and then builds a real compute pipeline
 * from it. That is the shape the routed-expert MoE kernels use, so it is the bar
 * an INT8 variant has to clear. v2 proved the element types instantiate; this
 * proves the device-tensor + pipeline form does too.
 *
 * build: clang -fobjc-arc -framework Foundation -framework Metal -o /tmp/mpp_probe3 /tmp/mpp_int8_probe3.m
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>

#define HDRS \
    "#include <metal_stdlib>\n" \
    "#include <metal_tensor>\n" \
    "#include <MetalPerformancePrimitives/MetalPerformancePrimitives.h>\n" \
    "using namespace metal;\n" \
    "using namespace mpp::tensor_ops;\n"

/* Mirrors ds4's probe kernel, parameterised on the operand and accumulate types. */
#define KERNEL(NAME, ATEXPR, ACEXPR) \
    "kernel void " NAME "(\n" \
    "        tensor<device " ATEXPR ",  dextents<int32_t, 2>> A [[buffer(0)]],\n" \
    "        tensor<device " ATEXPR ",  dextents<int32_t, 2>> B [[buffer(1)]],\n" \
    "        device " ACEXPR " *C [[buffer(2)]],\n" \
    "        uint2 tgid [[threadgroup_position_in_grid]]) {\n" \
    "    auto tA = A.slice(0, (int)tgid.y);\n" \
    "    auto tB = B.slice((int)tgid.x, 0);\n" \
    "    matmul2d<matmul2d_descriptor(16, 16, dynamic_extent), execution_simdgroups<4>> mm;\n" \
    "    auto cT = mm.get_destination_cooperative_tensor<decltype(tA), decltype(tB), " ACEXPR ">();\n" \
    "    auto sA = tA.slice(0, 0);\n" \
    "    auto sB = tB.slice(0, 0);\n" \
    "    mm.run(sB, sA, cT);\n" \
    "    auto tC = tensor<device " ACEXPR ", dextents<int32_t, 2>, tensor_inline>(C, dextents<int32_t, 2>(16, 16));\n" \
    "    cT.store(tC);\n" \
    "}\n"

static const char *SRC_HALF = HDRS KERNEL("ds4_tensor_probe", "half", "float");
static const char *SRC_INT8 = HDRS KERNEL("ds4_tensor_probe_i8", "signed char", "int");
static const char *SRC_UINT8 = HDRS KERNEL("ds4_tensor_probe_u8", "unsigned char", "int");

static int try_compile(id<MTLDevice> dev, const char *label, const char *kname, const char *src) {
    NSError *err = nil;
    id<MTLLibrary> lib = [dev newLibraryWithSource:[NSString stringWithUTF8String:src]
                                          options:[MTLCompileOptions new] error:&err];
    if (!lib) {
        printf("%-22s COMPILE FAIL\n", label);
        const char *m = err.localizedDescription.UTF8String;
        printf("    %.*s\n", 500, m ? m : "(no message)");
        return 1;
    }
    id<MTLFunction> fn = [lib newFunctionWithName:[NSString stringWithUTF8String:kname]];
    if (!fn) { printf("%-22s ENTRY POINT MISSING (%s)\n", label, kname); return 2; }
    err = nil;
    id<MTLComputePipelineState> pso = [dev newComputePipelineStateWithFunction:fn error:&err];
    if (!pso) {
        printf("%-22s PIPELINE FAIL\n", label);
        const char *m = err.localizedDescription.UTF8String;
        printf("    %.*s\n", 500, m ? m : "(no message)");
        return 3;
    }
    printf("%-22s OK  (compile + entry + pipeline; threads %u)\n",
           label, (unsigned)pso.threadExecutionWidth);
    return 0;
}

int main(void) {
    @autoreleasepool {
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (!dev) { printf("no Metal device\n"); return 3; }
        printf("device: %s\n\n", dev.name.UTF8String);
        int f = 0;
        f += try_compile(dev, "half (control)", "ds4_tensor_probe", SRC_HALF) ? 1 : 0;
        f += try_compile(dev, "int8 x int8 -> i32", "ds4_tensor_probe_i8", SRC_INT8) ? 1 : 0;
        f += try_compile(dev, "uint8 x uint8 -> i32", "ds4_tensor_probe_u8", SRC_UINT8) ? 1 : 0;
        printf("\n%d of 3 failed\n", f);
        return 0;
    }
}
