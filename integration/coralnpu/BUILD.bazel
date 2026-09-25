load("//rules:coralnpu_v2.bzl", "coralnpu_v2_binary")
package(default_visibility = ["//visibility:public"])
coralnpu_v2_binary(name="ddr_touch", srcs=["ddr_touch.cc", "benchmark_config.h"])
coralnpu_v2_binary(name="smoke", srcs=["smoke.cc", "smoke_config.h"])
coralnpu_v2_binary(
    name="tiny_llm", srcs=["tiny_llm.cc", "llm_config.h"], stack_size_bytes=4096,
    copts=["-Os", "-fno-tree-vectorize", "-fno-tree-slp-vectorize", "-ffp-contract=off", "-march=rv32imf_zicsr_zifencei"],
)
cc_library(
    name="coralnpu_native", srcs=["coralnpu_native.cc"], hdrs=["coralnpu_native.h"],
    includes=["."], linkstatic=True, alwayslink=True,
    deps=["//hw_sim:core_mini_axi_wrapper", "//tests/verilator_sim:elf"],
)
cc_binary(
    name="libcoralnpu-native.so", deps=[":coralnpu_native"], linkshared=True,
    additional_linker_inputs=["coralnpu_native.map"],
    linkopts=["-Wl,--version-script=$(location coralnpu_native.map)",
              "-Wl,-Bsymbolic", "-latomic", "-Wl,-z,defs"],
)
