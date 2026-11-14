#!/bin/bash
# Oracle: play a scripted Night Shift policy against the sidecar over MCP.
# Harbor uploads solution/ only for the oracle agent, so agent runs never see the engine.
set -euo pipefail

SOLUTION_DIR="$(cd "$(dirname "$0")" && pwd)"
POLICY="${NIGHTSHIFT_POLICY:-reference}"

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/@@UV_VERSION@@/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

uv venv /tmp/nightshift-solution --python 3.14 --quiet
uv pip install --python /tmp/nightshift-solution --quiet \
  -r "$SOLUTION_DIR/requirements.txt" "$SOLUTION_DIR/@@WHEEL_NAME@@"
/tmp/nightshift-solution/bin/nightshift play --mcp http://mcp-server:8000/mcp --policy "$POLICY" \
  --task "@@TASK_REF@@"
