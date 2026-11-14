"""Optimization-specific fixtures and repeated public-tool dispatch episodes."""

# Tests inspect diagnostics and simulator cleanup, never policy private inputs.
# pyright: reportPrivateUsage=false

import json
from math import isclose
from typing import Any

import pytest

from crucible.core.tools import Observation
from crucible.envs.dispatch.policies.v2.baselines import V2DispatchPolicy
from crucible.envs.dispatch.policies.v2.routing import Planner
from crucible.envs.dispatch.policies.v2.solver import RollingHorizonSolver, TourOptimizer
from crucible.envs.dispatch.policies.v2.views import EdgeView, MapView, StateView
from crucible.envs.dispatch.v2.task import DispatchTask


def fixture(
    orders: list[dict[str, Any]], *, capacity: int = 1, horizon: int = 60
) -> tuple[StateView, MapView]:
    city = MapView.model_validate(
        {
            "depot": "d",
            "nodes": [{"id": n} for n in ("d", "a", "b")],
            "roads": [
                {"a": "d", "b": "a", "distance": 1, "minutes": 5},
                {"a": "d", "b": "b", "distance": 1, "minutes": 5},
            ],
        }
    )
    state = StateView.model_validate(
        {
            "minute": 0,
            "horizon": horizon,
            "depot": "d",
            "orders": orders,
            "vehicles": [{"id": "v0", "capacity": capacity, "node": "d", "cargo": [], "tours": []}],
        }
    )
    return state, city


def order(oid: str, destination: str, *, value: int = 100, deadline: int = 50) -> dict[str, Any]:
    return {
        "id": oid,
        "destination": destination,
        "size": 1,
        "deadline": deadline,
        "value": value,
        "grace": 10,
        "status": "unassigned",
        "released_at": 0,
    }


def test_capacity_requires_real_sequential_reloads() -> None:
    state, city = fixture([order("o0", "a"), order("o1", "b")], capacity=1, horizon=20)
    routes, trace = TourOptimizer(state, city).solve()
    assert sorted(t[0] for t in routes["v0"]) == ["o0", "o1"]
    assert all(len(t) == 1 for t in routes["v0"])
    assert Planner(state, city).estimate(state.vehicles[0], routes["v0"]).finish == 20
    intervals = trace["cp_sat_selected_variables"]
    assert len(intervals) == 2
    assert intervals[0]["scheduled_finish"] <= intervals[1]["scheduled_start"]


def test_conflicting_windows_select_more_valuable_order() -> None:
    state, city = fixture(
        [
            order("cheap", "a", value=30, deadline=5),
            order("valuable", "b", value=250, deadline=5),
        ],
        horizon=10,
    )
    routes, trace = TourOptimizer(state, city).solve()
    assert routes == {"v0": [["valuable"]]}
    assert trace["orders_selected"] == 1
    assert "cheap" in trace["orders_postponed"]


def test_late_delivery_can_be_economically_worthwhile() -> None:
    state, city = fixture([order("late", "a", deadline=2)], horizon=15)
    routes, trace = TourOptimizer(state, city).solve()
    assert routes == {"v0": [["late"]]}
    assert trace["vehicles"]["v0"]["estimated_value"] == 70
    assert isclose(trace["estimated_total_utility"], 69.3)
    assert trace["cp_sat_selected_variables"][0]["lateness_minutes"] == [3]


def test_optional_order_with_negative_utility_is_not_served() -> None:
    state, city = fixture([order("bad", "a", value=1, deadline=0)], horizon=15)
    city.roads[0].distance = 100
    routes, trace = TourOptimizer(state, city).solve()
    assert routes == {"v0": []}
    assert trace["estimated_total_utility"] == 0


def test_loaded_suffix_and_edge_are_fixed_before_next_load() -> None:
    state, city = fixture([order("onboard", "a"), order("next", "b")], horizon=40)
    state.minute = 3
    state.orders[0].status = "onboard"
    vehicle = state.vehicles[0]
    vehicle.node = "d"
    vehicle.cargo = ["onboard"]
    vehicle.tours = [["onboard"]]
    vehicle.tour_active = True
    vehicle.edge = EdgeView(to_node="a", remaining_minutes=2, remaining_distance_km=0.4)
    routes, trace = TourOptimizer(state, city).solve()
    assert routes == {"v0": [["onboard"], ["next"]]}
    assert trace["cp_sat_selected_variables"][0]["scheduled_start"] >= 10
    assert trace["vehicles"]["v0"]["estimated_arrivals"]["onboard"] == 5


async def episode(seed: int, *, solver: bool) -> tuple[float, list[Any], list[dict[str, Any]]]:
    task = DispatchTask(regime="D")
    policy = RollingHorizonSolver() if solver else V2DispatchPolicy()
    observation = await task.reset(seed)
    await policy.reset(task.spec, task.tools(), task.instruction())
    actions: list[Any] = []
    try:
        while not task.done():
            call = await policy.act(observation)
            assert call is not None
            result = await task.step(call)
            assert result.ok, result.error
            actions.append(call.model_dump())
            observation = Observation(last_result=result)
        score = await task.grade()
        assert len(actions) == 14
        traces = policy.decision_traces if isinstance(policy, RollingHorizonSolver) else []
        return score.total, actions, traces
    finally:
        if task._simulation:
            await task._simulation.close()


async def test_repeated_online_solves_are_deterministic_and_valid() -> None:
    first = await episode(3000, solver=True)
    second = await episode(3000, solver=True)
    assert first == second
    assert len(first[2]) == 12
    for trace in first[2]:
        assert trace["estimated_total_utility"] + 1e-8 >= trace["incumbent_estimated_utility"]
        assert isclose(
            trace["estimated_total_utility"],
            sum(
                v["estimated_value"] - v["estimated_driving_cost"]
                for v in trace["vehicles"].values()
            ),
        )
        if trace["cp_sat_objective_scaled"] is not None:
            assert isclose(
                trace["cp_sat_objective_scaled"],
                sum(c["objective_component_scaled"] for c in trace["cp_sat_selected_variables"]),
            )
        json.dumps(trace, allow_nan=False)


@pytest.mark.parametrize("seed", [3000, 3001, 3002])
async def test_solver_development_episode_beats_or_matches_greedy(seed: int) -> None:
    reference, _, _ = await episode(seed, solver=True)
    greedy, _, _ = await episode(seed, solver=False)
    # These are development smoke fixtures, not held-out evidence or a global guarantee.
    assert reference >= greedy - 1e-9
