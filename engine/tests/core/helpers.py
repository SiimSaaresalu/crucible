"""Shared helpers for the episode tests."""

from pathlib import Path

from nightshift.core.episode import EpisodeResult, run_episode
from nightshift.core.jobs import Job
from nightshift.core.policy import Policy
from nightshift.core.registry import get_task
from nightshift.core.task import TaskSpec
from nightshift.core.tools import Observation, ToolCall, ToolSpec
from nightshift.core.transcript import TranscriptWriter


class ScriptedPolicy:
    """Plays a fixed list of calls, then stops."""

    name = "scripted"

    def __init__(self, calls: list[ToolCall]) -> None:
        self._calls = calls
        self._i = 0

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None:
        self._i = 0

    async def act(self, obs: Observation) -> ToolCall | None:
        if self._i >= len(self._calls):
            return None
        call = self._calls[self._i]
        self._i += 1
        return call


def guess_call(n: int) -> ToolCall:
    return ToolCall(name="guess", args={"n": n})


async def play(
    policy: Policy, seed: int, path: Path, task_ref: str = "guess@1", repeat: int = 0
) -> EpisodeResult:
    job = Job(task=task_ref, policy=policy.name, seed=seed, repeat=repeat)
    return await run_episode(
        get_task(task_ref), policy, seed, job=job, writer=TranscriptWriter(path)
    )
