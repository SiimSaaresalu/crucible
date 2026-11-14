"""Deterministic rolling-horizon CP-SAT dispatch optimization.

Reloads and immutable already-loaded prefixes are modeled as sequential optional
intervals on each physical vehicle. This avoids treating reload copies as vans
that can depart simultaneously. Candidate generation bounds route permutations;
CP-SAT globally selects and schedules those tours. The reference does not claim
to optimize routes outside its candidate set, or to predict unseen arrivals.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from math import ceil, floor, inf
from time import perf_counter
from typing import Any, cast

from ortools.sat.python import cp_model
from pydantic import JsonValue

from crucible.core.registry import register_policy
from crucible.core.task import TaskSpec
from crucible.core.tools import Observation, ToolCall, ToolSpec
from crucible.envs.dispatch.policies.v2.routing import Planner, Routes
from crucible.envs.dispatch.policies.v2.views import MapView, StateView


@dataclass(frozen=True)
class SolverConfig:
    """Inference work limits, independent of host wall-clock speed."""

    deterministic_time: float = 0.25
    seed: int = 0
    beam_width: int = 48
    candidate_limit: int = 320
    money_scale: int = 1000
    max_tour_orders: int = 8


@dataclass(frozen=True)
class Tour:
    orders: tuple[str, ...]
    duration: int
    distance: float
    arrivals: tuple[int, ...]


@dataclass
class Choice:
    vehicle_id: str
    tour: Tour
    selected: cp_model.IntVar
    start: cp_model.IntVar
    loss_vars: list[cp_model.IntVar]


class TourOptimizer:
    """Optimize released orders using public paths and fixed vehicle prefixes."""

    def __init__(self, state: StateView, city_map: MapView, config: SolverConfig | None = None):
        self.state = state
        self.city_map = city_map
        self.config = config or SolverConfig()
        self.planner = Planner(state, city_map)
        self.orders = self.planner.orders
        self.pending = tuple(
            sorted(
                oid for oid, o in self.orders.items() if o.status not in ("onboard", "delivered")
            )
        )
        self.prefixes = self.planner.initial_routes()
        self.availability = {
            vid: self.planner.estimate(v, self.prefixes[vid]).finish
            for vid, v in self.planner.vehicles.items()
        }
        self._tour_cache: dict[tuple[str, ...], Tour | None] = {}

    def tour(self, ids: tuple[str, ...]) -> Tour | None:
        if ids in self._tour_cache:
            return self._tour_cache[ids]
        node = self.city_map.depot
        minute, distance = self.state.load_service_minutes, 0.0
        arrivals: list[int] = []
        for oid in ids:
            order = self.orders[oid]
            duration, length = self.planner.routing.leg(node, order.destination)
            minute += duration + self.state.service_minutes
            distance += length
            if minute == inf:
                self._tour_cache[ids] = None
                return None
            arrivals.append(ceil(minute))
            node = order.destination
        duration, length = self.planner.routing.leg(node, self.city_map.depot)
        minute += duration
        distance += length
        if minute == inf:
            self._tour_cache[ids] = None
            return None
        result = Tour(ids, ceil(minute), distance, tuple(arrivals))
        self._tour_cache[ids] = result
        return result

    def _value(self, tour: Tour, start: float) -> float:
        return (
            sum(
                self.orders[oid].value
                * max(
                    0.0,
                    1.0
                    - max(0.0, start + offset - self.orders[oid].deadline) / self.orders[oid].grace,
                )
                for oid, offset in zip(tour.orders, tour.arrivals, strict=True)
            )
            - self.state.driving_cost_per_km * tour.distance
        )

    def candidates(self, incumbent: Routes) -> list[Tour]:
        """Beam permutations plus singleton coverage and the complete greedy incumbent."""
        vehicles = self.planner.vehicles
        max_capacity = max(v.capacity for v in vehicles.values())
        available = min(self.availability.values())
        tours: dict[tuple[str, ...], Tour] = {}
        required: set[tuple[str, ...]] = set()
        for vid, assigned in incumbent.items():
            for orders in assigned[int(vehicles[vid].tour_active) :]:
                required.add(tuple(orders))
        required.update((oid,) for oid in self.pending)
        beam: list[Tour] = []
        for ids in sorted(required):
            candidate = self.tour(ids)
            if candidate is not None:
                tours[ids] = candidate
                if len(ids) == 1:
                    beam.append(candidate)
        for _depth in range(1, self.config.max_tour_orders):
            expanded: dict[tuple[str, ...], Tour] = {}
            for current in beam:
                size = sum(self.orders[oid].size for oid in current.orders)
                for oid in self.pending:
                    if oid in current.orders or size + self.orders[oid].size > max_capacity:
                        continue
                    ids = (*current.orders, oid)
                    candidate = self.tour(ids)
                    if candidate is None or available + candidate.duration > self.state.horizon:
                        continue
                    if self._value(candidate, available) <= 0:
                        continue
                    expanded[ids] = candidate
            if not expanded:
                break
            # Mix utility density, absolute value and deadline slack; no random search.
            ordered = list(expanded.values())
            rankings = [
                sorted(
                    ordered,
                    key=lambda t: (-self._value(t, available) / (t.duration + 10), t.orders),
                ),
                sorted(ordered, key=lambda t: (-self._value(t, available), t.duration, t.orders)),
                sorted(
                    ordered,
                    key=lambda t: (
                        min(
                            self.orders[oid].deadline - offset
                            for oid, offset in zip(t.orders, t.arrivals, strict=True)
                        ),
                        -self._value(t, available),
                        t.orders,
                    ),
                ),
            ]
            diverse: dict[tuple[str, ...], Tour] = {}
            for index in range(self.config.beam_width):
                for ranking in rankings:
                    if index < len(ranking):
                        candidate = ranking[index]
                        diverse[candidate.orders] = candidate
                if len(diverse) >= self.config.beam_width:
                    break
            beam = list(diverse.values())[: self.config.beam_width]
            tours.update((t.orders, t) for t in beam)
        # Preserve a route for every available order, including cheap singleton jobs.
        optional = sorted(
            (t for ids, t in tours.items() if ids not in required),
            key=lambda t: (
                -self._value(t, available) / (t.duration + 10),
                -self._value(t, available),
                t.orders,
            ),
        )
        selected = [tours[ids] for ids in sorted(required) if ids in tours]
        selected.extend(optional[: max(0, self.config.candidate_limit - len(selected))])
        return sorted(selected, key=lambda t: t.orders)

    def utility(self, routes: Routes) -> float:
        return sum(
            self.planner.estimate(v, routes[vid]).utility
            for vid, v in self.planner.vehicles.items()
        )

    def solve(self) -> tuple[Routes, dict[str, Any]]:
        incumbent = self.planner.build("greedy")
        incumbent_utility = self.utility(incumbent)
        candidates = self.candidates(incumbent)
        model = cp_model.CpModel()
        choices: list[Choice] = []
        order_choices: dict[str, list[cp_model.IntVar]] = {oid: [] for oid in self.pending}
        objective: list[Any] = []
        scale = self.config.money_scale
        for vid, vehicle in sorted(self.planner.vehicles.items()):
            available = self.availability[vid]
            if available == inf or available >= self.state.horizon:
                continue
            earliest = ceil(available)
            intervals: list[cp_model.IntervalVar] = []
            hint_start = earliest
            hint_times: dict[tuple[str, ...], int] = {}
            for tour_ids in incumbent[vid][int(vehicle.tour_active) :]:
                tour = self.tour(tuple(tour_ids))
                if tour is not None:
                    hint_times[tour.orders] = hint_start
                    hint_start += tour.duration
            for candidate in candidates:
                if sum(self.orders[oid].size for oid in candidate.orders) > vehicle.capacity:
                    continue
                latest = min(
                    floor(self.state.horizon - candidate.duration),
                    *(
                        floor(self.orders[oid].deadline + self.orders[oid].grace - offset)
                        for oid, offset in zip(candidate.orders, candidate.arrivals, strict=True)
                    ),
                )
                if latest < earliest or self._value(candidate, earliest) <= 0:
                    continue
                index = len(choices)
                present = model.new_bool_var(f"take_{vid}_{index}")
                start = model.new_int_var(earliest, latest, f"start_{vid}_{index}")
                end = model.new_int_var(
                    earliest + candidate.duration, latest + candidate.duration, f"end_{vid}_{index}"
                )
                interval = model.new_optional_interval_var(
                    start, candidate.duration, end, present, f"tour_{vid}_{index}"
                )
                cast(Any, model.add(start == earliest)).only_enforce_if(present.Not())
                intervals.append(interval)
                loss_vars: list[cp_model.IntVar] = []
                potential = sum(self.orders[oid].value for oid in candidate.orders)
                cost = candidate.distance * self.state.driving_cost_per_km
                objective.append(round((potential - cost) * scale) * present)
                for oid, offset in zip(candidate.orders, candidate.arrivals, strict=True):
                    order = self.orders[oid]
                    late = model.new_int_var(0, ceil(order.grace), f"late_{index}_{oid}")
                    cast(
                        Any, model.add(late >= start + offset - floor(order.deadline))
                    ).only_enforce_if(present)
                    cast(Any, model.add(late == 0)).only_enforce_if(present.Not())
                    objective.append(-round(scale * order.value / order.grace) * late)
                    order_choices[oid].append(present)
                    loss_vars.append(late)
                choices.append(Choice(vid, candidate, present, start, loss_vars))
                hinted = candidate.orders in hint_times
                model.add_hint(present, int(hinted))
                model.add_hint(start, hint_times[candidate.orders] if hinted else earliest)
            model.add_no_overlap(intervals)
        for variables in order_choices.values():
            if variables:
                model.add_at_most_one(variables)
        model.maximize(sum(objective))
        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        solver.parameters.random_seed = self.config.seed
        solver.parameters.max_deterministic_time = self.config.deterministic_time
        solver.parameters.log_search_progress = False
        status = solver.solve(model)
        routes = {vid: [list(t) for t in tours] for vid, tours in self.prefixes.items()}
        selected_vars: list[dict[str, Any]] = []
        has_incumbent = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        if has_incumbent:
            selected = sorted(
                (c for c in choices if solver.boolean_value(c.selected)),
                key=lambda c: (c.vehicle_id, solver.value(c.start), c.tour.orders),
            )
            for choice in selected:
                routes[choice.vehicle_id].append(list(choice.tour.orders))
                losses = [solver.value(v) for v in choice.loss_vars]
                potential = sum(self.orders[oid].value for oid in choice.tour.orders)
                cost = choice.tour.distance * self.state.driving_cost_per_km
                scaled_loss = sum(
                    round(scale * self.orders[oid].value / self.orders[oid].grace) * late
                    for oid, late in zip(choice.tour.orders, losses, strict=True)
                )
                selected_vars.append(
                    {
                        "vehicle": choice.vehicle_id,
                        "orders": list(choice.tour.orders),
                        "scheduled_start": solver.value(choice.start),
                        "scheduled_finish": solver.value(choice.start) + choice.tour.duration,
                        "lateness_minutes": losses,
                        "potential_value": potential,
                        "driving_cost": cost,
                        "lateness_loss_scaled": scaled_loss,
                        "objective_component_scaled": round((potential - cost) * scale)
                        - scaled_loss,
                    }
                )
        candidate_utility = self.utility(routes) if has_incumbent else -inf
        fallback = not has_incumbent or candidate_utility + 1e-8 < incumbent_utility
        if fallback:
            routes = incumbent
        estimates = {
            vid: self.planner.estimate(v, routes[vid]) for vid, v in self.planner.vehicles.items()
        }
        selected_orders = {oid for tours in routes.values() for tour in tours for oid in tour}
        trace: dict[str, Any] = {
            "round": int(self.state.minute / self.state.decision_interval) + 1,
            "minute": self.state.minute,
            "public_input_sha256": hashlib.sha256(
                json.dumps(
                    {"state": self.state.model_dump(), "map": self.city_map.model_dump()},
                    sort_keys=True,
                ).encode()
            ).hexdigest(),
            "solver": "ortools-cp-sat-9.15.6755",
            "configuration": asdict(self.config),
            "status": solver.status_name(status),
            "deterministic_work": solver.response_proto.deterministic_time,
            "deterministic_time": solver.response_proto.deterministic_time,
            "candidate_tours": len(candidates),
            "candidate_vehicle_intervals": len(choices),
            "orders_considered": len(self.pending),
            "orders_selected": len(selected_orders.intersection(self.pending)),
            "cp_sat_objective_scaled": solver.objective_value if has_incumbent else None,
            "cp_sat_best_bound_scaled": solver.best_objective_bound if has_incumbent else None,
            "cp_sat_selected_variables": selected_vars,
            "schedule_interpretation": (
                "CP-SAT times certify feasible nonoverlapping tours. Emitted tours execute "
                "consecutively without optional waiting; earlier delivery cannot reduce value."
            ),
            "incumbent_estimated_utility": incumbent_utility,
            "candidate_estimated_utility": candidate_utility if candidate_utility != -inf else None,
            "fallback": fallback,
            "fallback_used": fallback,
            "fallback_reason": "no CP-SAT incumbent"
            if not has_incumbent
            else "greedy incumbent has higher exact estimated utility"
            if fallback
            else None,
            "emitted_plan": routes,
            "vehicles": {
                vid: {
                    "tours": routes[vid],
                    "estimated_value": e.value,
                    "estimated_driving_cost": e.distance * self.state.driving_cost_per_km,
                    "estimated_utility": e.utility,
                    "estimated_finish": e.finish,
                    "estimated_arrivals": e.arrivals,
                }
                for vid, e in estimates.items()
            },
            "orders_postponed": {
                oid: "not selected by optimization" if not fallback else "not selected by incumbent"
                for oid in self.pending
                if oid not in selected_orders
            },
            "estimated_total_utility": sum(e.utility for e in estimates.values()),
            "limitations": (
                "bounded candidate tours; current traffic held fixed; unseen arrivals excluded; "
                "integer-minute conservative rounding; 0.001-dollar coefficient rounding"
            ),
        }
        return routes, trace


@register_policy("rolling_horizon_solver", "dispatch")
class RollingHorizonSolver:
    """Public MCP policy; no simulator, private world, or grade access."""

    name = "rolling_horizon_solver"

    def __init__(self, config: SolverConfig | None = None) -> None:
        self.config = config or SolverConfig()
        self._map: MapView | None = None
        self._phase = "map"
        self.decision_traces: list[dict[str, Any]] = []
        self.solve_runtime_seconds: list[float] = []

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None:
        if task_spec.id != "dispatch" or task_spec.version != 2:
            raise ValueError("rolling_horizon_solver requires dispatch@2")
        self._map, self._phase = None, "map"
        self.decision_traces = []
        self.solve_runtime_seconds = []

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
        state = StateView.model_validate(result.content)
        if state.minute >= state.horizon:
            return None
        if self._map is None:
            raise RuntimeError("Static map required before optimization")
        started = perf_counter()
        routes, trace = TourOptimizer(state, self._map, self.config).solve()
        self.solve_runtime_seconds.append(perf_counter() - started)
        self.decision_traces.append(trace)
        return ToolCall(name="submit_dispatch_plan", args={"routes": cast(JsonValue, routes)})
