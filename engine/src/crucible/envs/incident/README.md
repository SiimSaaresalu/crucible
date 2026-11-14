# incident@1: on-call incident

On-call incident response. A small web service (`api -> cache -> db`) runs for 120 simulated
minutes; one fault starts somewhere in minutes 15–45, with unrelated noise alerts around it. The
agent must find the root cause from tool results, repair it, and close the incident with the
right diagnosis.

| | |
|---|---|
| Ref | `incident@1` |
| Entry point | `crucible.envs` → `incident = "crucible.envs.incident"` |
| Step budget | 40 tool calls; the episode also ends at minute 120 |
| Seeds | any integer; the seed fixes the fault, its start minute, deploys, noise and herrings |
| Root causes | `bad_deploy`, `cache_crash`, `db_disk_full` |
| Noise alerts | `cache_eviction_burst`, `api_latency_spike`, `db_slow_query` (never a root-cause name) |

## Tools

| Tool | Kind | Effect |
|---|---|---|
| `get_topology`, `get_alerts`, `list_deploys` | read | service graph, firing alerts, deploy history (1 min) |
| `get_metrics(service)` | read | error rate, p99 latency, CPU, disk (1 min) |
| `read_logs(service, since)` | read | last 20 log lines since a minute (1 min) |
| `wait(minutes)` | clock | 1–30 minutes; stops early when a new alert fires |
| `restart`, `rollback`, `clean_disk` | action | repairs (3, 5, 2 min) |
| `scale(service, n)` | action | eases load, repairs nothing, costs instance-minutes |
| `resolve_incident(root_cause)` | end | closes the incident and ends the episode |

## Scoring

Reward in [0, 1]: requests recovered compared with doing nothing, divided by the gain of an
oracle that repairs at the fault start. Weights and penalties are in `scoring.yaml` (base 0.7,
diagnosis 0.3; penalties for wrong repairs, repairs in the healthy phase, and scaling). Doing
nothing scores exactly 0 and the oracle exactly 1. See `docs/decisions.md` for why.

## Policies

In `policies/`. They read tool results only, through `policies/views.py`; an `ast` test stops
them importing `task`, `world`, `sim`, `state` or `grader`.

| Policy | What it does | Golden mean, seeds 0–9 |
|---|---|---|
| `reference` | runbook: wait for an alert, read logs upstream, repair after a log signature, confirm, resolve | 0.971 |
| `shotgun` | fires every repair without triage (exploit baseline) | 0.014 |
| `stop_early` | guesses after two reads (exploit baseline) | 0.000 |
| `do_nothing` | generic; stops at once | 0.000 |

## Layout

- `task.py`: the `Task` (tools, validation, observations). `world.py`: seeded world. `sim.py`:
  pure simulation over `(world, interventions, minute)`. `state.py`: episode state.
  `grader.py` + `scoring.yaml`: the reward.
- `tests/`: world, sim, grader, task, calibration (scripted solvers in `tests/scripts.py` may read
  the hidden world; real policies may not), reference and exploit tests.
