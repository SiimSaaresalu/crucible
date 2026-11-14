from pathlib import Path

from nightshift.core.jobs import Job
from nightshift.core.registry import get_task
from nightshift.core.session import EpisodeSession
from nightshift.core.transcript import TranscriptWriter, read_transcript
from tests.core.helpers import guess_call


async def _start(tmp_path: Path, seed: int = 0) -> tuple[EpisodeSession, Path]:
    path = tmp_path / "t.jsonl"
    session = EpisodeSession(get_task("guess@1"), seed, TranscriptWriter(path))
    job = Job(task="guess@1", policy="external", seed=seed, repeat=0)
    await session.start(policy_name="external", job=job)
    return session, path


async def _secret(tmp_path: Path, seed: int) -> int:
    """Find the number for a seed by playing a throwaway session."""
    for n in range(1, 101):
        session, _ = await _start(tmp_path / f"probe{n}", seed)
        await session.step(guess_call(n))
        if session.task.done():
            await session.close()
            return n
        await session.close()
    raise AssertionError("no solution found")


async def test_budget_refusal_is_not_counted_or_written(tmp_path: Path) -> None:
    session, path = await _start(tmp_path)
    for _ in range(session.max_steps):
        assert not (await session.step(guess_call(0))).ok  # invalid, never solves
    assert session.steps_used == session.max_steps
    refused = await session.step(guess_call(1))
    assert not refused.ok
    assert refused.error == f"Step budget of {session.max_steps} calls is used up"
    assert session.steps_used == session.max_steps
    reason, score = await session.close()
    assert reason == "budget_exhausted"
    assert score.flags["budget_exhausted"] is True
    records = read_transcript(path)
    assert len(records.steps) == session.max_steps


async def test_over_refusal_after_task_done(tmp_path: Path) -> None:
    n = await _secret(tmp_path, 3)
    session, path = await _start(tmp_path, 3)
    assert (await session.step(guess_call(n))).ok
    assert session.task.done()
    refused = await session.step(guess_call(n))
    assert not refused.ok
    assert refused.error == "Episode is over"
    assert session.steps_used == 1
    reason, score = await session.close()
    assert reason == "task_done"
    assert score.flags["solved"] is True
    assert len(read_transcript(path).steps) == 1


async def test_closed_refusal_and_idempotent_close(tmp_path: Path) -> None:
    session, path = await _start(tmp_path)
    await session.step(guess_call(0))
    first = await session.close()
    assert first[0] == "policy_stop"
    assert session.closed
    assert await session.close() == first
    refused = await session.step(guess_call(1))
    assert refused.error == "Episode is closed"
    assert session.steps_used == 1
    assert len(read_transcript(path).steps) == 1


async def test_error_close(tmp_path: Path) -> None:
    session, _ = await _start(tmp_path)
    reason, score = await session.close("Boom")
    assert reason == "error"
    assert score.total == 0
    assert score.flags == {"env_error": True}
