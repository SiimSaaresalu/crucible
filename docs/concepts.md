# Concepts

The words we use in Crucible, each with an example from `incident@1`.

## One rollout

```text
            seed (+ salt)
                 │
                 ▼
  ┌────────── world ──────────┐   hidden: fault, start minute, deploys, noise
  │                           │
  │   reset ──► observation   │   instruction text, minute 0
  │                │          │
  │      ┌─────────▼───────┐  │
  │      │  policy.act()   │◄─┼──── observation (last tool result only)
  │      └─────────┬───────┘  │
  │           tool call       │
  │                ▼          │
  │       task.step() ────────┼──► tool result (no hidden state)
  │                │          │
  │    repeat until done or step budget
  │                ▼          │
  │       grader (task.grade) │   reads the hidden world + episode record
  └────────────────┬──────────┘
                   ▼
        reward 0–1 + breakdown + flags  ──►  transcript (JSONL)
```

## Glossary

**Environment.** A package under `engine/src/crucible/envs/<name>/`: a task, its policies, tests and a `README.md` card. It registers through the `crucible.envs` entry point.
*Example:* `engine/src/crucible/envs/incident/`.

**Task ref.** The id and version of a task, written `id@version`. A change that moves scores needs a new version.
*Example:* `incident@1`. `crucible eval --task incident@1`.

**Seed.** An integer that fixes the hidden world. The same seed always gives the same world.
*Example:* seed 2 is a `cache_crash`; the cache logs `out of memory, process exited` at minute 28.

**Salt.** An optional string mixed into the seed. A salted world differs from the unsalted world of the same seed. The Harbor export writes a new random salt per task, so an agent cannot rebuild the world from the public seed.
*Example:* the sidecar reads `SIDECAR_SALT`; the transcript header says `"salted": true` but never holds the salt.

**Tool.** A typed action the agent can call. Each tool has a name, a description, a kind (`read`, `action`, `clock`, `end`) and a JSON Schema for its arguments.
*Example:* `read_logs(service, since)` is a `read` tool that costs 1 simulated minute.

**Observation.** What the policy sees before each action: `text`, `data` and `last_result`. It never holds hidden state.
*Example:* after `get_alerts`, `last_result.content` is `{"minute": 31, "alerts": []}`.

**Policy.** Code that picks the next tool call from the observation. `act` returns `None` to stop. A model behind an MCP client is a policy too.
*Example:* `reference`, `shotgun`, `stop_early`, `do_nothing`.

**Rollout.** One episode: reset, then steps until the task is done or the policy stops, then grading. In Harbor one rollout is one **trial**.
*Example:* one row in `summary.csv`; one `incident-1-seed-0__<id>` folder in a Harbor job.

**Step budget.** The most tool calls in one rollout (`TaskSpec.max_steps`). Invalid calls count too.
*Example:* 40 calls for `incident@1`. The episode also ends at minute 120.

**Reward.** The single number in 0–1 that grading returns (`Score.total`).
*Example:* the reference scores 0.935 on seed 0.

**Grader.** The code that turns the hidden world plus the episode record into a `Score` (total, breakdown, flags).
*Example:* `IncidentScoreSpec` in `envs/incident/grader.py`, with weights in `scoring.yaml`.

**Reference.** A hand-written policy that solves the task from tool results alone. It shows the task is solvable and sets the bar for exploits.
*Example:* the incident runbook: wait for an alert, read logs upstream, repair after a log signature, confirm, resolve. Mean 0.968 on seeds 0–99.

**Baselines.** Simple policies that bound the reward from below. Exploit baselines try a cheap trick and must score below the reference on every seed.
*Example:* `do_nothing` (0.000), `stop_early` (0.000), `shotgun` (0.012).

**Oracle / anchors.** Fixed points that set the reward scale. The do-nothing replay anchors 0. The oracle anchors 1: it applies the right repair the moment the fault starts. (Harbor also uses "oracle" for the agent that runs `solution/solve.sh`; in our export that script plays the `reference` policy.)
*Example:* `oracle_interventions(world)` in `grader.py`.

**Sidecar.** The container that runs the engine next to the agent in Harbor (`MODE=sidecar`). It serves one episode as MCP at `/mcp` and the grade at `/grade`.
*Example:* `mcp-server` in the generated `environment/docker-compose.yaml`.

**Grade token.** A random secret that `/grade` needs in the `x-grade-token` header. Without it the route returns 403. Only the verifier has it.
*Example:* `GRADE_TOKEN` in `[verifier.env]` of `task.toml` and in the sidecar's environment.

**Export.** `crucible export harbor` writes one Harbor task folder per seed, with the engine wheel vendored into the sidecar and `solution/`.
*Example:* `harbor/incident-1/incident-1-seed-0/`.

**Job.** Two meanings. In Crucible, a `Job` is one planned rollout (task, policy, seed, repeat), and `crucible eval` runs a list of them. In Harbor, a job is one `harbor run`, with one folder of trials under `jobs/`.
*Example:* `harbor run ... --job-name incident-1-oracle` writes `jobs/incident-1-oracle/`.
