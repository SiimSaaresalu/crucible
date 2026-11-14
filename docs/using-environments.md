# Using environments

Run the commands from the repo root unless a section says otherwise. `uv run --directory engine` runs the CLI inside `engine/`, so relative paths such as `--out ../harbor/...` are relative to `engine/`.

## Setup

1. Install [uv](https://docs.astral.sh/uv/) 0.12.18. uv installs Python 3.14 for you.
2. Install Docker. Harbor and the sidecar image need it.
3. Install Harbor 0.26.0 (the `harbor` command).
4. For OpenCode runs, put your Gemini key in `.env` at the repo root:

   ```text
   GOOGLE_GENERATIVE_AI_API_KEY=...
   ```

   `.env` is gitignored. OpenCode reads this exact name.

Check the engine:

```bash
uv run --directory engine pytest -q
```

## List tasks and policies

```bash
uv run --directory engine crucible tasks
```

```bash
uv run --directory engine crucible policies --task incident@1
```

For `incident@1` this prints `do_nothing`, `reference`, `shotgun` and `stop_early`.

## Evaluate policies in process

```bash
uv run --directory engine crucible eval --task incident@1 --policy reference,shotgun,stop_early,do_nothing --seeds 0-9 --concurrency 4
```

| Flag | Meaning |
|---|---|
| `--task` | task ref, e.g. `incident@1` |
| `--policy` | comma-separated policy names (default `reference`) |
| `--seeds` | e.g. `0-9,12` (default `0-9`) |
| `--repeats` | episodes per seed (default 1) |
| `--concurrency` | episodes run at once (default 1); results do not change |
| `--out` | output folder; reuse it to resume (default `runs/<task>-<time>`) |
| `--per-seed` | also print one row per episode |

The command prints one table: mean, min, max and the share of episodes with each flag. The output folder holds `summary.csv` and one JSONL transcript per episode in `episodes/`.

## Serve and play

`crucible serve` runs the HTTP server. Settings come from environment variables (see [engine/README.md](../engine/README.md#server)).

API mode serves many episodes under `/v1`:

```bash
uv run --directory engine crucible serve
```

In a second shell, play the reference against it:

```bash
uv run --directory engine crucible play --api http://localhost:8000 --task incident@1 --seed 0 --policy reference
```

This prints `{"steps": 8, "stop_reason": "task_done", "score": 0.934778345662945}`, the same score as `crucible eval` on seed 0.

Sidecar mode serves one episode as MCP, like inside Harbor:

```bash
MODE=sidecar GRADE_TOKEN=secret SIDECAR_TASK=incident@1 SIDECAR_SEED=3 uv run --directory engine crucible serve
```

```bash
uv run --directory engine crucible play --mcp http://localhost:8000/mcp --task incident@1 --policy reference
```

```bash
curl -H 'x-grade-token: secret' http://localhost:8000/grade
```

`play --mcp` prints no score, because only the token holder can grade. Without the header, `/grade` returns 403. Set `SIDECAR_SALT` to salt the world.

## Export to Harbor

```bash
uv run --directory engine crucible export harbor --task incident@1 --seeds 0-9 --out ../harbor/incident-1
```

This writes `harbor/incident-1/incident-1-seed-<n>/` for each seed:

```text
incident-1-seed-0/
  task.toml            # metadata, resources, MCP server, [verifier.env] GRADE_TOKEN
  instruction.md       # the task instruction
  environment/         # agent container (ubuntu, no engine) + mcp-server sidecar
  solution/solve.sh    # the oracle: plays a policy over MCP
  tests/test.sh        # the verifier: calls /grade with the token
```

| Flag | Meaning |
|---|---|
| `--task` | required |
| `--seeds` | default `0-9` |
| `--out` | required; a Harbor dataset folder |
| `--solution-policy` | policy that `solve.sh` plays (default `reference`) |
| `--force` | replace a non-empty `--out` |
| `--wheel`, `--requirements` | use a prebuilt engine wheel; give both or neither |

Every export makes a new grade token and a new salt per task. `harbor/` is gitignored. Do not commit it.

## Run in Harbor

On macOS with Docker Desktop, put `docker` on `PATH` first:

```bash
export PATH=$PATH:/Applications/Docker.app/Contents/Resources/bin
```

Oracle (runs `solution/solve.sh`, which plays the reference):

```bash
harbor run -p harbor/incident-1 -a oracle -o jobs --job-name incident-1-oracle
```

No-op agent (does nothing; the reward must be 0):

```bash
harbor run -p harbor/incident-1 -a nop -o jobs --job-name incident-1-nop -l 3
```

OpenCode with Gemini:

```bash
set -a; . ./.env; set +a
```

```bash
harbor run -p harbor/incident-1 -a opencode -m google/gemini-3.8-flash \
  --ak version=1.19.5 --ak disable_web_search=true \
  --api-key-name GOOGLE_GENERATIVE_AI_API_KEY \
  --ae GOOGLE_GENERATIVE_AI_API_KEY=$GOOGLE_GENERATIVE_AI_API_KEY \
  -o jobs --job-name incident-1-opencode-gemini -l 3
```

| Flag | Meaning |
|---|---|
| `-p` | path to the exported dataset folder |
| `-a` | agent: `oracle`, `nop`, `opencode`, ... |
| `-m` | model, for model agents |
| `-o` | jobs folder (`jobs/` is gitignored) |
| `--job-name` | folder name of this job |
| `-l` | run at most this many tasks |
| `-n` | trials run at once |
| `--ak version=1.19.5` | pin OpenCode |
| `--ak disable_web_search=true` | turn off OpenCode's web search tool |
| `--api-key-name`, `--ae` | pass the Gemini key into the agent container |

## Read the results

Each trial folder has `verifier/` with three files:

- `reward.txt`: the 0–1 reward Harbor reports, e.g. `0.9820211287992733`.
- `grade.json`: `reward`, `breakdown`, `flags`, `steps` and `stop_reason`. Use it to see why a reward is what it is.
- `transcript.json`: the full rollout. The first record is the header (task, seed, `scoring_hash`, `salted`). Then one record per tool call with its arguments and result. The last record is the footer with the score.

For example, `grade.json` from the `guess@1` oracle:

```json
{
  "reward": 0.5,
  "breakdown": {"guesses": 6.0},
  "flags": {"solved": true, "budget_exhausted": false},
  "steps": 6,
  "stop_reason": "task_done"
}
```

`agent/` holds the agent's own logs (for OpenCode, `trajectory.json`). Browse jobs in a web UI:

```bash
harbor view jobs
```

Transcripts from `crucible eval` use the same JSONL format, one file per episode in `<out>/episodes/`.

## Troubleshooting

| Error | Cause and fix |
|---|---|
| `ProviderAuthError` (OpenCode) | The key did not reach the agent. Pass `--api-key-name GOOGLE_GENERATIVE_AI_API_KEY --ae GOOGLE_GENERATIVE_AI_API_KEY=$GOOGLE_GENERATIVE_AI_API_KEY`, after loading `.env`. |
| `conflicting options: port exposing and the container type network mode` | A task sets `[agent] network_mode = "allowlist"`. This fails on the sidecar in Harbor 0.26.0. The export does not set it; remove it if you added it. |
| `VerifierOutputParseError` | A verifier wrote a nested `reward.json`. Harbor 0.26.0 wants only numbers there. Our `test.sh` writes `reward.txt` and `grade.json` instead; re-export if you have an old task folder. |
| `docker: command not found` | Docker Desktop on macOS: `export PATH=$PATH:/Applications/Docker.app/Contents/Resources/bin`. |
| Sidecar build fails to download packages | The sidecar installs the engine's requirements from PyPI. The build needs internet. |
| `GRADE_TOKEN is required in sidecar mode` | Set `GRADE_TOKEN` when you run `MODE=sidecar`. |
| `Missing option '--task'` | `play` and `export harbor` have no default task. Pass `--task`. |
| `... is not empty; pass --force to replace it` | `--out` already has files. Pick a new folder or pass `--force`. |
| `Unknown task 'incident@2'. Known tasks: ...` | Check `crucible tasks` for the right ref. |
| MCP call returns `Episode is closed` | `/grade` closes the episode. Start a new sidecar for a new run. |
| Harbor oracle reward differs from `crucible eval` on the same seed | Expected. Exports are salted, so the Harbor world is not the in-process world of that seed. |
