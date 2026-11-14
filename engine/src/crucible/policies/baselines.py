"""Task-agnostic baseline policies."""

from crucible.core.registry import register_policy
from crucible.core.task import TaskSpec
from crucible.core.tools import Observation, ToolCall, ToolSpec


@register_policy("do_nothing")
class DoNothing:
    """Stops at once. Scores the floor on every task."""

    name = "do_nothing"

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None:
        return None

    async def act(self, obs: Observation) -> ToolCall | None:
        return None


__all__ = ["DoNothing"]
