import json
from pathlib import Path

import pytest

from crucible.core.rng import RngStreams
from crucible.core.scoring import Score
from crucible.core.tools import ToolCall, ToolResult
from crucible.core.transcript import (
    TranscriptWriter,
    git_commit,
    read_transcript,
)
from tests.core.helpers import ScriptedPolicy, guess_call, play


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "t.jsonl"
    writer = TranscriptWriter(path)
    writer.header(task="x@1", scoring_hash="h", policy="p", seed=3, repeat=1, job_id="abc")
    call = ToolCall(name="t", args={"a": [1, {"b": None}]})
    writer.step(0, call, ToolResult(ok=True, content={"k": "v"}))
    writer.step(1, call, ToolResult(ok=False, error="bad"))
    writer.footer(Score(total=0.5, breakdown={"b": 1.0}, flags={"f": True}), "task_done")
    writer.close()
    t = read_transcript(path)
    assert (t.header.task, t.header.seed, t.header.repeat, t.header.job_id) == ("x@1", 3, 1, "abc")
    assert t.header.schema_version == 1
    assert t.header.git_commit == git_commit()
    assert [s.step for s in t.steps] == [0, 1]
    assert t.steps[0].call == call
    assert t.steps[0].result.content == {"k": "v"}
    assert t.steps[1].result.error == "bad"
    assert t.footer.score == Score(total=0.5, breakdown={"b": 1.0}, flags={"f": True})
    assert t.footer.steps == 2
    assert t.footer.stop_reason == "task_done"
    assert t.footer.error is None
    types = [json.loads(ln)["type"] for ln in path.read_text().splitlines()]
    assert types == ["header", "step", "step", "footer"]


def test_tmp_file_until_close(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    writer = TranscriptWriter(path)
    assert not path.exists()
    assert (tmp_path / "t.jsonl.tmp").exists()
    writer.footer(Score(total=0), "policy_stop")
    writer.close()
    writer.close()
    assert path.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["t.jsonl"]


def test_abort_leaves_nothing(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    writer = TranscriptWriter(path)
    writer.abort()
    writer.abort()
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError, match="closed"):
        writer.footer(Score(total=0), "policy_stop")


def test_read_rejects_incomplete_file(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    writer = TranscriptWriter(path)
    writer.header(task="x@1", scoring_hash="h", policy="p", seed=0, repeat=0, job_id="a")
    writer.close()
    with pytest.raises(ValueError, match="header and end with a footer"):
        read_transcript(path)


def test_git_commit_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    git_commit.cache_clear()
    monkeypatch.setenv("CRUCIBLE_GIT_COMMIT", "deadbeef")
    try:
        assert git_commit() == "deadbeef"
        monkeypatch.setenv("CRUCIBLE_GIT_COMMIT", "other")
        assert git_commit() == "deadbeef"  # cached
    finally:
        git_commit.cache_clear()


def test_git_commit_falls_back_to_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    git_commit.cache_clear()
    monkeypatch.delenv("CRUCIBLE_GIT_COMMIT", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # no git here
    try:
        assert git_commit() == "unknown"
    finally:
        git_commit.cache_clear()


async def test_no_target_in_steps_before_correct_guess(tmp_path: Path) -> None:
    seed = 7
    target = RngStreams(seed).exogenous.randint(1, 100)
    calls = [guess_call(n) for n in (1, 100, 50, 25, 75)]
    calls = [c for c in calls if c.args["n"] != target] + [guess_call(target)]
    path = tmp_path / "t.jsonl"
    await play(ScriptedPolicy(calls), seed, path)
    t = read_transcript(path)
    for s in t.steps[:-1]:
        assert s.result.ok
        assert s.result.content in ("higher", "lower")
        result_json = s.result.model_dump_json()
        assert str(target) not in result_json
    assert t.steps[-1].result.content == "correct"
    assert "target" not in path.read_text()
