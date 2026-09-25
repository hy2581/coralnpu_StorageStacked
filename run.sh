#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command=${1:-run}
[[ $# == 0 ]] || shift
case "$command" in
    smoke)
        source "$root/scripts/activate.sh"
        exec "$AXI_PYTHON" "$root/scripts/run.py" --benchmark config/benchmarks/smoke.json "$@" ;;
    llm)
        source "$root/scripts/activate.sh"
        exec "$AXI_PYTHON" "$root/scripts/run.py" --benchmark config/benchmarks/tiny_llm.json "$@" ;;
    setup|build) exec bash "$root/scripts/$command.sh" "$@" ;;
    run|test|check|validate)
        source "$root/scripts/activate.sh"
        exec "$AXI_PYTHON" "$root/scripts/$command.py" "$@" ;;
    *) echo "用法: ./run.sh {setup|build|run|smoke|llm|test|check|validate} [参数]" >&2; exit 2 ;;
esac
