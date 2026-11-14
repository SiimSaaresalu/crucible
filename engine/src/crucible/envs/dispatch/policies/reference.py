"""Online dispatch reference: value-weighted regret insertion and local route improvement."""

from typing import cast

from pydantic import JsonValue

from crucible.core.policy import Policy
from crucible.core.registry import register_policy
from crucible.core.task import TaskSpec
from crucible.core.tools import Observation, ToolCall, ToolSpec
from crucible.envs.dispatch.policies.planning import Mode, Planner
from crucible.envs.dispatch.policies.views import StateView


class DispatchPolicy:
    """One public state read followed by at most twelve plan submissions."""

    name = "dispatch"
    mode: Mode = "regret"
    stop_after_first = False

    def __init__(self) -> None:
        self._read = False
        self._submitted = 0
        self._version_policy: Policy | None = None

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None:
        self._read = False
        self._submitted = 0
        self._version_policy = None
        if task_spec.version == 2:
            if self.name == "reference":
                from crucible.envs.dispatch.policies.v2.solver import RollingHorizonSolver

                self._version_policy = RollingHorizonSolver()
            else:
                from crucible.envs.dispatch.policies.v2.baselines import V2DispatchPolicy

                self._version_policy = V2DispatchPolicy(
                    mode=self.mode, stop_after_first=self.stop_after_first
                )
            await self._version_policy.reset(task_spec, tools, instruction)
        elif task_spec.version != 1:
            raise ValueError(f"Unsupported dispatch version: {task_spec.version}")

    async def act(self, obs: Observation) -> ToolCall | None:
        if self._version_policy is not None:
            return await self._version_policy.act(obs)
        if not self._read:
            self._read = True
            return ToolCall(name="get_dispatch_state")
        if self.stop_after_first and self._submitted:
            return ToolCall(name="finish")
        result = obs.last_result
        if result is None or not result.ok:
            return None
        state = StateView.model_validate(result.content)
        if state.minute >= state.horizon:
            return None
        routes = Planner(state).build(self.mode)
        self._submitted += 1
        return ToolCall(name="submit_dispatch_plan", args={"routes": cast(JsonValue, routes)})


@register_policy("reference", "dispatch")
class RegretInsertion(DispatchPolicy):
    """Reference: schedule valuable opportunities with few good alternative vans first."""

    name = "reference"


@register_policy("regret_insertion", "dispatch")
class NamedRegretInsertion(RegretInsertion):
    name = "regret_insertion"


__all__ = ["DispatchPolicy", "NamedRegretInsertion", "RegretInsertion"]
