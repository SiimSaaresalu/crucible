# incident@1: on-call incident

The agent is the on-call engineer for a small web service. Something breaks. It must find the root cause from alerts, metrics, logs and deploys, repair it, and close the incident with the right diagnosis. Every minute it spends costs failed requests.

| | |
|---|---|
| Ref | `incident@1` |
| Entry point | `crucible.envs` → `incident = "crucible.envs.incident"` |
| Services | `api -> cache -> db` (api calls cache; on a miss or cache down, api calls db) |
| Step budget | 40 tool calls; the episode also ends at minute 120 |
| Root causes | `bad_deploy`, `cache_crash`, `db_disk_full` |
| Noise alerts | `cache_eviction_burst`, `api_latency_spike`, `db_slow_query` |
| Harbor resources | 1 CPU, 2048 MB, 900 s agent timeout |

## The job and the time model

- The episode is 120 simulated minutes. One fault starts at a random minute from 15 to 45.
- One noise alert ("herring") fires at another time. It changes logs and metrics a little but costs no requests.
- Some seeds have an unrelated deploy before the fault.
- Time moves only when the agent acts. Reads take 1 minute. `restart` takes 3, `rollback` 5, `clean_disk` 2, `scale` 1.
- `wait(minutes)` lets 1–30 minutes pass and stops early when a new alert fires.
- An invalid call is refused, changes nothing and takes no time, but still uses one of the 40 calls.

| Fault | Faulty service | Usual signs | Repair |
|---|---|---|---|
| `bad_deploy` | api | api errors after a deploy; api logs name the build | `rollback(deploy_id)` |
| `cache_crash` | cache | db overloaded, api slow; cache logs show it exited | `restart(cache)` |
| `db_disk_full` | db | db write errors, disk at the limit; db logs say no space | `clean_disk(db)` |

## Tools

| Tool | Kind | Effect |
|---|---|---|
| `get_topology` | read | the service graph |
| `get_alerts` | read | alerts firing now, with start minutes |
| `list_deploys` | read | deploys so far: id, service, minute |
| `get_metrics(service)` | read | error rate, p99 latency, CPU, disk now |
| `read_logs(service, since)` | read | last 20 log lines from `since` up to now |
| `wait(minutes)` | clock | 1–30 minutes; stops at a new alert |
| `restart(service)`, `rollback(deploy_id)`, `clean_disk(service)` | action | repairs |
| `scale(service, n)` | action | 1–5 instances; eases load, repairs nothing, costs instance-minutes |
| `resolve_incident(root_cause)` | end | closes the incident with a diagnosis; ends the episode |

Every result starts with the `minute` the call was made.

## Why it is hard

The right repair is easy once you look in the right place. The hard part is to look there first. Each extra read is one more minute of failed requests.

Two OpenCode + Gemini 3.8 Flash runs on the same world (seed 0, unsalted) show it. Both waited, saw `api_error_rate_high` at minute 47, listed deploys and found the right one:

```text
list_deploys  -> d-ae49 cache -46, d-9f1f cache -26, d-f0cf cache 20, d-b39f api 45
get_metrics(api) -> error_rate 0.556
```

Run A read the api logs next:

```text
read_logs(api, 44) -> "500 in checkout handler (build d-b39f)"   minute 49
rollback(d-b39f)                                                  minute 50
reward 0.918
```

Run B first checked the cache and db metrics, which were healthy:

```text
get_metrics(cache) -> error_rate 0.0      minute 49
get_metrics(db)    -> error_rate 0.006    minute 50
read_logs(api, 44) -> "500 in checkout handler (build d-b39f)"   minute 51
rollback(d-b39f)                                                  minute 52
reward 0.887
```

Two extra reads cost 0.031 reward. The cache deploy at minute 20 is a trap: a recent deploy, but on the wrong service. Rolling it back would be a wrong repair (−3000 requests) and would bring back an old bug.

## Scoring

```text
recovered_frac = (served_agent - served_nothing) / (served_oracle - served_nothing)
raw   = recovered_frac * (0.7 + 0.3 * diagnosis) - penalty / oracle_gain
total = clamp(raw, 0, 1)
```

