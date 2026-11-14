"""Fixed public-observation procurement protocol, projected onto weekly controls."""

from collections import deque
from statistics import fmean
from typing import Any

from crucible.core.task import TaskSpec
from crucible.core.tools import Observation, ToolCall, ToolSpec
from crucible.envs.supplygym.policies.baselines import _register
from crucible.envs.supplygym.policies.views import call, episode_over, parse_status

HISTORY_WEEKS = 4  # One month smooths ordinary week-to-week demand variation.
NORMAL_BUFFER_WEEKS = 1  # Cover one weekly review interval in ordinary conditions.
RISK_BUFFER_WEEKS = 2  # Add one further review interval when a route is at risk.
HIGH_RISK = 0.5  # The midpoint of the public 0-to-1 risk scale warrants caution.
DISRUPTED_FILL = 0.8  # A missing fifth of promised supply warrants rerouting.


@_register("protocol")
class Protocol:
    """One briefing, one status, then one set_controls per simulated week.

    No seed, simulator object, model weights, or random generator is accessible here.
    Tool-schema bounds are mechanical limits, not fitted policy parameters.
    """

    name = "protocol"

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None:
        self.phase = "briefing"
        self.history: deque[float] = deque(maxlen=HISTORY_WEEKS)
        self.last_day = 0
        schema = next(t for t in tools if t.name == "set_controls").json_schema()
        self.limits: dict[str, tuple[float, float]] = {}
        for name in ("supplier_weights", "safety_stock_days", "order_multiplier"):
            field = next(f for f in schema["properties"][name]["anyOf"] if f["type"] != "null")
            if name == "supplier_weights":
                field = field["additionalProperties"]
            self.limits[name] = (field["minimum"], field["maximum"])

    def bounded(self, name: str, value: float) -> float:
        lo, hi = self.limits[name]
        return min(hi, max(lo, value))

    async def act(self, obs: Observation) -> ToolCall | None:
        if self.phase == "briefing":
            self.phase = "read_briefing"
            return call("get_briefing")
        result = obs.last_result
        if result is None or not result.ok or episode_over(result):
            return None
        if self.phase == "read_briefing":
            assert isinstance(result.content, dict)
            self.briefing: dict[str, Any] = dict(result.content)
            self.phase = "control"
            return call("get_status")
        view = parse_status(result)
        interval = self.briefing["decision_interval_days"]
        nominal_demand = self.briefing["demand_mean_per_day"]
        elapsed = view.day - self.last_day
        if view.last_week is not None and elapsed > 0:
            ledger = view.last_week
            # Sales plus unmet demand recover realised demand using public economics.
            demand = float(ledger["revenue"]) / self.briefing["sell_price"]  # type: ignore[arg-type]
            demand += float(ledger["lost_sales"]) / self.briefing["lost_sale_penalty_per_unit"]  # type: ignore[arg-type]
            self.history.append(demand / elapsed)
        self.last_day = view.day
        demand_rate = fmean(self.history) if self.history else nominal_demand
        days_left = self.briefing["horizon_days"] - view.day
        active = [s for s in view.suppliers if s.state == "active"]
        disrupted = {s.id for s in active if s.late_orders > 0 or s.fill_ratio < DISRUPTED_FILL}
        usable = [s for s in active if s.id not in disrupted and s.lead_days <= days_left]
        weights = {s.id: 1.0 for s in view.suppliers}
        for s in active:
            if (s.id in disrupted and usable) or s.lead_days > days_left:
                weights[s.id] = self.limits["supplier_weights"][0]
        total = sum(s.capacity * weights[s.id] for s in active)
        target = 0.0
        for s in active:
            region = view.regions[s.region]
            at_risk = s.risk >= HIGH_RISK or region.rumors_low + region.rumors_medium > 0
            buffer = RISK_BUFFER_WEEKS if at_risk else NORMAL_BUFFER_WEEKS
            rate = nominal_demand if at_risk else demand_rate
            route_target = rate * (s.lead_days + buffer * interval)
            if s.lead_days > days_left:
                route_target = 0.0
            target += s.capacity * weights[s.id] / total * route_target
        safety = self.bounded(
            "safety_stock_days", (target - view.base_stock_level) / nominal_demand
        )
        # The API has no quantity field: approximate net order-up-to needs by scaling
        # the public reorder threshold gap; daily ordering still belongs to the simulator.
        net_need = max(0.0, target - view.inventory_position)
        available_gap = max(
            nominal_demand,
            view.base_stock_level + safety * nominal_demand - view.inventory_position,
        )
        multiplier = self.bounded("order_multiplier", net_need / available_gap)
        args: dict[str, Any] = {
            "supplier_weights": weights,
            "safety_stock_days": safety,
            "order_multiplier": multiplier,
            "expedite_fraction": 0.0,
        }
        # Existing healthy routes are backups; pay onboarding only if none remain usable.
        alternatives = [s for s in view.suppliers if s.state == "not_approved" and s.kind == "alt"]
        if not usable and disrupted and alternatives:
            candidate = alternatives[0]
            if days_left > self.briefing["alternative_onboarding_days"] + candidate.lead_days:
                args["approve_alternative"] = candidate.id
        return call("set_controls", **args)
