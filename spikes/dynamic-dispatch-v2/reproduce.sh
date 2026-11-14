#!/usr/bin/env bash
# Independent v2 reproduction; historical v1 script remains unchanged.
set -euo pipefail
cd "$(dirname "$0")/../.."
MODE="${1:-help}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/crucible-uv-cache}"
export PATH="$PWD/engine/.venv/bin:$PATH"
TASK="${DISPATCH_TASK:-dispatch@2}"
TASK_KEY="${TASK/@/-}"
REGIME="${DISPATCH_REGIME:-D}"
CANDIDATE="${DISPATCH_CANDIDATE:-candidate-d-v1}"
EXPORT_DIR="${DISPATCH_EXPORT_DIR:-harbor/${TASK_KEY}-${CANDIDATE}}"
JOB_PREFIX="${DISPATCH_JOB_PREFIX:-${TASK_KEY}-${CANDIDATE}}"
SEEDS="${DISPATCH_HARBOR_SEEDS:-6000-6002}"
REPORT_DIR="spikes/dynamic-dispatch-v2/experiments/${CANDIDATE}"
RUNTIME_IMAGE="${DISPATCH_RUNTIME_IMAGE:-crucible-dispatch-v2-runtime:candidate-d-v1}"

setup() { uv sync --directory engine --locked; }
evaluate() {
  if [[ "$TASK" == "dispatch@2" ]]; then
    python spikes/dynamic-dispatch-v2/run_linux.py --image "$RUNTIME_IMAGE" evaluate \
      --task "$TASK" --regime "$REGIME" --candidate "$CANDIDATE" --split "$1"
  else
    python spikes/dynamic-dispatch-v2/evaluate.py --task "$TASK" --regime "$REGIME" \
      --candidate "$CANDIDATE" --split "$1"
  fi
}
export_tasks() {
  python -c 'import sys; from crucible.envs.dispatch.v2.task import DEFAULT_REGIME; assert sys.argv[1] != "dispatch@2" or sys.argv[2] == DEFAULT_REGIME, "Export uses frozen DEFAULT_REGIME; requested regime differs"' "$TASK" "$REGIME"
  if [[ "$TASK" == "dispatch@2" ]]; then
    python spikes/dynamic-dispatch-v2/export_v2.py --seeds "$SEEDS" --out "$EXPORT_DIR"
  else
    crucible export harbor --task "$TASK" --seeds "$SEEDS" --out "$EXPORT_DIR"
  fi
}
runtime() {
  if [[ "$TASK" != "dispatch@2" ]]; then
    echo "The pinned runtime build is specific to dispatch@2." >&2
    return 2
  fi
  local first_task
  first_task="$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["tasks"][0])' "$EXPORT_DIR/manifest.json")"
  docker build --platform linux/arm64 -f spikes/dynamic-dispatch-v2/runtime.Dockerfile \
    -t "$RUNTIME_IMAGE" "$EXPORT_DIR/$first_task/environment/mcp-server"
}
harbor_checks() {
  harbor run -p "$EXPORT_DIR" -a oracle -o jobs --job-name "${JOB_PREFIX}-oracle"
  harbor run -p "$EXPORT_DIR" -a nop -o jobs --job-name "${JOB_PREFIX}-nop"
}
frontier() {
  python spikes/dynamic-dispatch-v2/frontier.py --export "$EXPORT_DIR" \
    --job-prefix "$JOB_PREFIX" --repeats "${DISPATCH_MODEL_REPEATS:-3}"
}
collect() {
  python spikes/dynamic-dispatch-v2/collect_harbor.py --job-prefix "$JOB_PREFIX" \
    --export "$EXPORT_DIR" --out "${REPORT_DIR}/harbor" \
    --expected-repeats "${DISPATCH_MODEL_REPEATS:-3}"
}
replay() {
  if [[ "$TASK" == "dispatch@2" ]]; then
    python spikes/dynamic-dispatch-v2/run_linux.py --image "$RUNTIME_IMAGE" replay \
      --job-prefix "$JOB_PREFIX" --export "$EXPORT_DIR" --out "${REPORT_DIR}/harbor-replay.json"
  else
    python spikes/dynamic-dispatch-v2/replay_harbor.py --job-prefix "$JOB_PREFIX" \
      --export "$EXPORT_DIR" --out "${REPORT_DIR}/harbor-replay.json"
  fi
}
case "$MODE" in
  setup) setup ;;
  development|calibration|heldout|salted) setup; evaluate "$MODE" ;;
  export) setup; export_tasks ;;
  runtime) runtime ;;
  harbor) harbor_checks ;;
  frontier|pilot) frontier ;;
  collect) collect ;;
  replay) replay ;;
  local) setup; evaluate heldout; evaluate salted ;;
  all)
    setup; export_tasks
    if [[ "$TASK" == "dispatch@2" ]]; then runtime; fi
    evaluate heldout; evaluate salted; harbor_checks
    ;;
  *) echo "Usage: $0 [setup|development|calibration|heldout|salted|export|runtime|harbor|frontier|pilot|collect|replay|local|all]" ;;
esac
