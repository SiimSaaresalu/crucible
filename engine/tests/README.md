# Tests

Run everything with `uv run pytest` (from `engine/`). It collects `tests/` (core, server,
export, cross-environment checks) and each environment's own `src/crucible/envs/<name>/tests/`.

The nine tests from the brief, for `incident@1` (paths under `src/crucible/envs/incident/tests/`
unless they start with `tests/`):

| Brief test | Where |
|---|---|
| `test_determinism` | `test_task.py::test_same_input_same_transcript_and_score` (hypothesis) |
| `test_do_nothing_zero` | `test_grader.py::test_do_nothing_scores_exactly_zero`, `test_task.py::test_do_nothing_scores_zero` |
| `test_reference_solves` | `test_reference_incident.py::test_reference_solves` (100 seeds: healthy probes, correct root cause) |
| `test_stop_early_low` | `test_exploits.py::test_stop_early_low` |
| `test_no_hidden_leak` | `test_task.py::test_no_hidden_leak_in_results`; over HTTP in `tests/server/test_app.py`; over MCP in `tests/server/test_mcp.py` |
| `test_tool_validation` | `test_task.py::test_invalid_calls_change_nothing`; MCP bad call in `tests/server/test_mcp.py` |
| `test_exploits` | `test_exploits.py::test_exploits_below_reference` |
| `test_api_episode` | `tests/server/test_api_episode.py::test_api_episode` (HTTP result equals in-process result) |
| `test_parallel_same_result` | `tests/policies/test_parallel_same_result.py::test_concurrency_8_matches_concurrency_1` |

`tests/policies/golden_scores.json` holds pinned scores for every environment; do not edit it by
hand. `tests/policies/test_no_hidden_imports.py` checks every environment's `policies/`.

Environment tests can use `crucible.testing` (`ScriptedPolicy`, `play`); a plugin environment
can test itself the same way.

`tests/export/test_harbor.py` checks the Harbor export without Docker: the folder layout, `task.toml`
and `docker-compose.yaml`, and that each grade token is only in `[verifier.env]` and the
sidecar's environment. The real Harbor runs are manual; see `docs/harbor-runs.md`.
