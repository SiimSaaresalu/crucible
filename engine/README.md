# crucible engine

Deterministic task engine: environments, policies, an episode runner and a server.

## Layout

```
src/crucible/
  core/      task and policy interfaces, registry, episode runner, executor, transcripts
  server/    HTTP API and the MCP sidecar
  agents/    remote policy clients (HTTP, MCP)
  export/    Harbor export
  policies/  task-agnostic policies (`do_nothing`)
  testing.py helpers for environment tests (`ScriptedPolicy`, `play`)
  envs/<name>/
    README.md  the environment card: tools, scoring, policies, golden scores
    task.py    the task (plus its world, simulator and grader modules)
    policies/  policies for this environment; tool results only
    tests/     this environment's tests (kept out of the wheel)
```

Built-in environments: [`incident`](src/crucible/envs/incident/README.md),
[`guess`](src/crucible/envs/guess/README.md).

## Environment plugins

The registry loads environments from the `crucible.envs` entry point group, the built-ins
included. Each entry point names a module whose import registers the environment's tasks
(`@register_task`) and policies (`@register_policy(name, task_id)`). A separate package adds an
environment with:

```toml
[project.entry-points."crucible.envs"]
myenv = "my_package.myenv"
```

Install it into the same environment as `crucible` and `crucible tasks` lists it. A module that
fails to import raises `EnvLoadError`, which names the entry point.

## Commands

```sh
uv run crucible tasks
uv run crucible policies --task incident@1
uv run crucible eval --task incident@1 --policy reference --seeds 0-9
```

## Server

`crucible serve` runs the HTTP server. It reads its settings from the environment.

| Variable | Default | Meaning |
|---|---|---|
| `MODE` | `api` | `api` or `sidecar` |
| `PORT` | `8000` | listen port |
| `LOG_LEVEL` | `info` | log level |
| `RUNS_DIR` | `runs/server` | where transcripts are written |
| `SESSION_TTL_SECONDS` | `3600` | idle time before an API episode is dropped |
| `GRADE_TOKEN` | empty | required in sidecar mode; the `x-grade-token` header for `/grade` |
| `SIDECAR_TASK` | `incident@1` | task served in sidecar mode |
| `SIDECAR_SEED` | `0` | seed served in sidecar mode |

Two modes:

- `api` (default): many episodes under `/v1` (`GET /v1/tasks`, `GET /v1/tasks/{id}/tools`,
  `POST /v1/episodes`, `POST /v1/episodes/{id}/step`, `POST /v1/episodes/{id}/close`).
  `GET /healthz` returns 200.
- `sidecar`: one episode, served as MCP at `/mcp`, and the score at `GET /grade`, which needs the
  `x-grade-token` header. There is no `/v1`.

```sh
uv run crucible serve                                            # api mode on :8000
MODE=sidecar GRADE_TOKEN=secret uv run crucible serve            # sidecar mode

# Play a policy against a running server; prints one JSON line.
uv run crucible play --api http://localhost:8000 --task incident@1 --seed 0 --policy reference
uv run crucible play --mcp http://localhost:8000/mcp --task incident@1 --policy reference   # sidecar
curl -H 'x-grade-token: secret' http://localhost:8000/grade
```

Docker (run from `engine/`):

```sh
docker build --build-arg CRUCIBLE_GIT_COMMIT=$(git rev-parse HEAD) -t crucible-engine .
docker run -p 8000:8000 crucible-engine
docker run -p 8000:8000 -e MODE=sidecar -e GRADE_TOKEN=secret crucible-engine
scripts/docker_smoke.sh   # build, one full /v1 episode, sidecar token check
```

## Harbor export

`crucible export harbor` writes one Harbor task folder per seed. It builds the engine wheel
and vendors it into each task's sidecar and `solution/`; the agent's container gets no engine.

```sh
uv run crucible export harbor --task incident@1 --seeds 0-9 --out ../harbor/incident-1
uv run crucible export harbor ... --solution-policy stop_early   # oracle plays stop_early
uv run crucible export harbor ... --force                        # replace a non-empty --out
```

Each task gets a new grade token on every export. The token is only in `[verifier.env]` and the
sidecar's environment, so do not commit the output (`harbor/` is gitignored). The verifier
writes `reward.txt`, `grade.json` (breakdown, flags, steps, stop reason) and `transcript.json`
to `/logs/verifier/`.

Tests: see [tests/README.md](tests/README.md).
