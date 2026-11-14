"""Policy interface."""

from typing import Protocol

from crucible.core.task import TaskSpec
from crucible.core.tools import Observation, ToolCall, ToolSpec


class Policy(Protocol):
    """Chooses the next tool call. `act` returns `None` to stop."""

    name: str

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None: ...

    async def act(self, obs: Observation) -> ToolCall | None: ...


__all__ = ["Policy"]
