"""Typed public tools for the private dynamic delivery simulation."""

from copy import deepcopy
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from crucible.core.registry import register_task
from crucible.core.scoring import Score
from crucible.core.task import Task, TaskChecks, TaskSpec
from crucible.core.tools import (
    Observation,
    ToolCall,
    ToolResult,
    ToolSpec,
    tool_map,
    validate_call,
)
from crucible.envs.dispatch.grader import (
    SCORING_CONFIG,
    SCORING_HASH,
    DeliveryRecord,
    DispatchScoreSpec,
    GradeInput,
)
from crucible.envs.dispatch.world import DispatchSimulation

_JSON = TypeAdapter(JsonValue)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    """A tool without arguments."""


class DispatchPlanArgs(_Args):
    """Replacement delivery sequences for the specified vehicles."""

    routes: dict[str, list[str]] = Field(
        description="Vehicle ID to ordered delivery IDs. Omitted vehicles keep their routes. "
        "An empty dictionary waits 30 minutes. Depot pickups are automatic."
    )


_TOOLS = [
    ToolSpec(
        name="get_dispatch_state",
        description="Read released orders, vehicles, cargo, routes, traffic and estimates. "
        "Does not advance time.",
        kind="read",
        args_model=NoArgs,
    ),
    ToolSpec(
        name="get_city_map",
        description="Read the public road network and current estimated travel times. "
        "Does not advance time.",
        kind="read",
        args_model=NoArgs,
    ),
    ToolSpec(
        name="submit_dispatch_plan",
        description="Replace allowed route suffixes, then advance 30 minutes and return state. "
        "Parcels must be collected at the depot; the edge already in progress is locked.",
        kind="action",
        args_model=DispatchPlanArgs,
    ),
    ToolSpec(
        name="finish",
        description="Stop dispatching. Committed routes continue until the six-hour shift ends; "
        "no new assignments are made. Return final public state.",
        kind="end",
        args_model=NoArgs,
    ),
]
_TOOL_MAP = tool_map(_TOOLS)


class _OrderRecord(BaseModel):
    """Only backend-owned order fields used by the private grader."""

    status: str
    value: float
    deadline: float
    grace: float
    delivered_at: float | None = None


class _VehicleRecord(BaseModel):
    distance: float


class _GradeState(BaseModel):
    orders: list[_OrderRecord]
    vehicles: list[_VehicleRecord]


