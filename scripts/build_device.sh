#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/activate.sh"
bash "$HET_PROJECT_ROOT/coralnpu/install.sh"
"$AXI_PYTHON" "$SS_ROOT/scripts/configure.py" --generate-kernel "${1:-config/benchmark.json}"
mode=${2:-full}
if [[ $mode != full && $mode != --kernel-only ]]; then
    echo "Unknown build mode: $mode" >&2
    exit 2
fi
if [[ $mode == --kernel-only && ! -f "$DEVICE_BUILD/libcoralnpu-native.so" ]]; then
    echo 'RTL library is missing; run ./run.sh build first' >&2
    exit 1
fi
cd "$CORALNPU_HOME"
args=(--workspace_status_command="$SS_ROOT/scripts/workspace_status.sh" --define=storagestacked_native_cpp=1 --jobs="$BUILD_JOBS"
    --repo_env="CC=$AXI_CC" --repo_env="CXX=$AXI_CXX"
    --action_env="CC=$AXI_CC" --action_env="CXX=$AXI_CXX"
    --repo_env="CPLUS_INCLUDE_PATH=$SS_DEPS_ROOT/xpu-native/include"
    --action_env="CPLUS_INCLUDE_PATH=$SS_DEPS_ROOT/xpu-native/include"
    --host_action_env="CPLUS_INCLUDE_PATH=$SS_DEPS_ROOT/xpu-native/include"
    --linkopt="-L$SS_DEPS_ROOT/xpu-native/lib" --host_linkopt="-L$SS_DEPS_ROOT/xpu-native/lib")
benchmark_name=$("$AXI_PYTHON" "$SS_ROOT/scripts/configure.py" --benchmark-name "${1:-config/benchmark.json}")
kernel_target=$benchmark_name
[[ $benchmark_name != memory_roundtrip ]] || kernel_target=ddr_touch
targets=("//native:$kernel_target.elf")
if [[ $mode == full ]]; then
    targets+=(//native:libcoralnpu-native.so)
fi
"$SS_DEPS_ROOT/xpu-tools/bin/bazel" --output_user_root="$SS_DEPS_ROOT/bazel" build "${args[@]}" "${targets[@]}"
mkdir -p "$DEVICE_BUILD"
if [[ $mode == full ]]; then
    cp -fL bazel-bin/native/libcoralnpu-native.so "$DEVICE_BUILD/libcoralnpu-native.so"
fi
"$SS_DEPS_ROOT/xpu-tools/bin/bazel" --output_user_root="$SS_DEPS_ROOT/bazel" cquery "${args[@]}" --output=files "//native:$kernel_target.elf" > "$DEVICE_BUILD/kernel-path.txt"
while IFS= read -r file; do
    if [[ $file == */$kernel_target.elf ]]; then
        cp -f "$file" "$DEVICE_BUILD/$benchmark_name.elf"
    fi
done < "$DEVICE_BUILD/kernel-path.txt"
cd "$SS_ROOT"
"$AXI_PYTHON" "$SS_ROOT/scripts/configure.py" --record-build "${1:-config/benchmark.json}"
