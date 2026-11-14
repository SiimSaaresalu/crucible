"""Statistics and private-world matching checks for the Harbor collector."""

import importlib.util
import json
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).with_name("collect_harbor.py")
SPEC = importlib.util.spec_from_file_location("dispatch_v2_collector_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def row(
    agent: str, world: str, reward: float, error: str | None = None
) -> dict[str, Any]:
    return {
        "agent": agent,
        "world_sha256": world,
        "seed": int(world),
        "reward": reward,
        "total_utility": reward * 100,
        "invalid_actions": 0,
        "error": error,
    }


def test_repeats_are_averaged_before_world_bootstrap() -> None:
    rows = [row("oracle", "0", 1), row("oracle", "1", 1)]
    rows += [row("opencode", "0", 0) for _ in range(10)]
    rows += [row("opencode", "1", 1)]
    report = collector.summarize(rows, 2, expected_repeats=1)
    difference = report["paired_reference_minus_model"]["reward"]
    assert difference["n_worlds"] == 2
    assert difference["mean"] == 0.5
    assert report["accepted_complete_comparison"]
    assert not collector.summarize(rows, 2)["accepted_complete_comparison"]
    assert collector.bootstrap([1.0, 0.0]) == collector.bootstrap([1.0, 0.0])


def test_missing_or_failed_world_remains_visible() -> None:
    rows = [row("oracle", "0", 1), row("opencode", "0", 0, "failed trial")]
    report = collector.summarize(rows, 2)
    assert report["agents"]["opencode"]["errors"] == 1
    assert report["expected_export_worlds"] == 2
    assert report["unpaired_worlds"][0]["valid_model_trials"] == 0
    assert not report["accepted_complete_comparison"]


def test_failed_oracle_process_is_not_a_legitimate_zero_score(tmp_path: Path) -> None:
    export = tmp_path / "export"
    folder = export / "dispatch-2-seed-1"
    (folder / "environment").mkdir(parents=True)
    (folder / "environment/docker-compose.yaml").write_text("private-world")
    (export / "manifest.json").write_text(
        json.dumps(
            {"tasks": [folder.name], "task_ref": "dispatch@2", "wheel_sha256": "wheel"}
        )
    )
    trial = tmp_path / "jobs/pilot-oracle/dispatch-2-seed-1__trial"
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir()
    (trial / "agent/exit-code.txt").write_text("2")
    (trial / "result.json").write_text(
        json.dumps({"task_id": {"path": str(folder)}, "exception_info": None})
    )
    (trial / "verifier/grade.json").write_text(
        json.dumps(
            {
                "reward": 0,
                "steps": 0,
                "stop_reason": "policy_stop",
                "flags": {},
                "breakdown": {"total_utility": 0},
            }
        )
    )
    (trial / "verifier/transcript.json").write_text("[]")
    rows = collector.collect(tmp_path / "jobs", "pilot", export)
    assert rows[0]["agent_exit_code"] == "2"
    assert "agent exited with code 2" in rows[0]["error"]
    report = collector.summarize(rows, 1)
    assert report["agents"]["oracle"]["completed_valid_trials"] == 0
    assert report["agents"]["oracle"]["errors"] == 1


def test_foreign_export_rejected_and_private_contents_not_copied(
    tmp_path: Path,
) -> None:
    export = tmp_path / "export"
    folder = export / "dispatch-2-seed-1"
    (folder / "environment").mkdir(parents=True)
    (folder / "environment/docker-compose.yaml").write_text("secret-salt-and-token")
    (export / "manifest.json").write_text(
        json.dumps(
            {
                "tasks": [folder.name],
                "task_ref": "dispatch@2",
                "wheel_sha256": "wheel-digest",
            }
        )
    )
    trial = tmp_path / "jobs/pilot-oracle/dispatch-2-seed-1__trial"
    (trial / "verifier").mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_id": {"path": str(tmp_path / "different-export" / folder.name)},
                "exception_info": None,
            }
        )
    )
    (trial / "verifier/grade.json").write_text(
        json.dumps(
            {
                "reward": 1.0,
                "steps": 14,
                "stop_reason": "task_done",
                "flags": {},
                "breakdown": {"total_utility": 100.0},
            }
        )
    )
    (trial / "verifier/transcript.json").write_text("[]")
    rows = collector.collect(tmp_path / "jobs", "pilot", export)
    assert len(rows) == 1
    assert "export path" in rows[0]["error"]
    assert "secret-salt-and-token" not in json.dumps(rows)