@register_task
class DispatchTask(Task):
    """Run a six-hour dispatch shift through four public tools."""

    spec: ClassVar[TaskSpec] = TaskSpec(
        id="dispatch",
        version=1,
        max_steps=40,
        title="Dynamic delivery dispatch",
        description="Dispatch four capacity-limited vans during a six-hour shift with new "
        "orders, delivery deadlines and changing traffic.",
        keywords=("dispatch", "vehicle-routing", "online-planning", "real-backend"),
        timeout_sec=900,
    )
    scoring_hash: ClassVar[str] = SCORING_HASH
    checks: ClassVar[TaskChecks] = TaskChecks(exploit_policies=("stop_early",))

    def __init__(self) -> None:
        self._simulation: DispatchSimulation | None = None
        self._seed = 0
        self._salt: str | None = None
        self._actions: list[dict[str, list[str]]] = []
        self._grade_cache: Score | None = None

    async def reset(
        self, seed: int, difficulty: int = 0, *, salt: str | None = None
    ) -> Observation:
        if self._simulation is not None:
            await self._simulation.close()
        self._simulation = await DispatchSimulation.create(seed, salt=salt)
        self._seed, self._salt = seed, salt
        self._actions = []
        self._grade_cache = None
        # Observation.data is public; policies may optionally start with get_dispatch_state.
        return Observation(text=self.instruction(), data={"minute": 0})

    def tools(self) -> list[ToolSpec]:
        return list(_TOOLS)

    def done(self) -> bool:
        return self._simulation is not None and self._simulation.minute >= 360

    def hidden_strings(self) -> frozenset[str]:
        return frozenset(
            {"simulator_token", "generated_orders", "disruption_schedule", "world_salt"}
        )

    async def step(self, call: ToolCall) -> ToolResult:
        simulation = self._simulation
        if simulation is None:
            return ToolResult(ok=False, error="Episode not started: call reset first")
        if self.done():
            return ToolResult(ok=False, error="Episode is over")
        args = validate_call(_TOOL_MAP, call)
        if isinstance(args, ToolResult):
            return args
        if call.name == "get_city_map":
            return ToolResult(ok=True, content=_JSON.validate_python(simulation.city_map()))
        if call.name == "get_dispatch_state":
            state = await simulation.state()
        elif isinstance(args, DispatchPlanArgs):
            try:
                state = await simulation.submit_plan(args.routes)
            except ValueError as exc:
                return ToolResult(ok=False, error=str(exc))
            self._actions.append(deepcopy(args.routes))
            self._grade_cache = None
        else:
            state = await simulation.finish()
            self._grade_cache = None
        state["driving_cost_per_km"] = SCORING_CONFIG.driving_cost_per_km
        return ToolResult(ok=True, content=_JSON.validate_python(state))

    async def grade(self) -> Score:
        if self._grade_cache is not None:
            return self._grade_cache
        simulation = self._simulation
        if simulation is None:
            raise ValueError("Episode not started: call reset first")
        # Stopping the policy or exhausting the call budget still executes existing routes.
        # Replay in a separate database to leave live public state and history unchanged.
        if self.done():
            final = _GradeState.model_validate(await simulation.state())
        else:
            replay = await DispatchSimulation.create(self._seed, salt=self._salt)
            try:
                for routes in self._actions:
                    await replay.submit_plan(routes)
                final = _GradeState.model_validate(await replay.finish())
            finally:
                await replay.close()
        deliveries = tuple(
            DeliveryRecord(
                value=o.value,
                deadline=o.deadline,
                grace=o.grace,
                delivered_at=o.delivered_at,
            )
            for o in final.orders
            if o.status == "delivered" and o.delivered_at is not None
        )
        score = DispatchScoreSpec().compute(
            GradeInput(
                total_order_value=simulation.world.total_value,
                generated_orders=len(simulation.world.orders),
                deliveries=deliveries,
                distance_km=sum(v.distance for v in final.vehicles),
            )
        )
        self._grade_cache = score
        return score

    def instruction(self) -> str:
        return (
            "Operate four delivery vans for a six-hour (360-minute) shift. "
            "Use get_dispatch_state to see currently released orders, vehicle locations, "
            "capacity, cargo, routes, deadlines, values and current traffic estimates. "
            "Use get_city_map to inspect public roads. Reads do not advance time. "
            "submit_dispatch_plan(routes) maps vehicle IDs to ordered delivery IDs; omitted "
            "vehicles keep their routes. It advances 30 minutes and returns updated state. "
            "An empty routes dictionary waits. There are 12 decision intervals and a total "
            "budget of 40 tool calls including reads and rejected calls. "
            "All parcels originate at the depot. Vehicles automatically visit the depot for "
            "pickups and must respect capacity; the edge currently being driven cannot be changed. "
            "The total parcel size on each assigned route must fit the van capacity. "
            "Cargo already on a van must stay with that van and remain on its route. "
            "Future orders and traffic changes are unknown. "
            "finish stops new assignments while committed routes continue to minute 360. "
            "Stopping or exhausting your call budget has the same continuation behavior. "
            "Reward is delivered order value, reduced linearly for lateness over each order's "
            "grace period, minus 0.35 per driven kilometre, divided by the total value of all "
            "orders generated during the shift and clipped to [0,1]. Undelivered orders "
            "earn zero. Only physically collected and simulator-verified deliveries count."
        )


__all__ = ["DispatchPlanArgs", "DispatchTask", "NoArgs"]
