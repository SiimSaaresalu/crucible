#!/usr/bin/env bash
# Core experiment reproduction. Run from any working directory.
set -euo pipefail

cd "$(dirname "$0")/../.."
MODE="${1:-all}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/crucible-uv-cache}"
export PATH="$PWD/engine/.venv/bin:$PATH"
EXPORT_DIR="${DISPATCH_EXPORT_DIR:-harbor/dispatch-1}"
JOB_PREFIX="${DISPATCH_JOB_PREFIX:-dispatch-1}"

setup() {
  uv sync --directory engine --locked
}

local_evaluation() {
  python spikes/dynamic-dispatch/evaluate.py --split heldout --overwrite
  python spikes/dynamic-dispatch/evaluate.py --split salted --overwrite
}

export_tasks() {
  crucible export harbor --task dispatch@1 --seeds 1000-1002 --out "$EXPORT_DIR"
}

harbor_checks() {
  harbor run -p "$EXPORT_DIR" -a oracle -o jobs --job-name "${JOB_PREFIX}-oracle"
  harbor run -p "$EXPORT_DIR" -a nop -o jobs --job-name "${JOB_PREFIX}-nop"
}

frontier() {
  : "${GOOGLE_GENERATIVE_AI_API_KEY:?Set GOOGLE_GENERATIVE_AI_API_KEY before frontier runs}"
  harbor run -p "$EXPORT_DIR" -a opencode -m google/gemini-3.8-flash \
    --ak version=1.19.5 --ak disable_web_search=true \
    --api-key-name GOOGLE_GENERATIVE_AI_API_KEY \
    --ae "GOOGLE_GENERATIVE_AI_API_KEY=$GOOGLE_GENERATIVE_AI_API_KEY" \
    -o jobs --job-name "${JOB_PREFIX}-opencode-gemini" --n-concurrent 3
}

case "$MODE" in
  all) setup; local_evaluation; export_tasks; harbor_checks ;;
  local) setup; local_evaluation ;;
  export) setup; export_tasks ;;
  harbor) harbor_checks ;;
  frontier) frontier ;;
  *) echo "Usage: $0 [all|local|export|harbor|frontier]" >&2; exit 2 ;;
esac
