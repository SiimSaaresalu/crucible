"""Multi-tour comparison policies that consume only public MCP observations."""

from typing import cast

from pydantic import JsonValue

from crucible.core.task import TaskSpec
from crucible.core.tools import Observation, ToolCall, ToolSpec
from crucible.envs.dispatch.policies.v2.routing import Mode, Planner
from crucible.envs.dispatch.policies.v2.views import MapView, StateView


class V2DispatchPolicy:
    """Cache static roads, read current state, then submit at most twelve plans."""

    name = "dispatch_v2"

    def __init__(self, mode: Mode = "greedy", stop_after_first: bool = False) -> None:
        self.mode: Mode = mode
        self.stop_after_first = stop_after_first
        self._map: MapView | None = None
        self._phase = "map"
        self._submitted = 0

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None:
        if task_spec.id != "dispatch" or task_spec.version != 2:
            raise ValueError("Multi-tour dispatch policies require dispatch@2")
        self._map = None
        self._phase = "map"
        self._submitted = 0

    async def act(self, obs: Observation) -> ToolCall | None:
        if self._phase == "map":
            self._phase = "state"
            return ToolCall(name="get_city_map")
        result = obs.last_result
        if result is None or not result.ok:
            return None
        if self._phase == "state":
            self._map = MapView.model_validate(result.content)
            self._phase = "plan"
            return ToolCall(name="get_dispatch_state")
        if self.stop_after_first and self._submitted:
            return ToolCall(name="finish")
        state = StateView.model_validate(result.content)
        if state.minute >= state.horizon:
            return None
        if self._map is None:
            raise RuntimeError("Static map must be read before planning")
        routes = Planner(state, self._map).build(self.mode)
        self._submitted += 1
        return ToolCall(name="submit_dispatch_plan", args={"routes": cast(JsonValue, routes)})
