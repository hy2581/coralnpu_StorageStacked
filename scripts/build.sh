#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/activate.sh"
mkdir -p "$SS_ROOT/build"
exec 9>"$SS_ROOT/build/build.lock"
flock 9
cmake -S "$MEMSIM_HOME" -B "$MEMSIM_BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release "-DCMAKE_CXX_COMPILER=$AXI_CXX"
cmake --build "$MEMSIM_BUILD" -j "$BUILD_JOBS"
# Keep the build lock in this parent; Bazel's persistent server must not inherit it.
bash "$SS_ROOT/scripts/build_device.sh" 9>&-
cmake -S "$SS_ROOT" -B "$SS_ROOT/build/native" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    "-DSTORAGE_STACK_ROOT=$STORAGE_STACK_ROOT" \
    "-DCMAKE_CXX_COMPILER=$AXI_CXX" "-DCMAKE_C_COMPILER=$AXI_CC" "-DCMAKE_ASM_COMPILER=$AXI_CC"
cmake --build "$SS_ROOT/build/native" -j "$BUILD_JOBS"

"$AXI_PYTHON" "$SS_ROOT/scripts/storage_dependency.py" --record-build
