package(default_visibility = ["//visibility:public"])
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
