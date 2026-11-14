"""Compact public MCP contract and private economic grading for dispatch@2."""

from copy import deepcopy
from importlib.resources import as_file, files
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from crucible.core.registry import register_task
from crucible.core.scoring import Score, load_scoring_config
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
    DeliveryRecord,
    DispatchScoreSpec,
    GradeInput,
    ScoringConfig,
)
from crucible.envs.dispatch.v2.world import DispatchSimulation

_JSON = TypeAdapter(JsonValue)
with as_file(files("crucible.envs.dispatch.v2") / "scoring.yaml") as _path:
    SCORING_CONFIG, SCORING_HASH = load_scoring_config(_path, ScoringConfig)

REGIMES = ("A", "B", "C", "D")
DEFAULT_REGIME = "D"


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DispatchPlanArgs(NoArgs):
    routes: dict[str, list[list[str]]] = Field(
        description="Vehicle ID to ordered depot-load tours, each an ordered list of delivery IDs. "
        "Omitted vehicles retain their plans. Empty routes waits 30 minutes. "
        "The remaining loaded first tour cannot change; each later tour requires depot return."
    )


_TOOLS = [
    ToolSpec(
        name="get_dispatch_state",
        description="Read released orders, physical fleet state, tours, cargo, locked movements, "
        "and complete currently observed traffic changes. No static map; no time advance.",
        kind="read",
        args_model=NoArgs,
    ),
    ToolSpec(
        name="get_city_map",
        description="Read static depot, nodes and roads once. Combine with current traffic in "
        "get_dispatch_state to estimate routes. Does not advance time.",
        kind="read",
        args_model=NoArgs,
    ),
    ToolSpec(
        name="submit_dispatch_plan",
        description="Atomically replace permitted future tours, advance 30 minutes, return "
        "operational state. Capacity applies per load. Loaded sequence and current edge are fixed.",
        kind="action",
        args_model=DispatchPlanArgs,
    ),
    ToolSpec(
        name="finish",
        description="Stop new assignments. Existing reserved tours and physical returns continue "
        "until minute 360. Return final operational state.",
        kind="end",
        args_model=NoArgs,
    ),
]
_TOOL_MAP = tool_map(_TOOLS)


class _OrderRecord(BaseModel):
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
    """Run online capacity allocation and multi-tour routing through four tools."""

    spec: ClassVar[TaskSpec] = TaskSpec(
        id="dispatch",
        version=2,
        max_steps=40,
        title="Capacity-constrained dynamic delivery dispatch",
        description="Plan capacity-limited depot tours under online demand, competing deadlines, "
        "economic values and changing traffic during a six-hour shift.",
        keywords=("dispatch", "vehicle-routing", "online-planning", "real-backend"),
        timeout_sec=900,
    )
    scoring_hash: ClassVar[str] = SCORING_HASH
    checks: ClassVar[TaskChecks] = TaskChecks(exploit_policies=("stop_early",))

    def __init__(self, *, regime: str | None = None) -> None:
        configured = DEFAULT_REGIME if regime is None else regime
        if configured not in REGIMES:
            raise ValueError("dispatch@2 regime must be A, B, C, or D")
        self._configured_regime = configured
        self._simulation: DispatchSimulation | None = None
        self._seed = 0
        self._salt: str | None = None
        self._regime = self._configured_regime
        self._actions: list[dict[str, list[list[str]]]] = []
        self._grade_cache: Score | None = None

    async def reset(
        self, seed: int, difficulty: int = 0, *, salt: str | None = None
    ) -> Observation:
        if difficulty not in range(len(REGIMES)):
            raise ValueError("dispatch@2 difficulty must be 0=A, 1=B, 2=C, or 3=D")
        if self._simulation is not None:
            await self._simulation.close()
        self._regime = REGIMES[difficulty] if difficulty else self._configured_regime
        self._simulation = await DispatchSimulation.create(seed, salt=salt, regime=self._regime)
        self._seed, self._salt = seed, salt
        self._actions = []
        self._grade_cache = None
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
        return ToolResult(ok=True, content=_JSON.validate_python(state))

    async def grade(self) -> Score:
        if self._grade_cache is not None:
            return self._grade_cache
        simulation = self._simulation
        if simulation is None:
            raise ValueError("Episode not started: call reset first")
        if self.done():
            final = _GradeState.model_validate(await simulation.state())
        else:
            replay = await DispatchSimulation.create(
                self._seed, salt=self._salt, regime=self._regime
            )
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
        self._grade_cache = DispatchScoreSpec(SCORING_CONFIG).compute(
            GradeInput(
                total_order_value=simulation.world.total_value,
                generated_orders=len(simulation.world.orders),
                deliveries=deliveries,
                distance_km=sum(v.distance for v in final.vehicles),
            )
        )
        return self._grade_cache

    def instruction(self) -> str:
        return (
            "Operate a capacity-limited fleet for 360 minutes. Read get_city_map once for "
            "static roads/depot, then get_dispatch_state for released orders, fleet, cargo, "
            "tours, locked edges and current traffic. Traffic state is complete and may be "
            "read again without advancing time; future arrivals and disruptions are unknown. "
            "submit_dispatch_plan(routes) maps each vehicle to a list of tours, each tour an "
            'ordered list of order IDs: {"v0":[["o001","o004"],["o012"]]}. '
            "Only currently released orders can be assigned. Omitted vehicles retain plans; "
            "{} waits. Successful submission advances 30 minutes and returns operational "
            "state, with no repeated static map. Invalid submissions do not advance time. "
            "There are 12 decision intervals and a total 40 tool calls, including reads "
            "and rejected calls. Parcel sizes within each tour must fit that vehicle's "
            "capacity; capacity resets after physical depot return and next pickup. "
            "Each tour starts with depot pickup and ends with a physical depot return, "
            "including the final tour. Loading/delivery service time is zero. "
            "The remaining first tour while tour_active is true is an immutable loaded "
            "sequence: retain it exactly. Onboard parcels cannot change vans or tours. "
            "Future unstarted tours may be replaced or removed; no order may appear twice "
            "across the fleet. A road edge already in progress and a return already begun "
            "cannot be canceled. Empty inner tours are invalid. finish stops new assignments; "
            "all currently reserved tours continue until shift end. Stopping or exhausting "
            "the call budget has the same continuation behavior. Reward is physically "
            "delivered value minus 0.35 per driven kilometre, including depot returns and "
            "partial edges, divided by total value generated over the shift and clipped "
            "to [0,1]. Delivery earns full value by deadline, then linearly declines to "
            "zero over its grace period. Unserved deliveries earn zero. Choose economically "
            "valuable combinations; serving every released order is not required."
        )
