# Dispatch HTTP and Harbor verification

Executed 2026-11-14 with Harbor 0.26.0, Docker 29.9.3, Python 3.14.8,
OpenCode pin 1.19.5, and the working-tree implementation of `dispatch@1`.

## Reproduce

From a fresh checkout with Docker running:

```bash
./spikes/dynamic-dispatch/reproduce.sh
```

This installs the locked dependencies, runs the held-out and salted local
comparisons, builds the engine wheel, exports three salted Harbor worlds, and
runs the oracle and nop. Model evaluation is separate:

```bash
# Supply GOOGLE_GENERATIVE_AI_API_KEY in your environment.
./spikes/dynamic-dispatch/reproduce.sh frontier
```

The default export is `harbor/dispatch-1`; existing exports are retained rather
than overwritten. For another complete run, select a fresh export directory and
job prefix:

```bash
DISPATCH_EXPORT_DIR=harbor/dispatch-repeat \
DISPATCH_JOB_PREFIX=dispatch-repeat ./spikes/dynamic-dispatch/reproduce.sh
```

To retain exactly the same salted worlds, reuse the existing export and run
`reproduce.sh harbor` or `reproduce.sh frontier` with a fresh job prefix.
Re-exporting generates new private salts, so equal seed numbers alone do not
identify the same Harbor world.

## Actual core Harbor runs

The following commands were executed against one preserved export:

```bash
UV_CACHE_DIR=/tmp/crucible-uv-cache engine/.venv/bin/crucible export harbor \
  --task dispatch@1 --seeds 1000-1002 --out harbor/dispatch-1
harbor run -p harbor/dispatch-1 -a oracle -o jobs --job-name dispatch-1-oracle -n 3
harbor run -p harbor/dispatch-1 -a nop -o jobs --job-name dispatch-1-nop -n 3
```

| Seed | Oracle reward | Deliveries | On-time deliveries | Distance km | Calls | Nop reward |
|---|---:|---:|---:|---:|---:|---:|
| 1000 | 0.8706336088749412 | 33/35 | 26 | 220.828427 | 13 | 0 |
| 1001 | 0.9594805859434986 | 31/31 | 30 | 212.970562 | 13 | 0 |
| 1002 | 0.9278228830910823 | 31/31 | 27 | 204.000000 | 13 | 0 |

Oracle mean reward: **0.919312359303174**. Both jobs completed all three trials
with zero exceptions. Replaying each exported private salt locally matched the
oracle reward, full economic breakdown, and 13 calls exactly on every seed.
Oracle runtime was 32 seconds including image builds; nop
runtime was 13 seconds. Frontier results are reported with the other experiment
findings, using the same export.

Raw logs remain locally under `jobs/dispatch-1-oracle/` and
`jobs/dispatch-1-nop/`. Each trial retains `agent/oracle.txt` (oracle only),
`verifier/transcript.json`, `verifier/grade.json`, `verifier/reward.txt`,
`trial.log`, and `result.json`. The job-level `result.json` records aggregate
outcomes. These runtime directories and exports are gitignored; exports contain
private grading capabilities and world salts.

The exported engine wheel SHA-256 is
`a6ba6736d554e6a6412c9e4b954c348d7007ae926c42a2f90f66de8d18a68599`.
Inspection found 21 dispatch files, including the FastAPI service, SQLite store,
private world, grader, scoring YAML, and policies. Environment tests are excluded
from the wheel. The export manifest records this wheel digest and base Git
commit; the digest identifies the built working-tree artifact.

## Backend and sidecar boundaries

Each episode uses a separate on-disk SQLite database under a private temporary
directory. Locally and in the Harbor sidecar, the simulator serializes requests
through the FastAPI application's HTTP interface using HTTPX ASGI transport.
This keeps the backend inside the simulator container without a second exposed
network port. The backend also runs independently over TCP and accepts the same
HTTP requests through `DispatchBackend(base_url=..., simulator_token=...)`.

The standalone process can be started with:

```bash
export DISPATCH_DATABASE=/tmp/dispatch-demo.sqlite3
export DISPATCH_SIMULATOR_TOKEN="$(openssl rand -hex 32)"
uv run --directory engine uvicorn crucible.envs.dispatch.backend_service.app:app \
  --host 127.0.0.1 --port 8081
```

`test_backend_http.py` starts this standalone service as an independent Uvicorn
process, executes authenticated order creation, assignment, pickup, movement,
and delivery over real TCP, and restarts the process against the same SQLite
file. State and transaction history match exactly after restart. An
unauthenticated completion request receives HTTP 403.

Only the existing MCP server port 8000 is exposed in Harbor. Backend control
routes are absent from the public sidecar application; `/state`, `/history`,
`/events`, `/reset`, `/orders`, and `/plans` return 404 there. The grading endpoint
requires its existing verifier capability and returns 403 without it. The agent
container contains neither the engine wheel nor the backend database; the wheel
is vendored only into the sidecar and oracle solution.

`test_harbor.py` exercises a full reference episode over a real MCP connection
and checks exact local score/call parity, private grading, export layout, and
absence of backend routes. `test_exploits.py` checks atomic rejection of invented
or unreleased orders, duplicate/cross-vehicle assignments, one-van overload,
excessively long routes, fabricated completion, timestamps, values, deadlines,
and location edits. Completed orders cannot be credited twice. The world tests
cover locked-edge rerouting and physical depot pickup; backend tests cover
onboard cargo and capacity enforcement.

Run these checks with:

```bash
cd engine
uv run pytest src/crucible/envs/dispatch/tests/test_exploits.py \
  src/crucible/envs/dispatch/tests/test_harbor.py \
  src/crucible/envs/dispatch/tests/test_backend_http.py
```
