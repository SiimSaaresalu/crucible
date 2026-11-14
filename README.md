# Crucible

Interactive agent environments: a simulator and a grader, written one time, give MCP tools, seeded variants, baselines and exploit tests, and one command exports the tasks to Harbor.

- `engine/`: the Python engine, CLI and server. See [engine/README.md](engine/README.md).
- `engine/src/crucible/envs/<name>/`: one folder per environment, with its policies, tests and a `README.md` card. Other packages can add environments through the `crucible.envs` entry point.
- `docs/decisions.md`: design decisions. `docs/harbor-runs.md`: Harbor run scores.

## Pinned versions

| Tool | Version |
|---|---|
| Harbor | 0.26.0 |
| OpenCode | 1.19.5 (`--ak version=1.19.5`) |
| Python | 3.14 |
| uv | 0.12.18 |

## Run the Harbor tasks

```bash
uv run --directory engine crucible export harbor --task incident@1 --seeds 0-9 --out ../harbor/incident-1
harbor run -p harbor/incident-1 -a oracle -o jobs
```

OpenCode needs `GOOGLE_GENERATIVE_AI_API_KEY` (in `.env`):

```bash
set -a; . ./.env; set +a
harbor run -p harbor/incident-1 -a opencode -m google/gemini-3.8-flash \
  --ak version=1.19.5 --ak disable_web_search=true \
  --api-key-name GOOGLE_GENERATIVE_AI_API_KEY \
  --ae GOOGLE_GENERATIVE_AI_API_KEY=$GOOGLE_GENERATIVE_AI_API_KEY -o jobs
```

On macOS with Docker Desktop, `docker` may not be on `PATH`: `export PATH=$PATH:/Applications/Docker.app/Contents/Resources/bin`.

The sidecar build installs the engine wheel and its requirements from PyPI, so the build needs internet.
