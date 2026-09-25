#!/usr/bin/env bash
set -euo pipefail
self=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
: "${CORALNPU_HOME:?activate the project environment first}"
mkdir -p "$CORALNPU_HOME/native"
install -m 0644 "$self"/coralnpu_native.{h,cc,map} "$CORALNPU_HOME/native/"
install -m 0644 "$self/BUILD.bazel" "$CORALNPU_HOME/native/BUILD"
# The checked-in RTL wrapper and native Verilator build rules already contain
# the project adaptations. No runtime patching or external project is required.