- Doing nothing scores exactly 0. The oracle (right repair at the fault start) scores exactly 1.
- `diagnosis` is 1 when the resolved cause is right, the first repair was right, the agent read the faulty service's metrics or logs before that repair, and all probes are healthy at the end.
- Penalties: 3000 requests per wrong repair, 2000 per repair before the fault, 15 per instance-minute of scaling.

Weights are in `scoring.yaml`. Full explanation: [docs/scoring.md](../../../../../docs/scoring.md).

## Results

| Policy or agent | Where | Seeds | Mean | Min | Max | Root cause correct |
|---|---|---|---|---|---|---|
| `reference` | in process | 0–99 | 0.968 | 0.931 | 0.983 | 100% |
| `shotgun` | in process | 0–99 | 0.012 | 0.000 | 0.162 | 38% |
| `stop_early` | in process | 0–99 | 0.000 | 0.000 | 0.000 | 38% |
| `do_nothing` | in process | 0–99 | 0.000 | 0.000 | 0.000 | 0% |
| oracle (`solve.sh` → `reference`) | Harbor | 0–9 | 0.971 | 0.935 | 0.983 | 10/10 |
| OpenCode 1.19.5 + `google/gemini-3.8-flash` | Harbor | 0–2 | 0.955 | 0.918 | 0.983 | 3/3 |
| `nop` | Harbor | 0–2 | 0.000 | 0.000 | 0.000 | 0/3 |

Reproduce the in-process rows:

```bash
uv run --directory engine crucible eval --task incident@1 --policy reference,shotgun,stop_early,do_nothing --seeds 0-99 --concurrency 8
```

The Harbor rows are from [docs/harbor-runs.md](../../../../../docs/harbor-runs.md). They ran before exports were salted. Salted exports give each task a different world, so Harbor scores no longer equal the in-process score of the same seed.

## Exploits

| Policy | Trick | Why it fails |
|---|---|---|
| `stop_early` | reads alerts and topology, then guesses a cause | no repair, so nothing for the diagnosis bonus to multiply |
| `shotgun` | waits for any alert, then tries every repair and rollback | penalties and side effects; raw < 0 on 80 of 100 seeds |
| `scale_only` (test script) | scales every service to 5, then guesses | scaling repairs nothing and costs instance-minutes |

Each exploit must score below the reference on every seed (`tests/test_exploits.py` and the generic checks). More attacks and their blocks: [docs/reward-integrity.md](../../../../../docs/reward-integrity.md).

## Limits

- The gap between the reference (0.968) and Gemini 3.8 Flash (0.955) is small. The task does not separate strong models well. Faults that need more steps to find would help.
- There are three fault kinds and one herring per seed. A model that learns the runbook in the instruction does well.
- The reward mostly measures speed to the first right repair.
- In Harbor the agent's shell has internet (allowlist fails in Harbor 0.26.0). See [docs/reward-integrity.md](../../../../../docs/reward-integrity.md).

## Seeds and versions

- Any integer seed works. The seed fixes the fault, its start, demand, noise, deploys and the herring. Exports add a random salt per task.
- Golden scores for seeds 0–9 are in `engine/tests/policies/golden_scores.json`.
- `scoring_hash`: `1b4009a4457f1b406a0962c6de564c6b1744e88b93ea8b7c59dd05ae57cc5366`.
- Harbor 0.26.0, OpenCode 1.19.5, Python 3.14, uv 0.12.18.

## Layout

- `task.py`: the `Task` (tools, validation, observations, instruction).
- `world.py`: the seeded hidden world. `sim.py`: pure simulation over `(world, interventions, minute)`. `state.py`: episode state and public view models.
- `grader.py` + `scoring.yaml`: the reward.
- `policies/`: `reference`, `shotgun`, `stop_early`. They read tool results only, through `policies/views.py`.
- `tests/`: world, sim, grader, task, calibration, reference and exploit tests. Scripted solvers in `tests/scripts.py` may read the hidden world; real policies may not.
