# Tests

Run everything with `uv run pytest` (from `engine/`). The nine tests from the brief, for
`incident@1`:

| Brief test | Where |
|---|---|
| `test_determinism` | `incident/test_task.py::test_same_input_same_transcript_and_score` (hypothesis) |
| `test_do_nothing_zero` | `incident/test_grader.py::test_do_nothing_scores_exactly_zero`, `incident/test_task.py::test_do_nothing_scores_zero` |
| `test_reference_solves` | `policies/test_reference_incident.py::test_reference_solves` (100 seeds: healthy probes, correct root cause) |
| `test_stop_early_low` | `policies/test_exploits.py::test_stop_early_low` |
| `test_no_hidden_leak` | `incident/test_task.py::test_no_hidden_leak_in_results`; over HTTP in `server/test_app.py`; over MCP in `server/test_mcp.py` |
| `test_tool_validation` | `incident/test_task.py::test_invalid_calls_change_nothing`; MCP bad call in `server/test_mcp.py` |
| `test_exploits` | `policies/test_exploits.py::test_exploits_below_reference` |
| `test_api_episode` | `server/test_api_episode.py::test_api_episode` (HTTP result equals in-process result) |
| `test_parallel_same_result` | `policies/test_parallel_same_result.py::test_concurrency_8_matches_concurrency_1` |

`policies/golden_scores.json` holds pinned scores; do not edit it by hand.

`export/test_harbor.py` checks the Harbor export without Docker: the folder layout, `task.toml`
and `docker-compose.yaml`, and that each grade token is only in `[verifier.env]` and the
sidecar's environment. The real Harbor runs are manual; see `docs/harbor-runs.md`.

## Generic checks

`generic/test_generic.py` runs on every registered task, over `Task.checks.seeds` (20 by default):

| Check | Rule |
|---|---|
| `test_determinism` | same seed, same calls (reference and a seeded fuzzer) → same transcript and score |
| `test_do_nothing_scores_zero` | `do_nothing` scores exactly 0 |
| `test_reward_within_0_and_1` | reference, do-nothing, exploits and the fuzzer all score in 0–1 |
| `test_no_hidden_state_in_tool_results` | no word from `Task.hidden_strings()` in a successful result, unless the call itself said it |
| `test_reference_solves_every_seed` | `Task.solved(score)` for the reference on every seed |
| `test_exploits_score_below_reference` | each policy in `checks.exploit_policies` scores below the reference on every seed |
| `test_oracle_scores_one` | a task's `oracle` policy, if it registers one, scores exactly 1 |

A task turns a check off with a flag in its `checks` (`TaskChecks`), only when the check cannot
apply. `generic/test_runner.py` checks that the leak finder and the fuzzer work.

`test_scaffold.py` runs `nightshift new-env` in a copy of the engine and runs the generic checks
on the new environment in a separate process.

`core/test_salt.py` checks that a salt changes the world for the same seed and that no salt keeps it.
