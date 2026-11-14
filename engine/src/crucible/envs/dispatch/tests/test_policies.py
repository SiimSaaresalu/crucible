"""Physical route estimates and observation-only policies against the real environment."""

import pytest

from crucible.core.registry import get_policy
from crucible.core.tools import Observation
from crucible.envs.dispatch.policies.planning import Planner
from crucible.envs.dispatch.policies.views import OrderView, StateView, VehicleView
from crucible.envs.dispatch.task import DispatchTask


def state_view() -> StateView:
    matrix = {
        "d": {"d": 0.0, "a": 5.0, "b": 10.0},
        "a": {"d": 5.0, "a": 0.0, "b": 5.0},
        "b": {"d": 10.0, "a": 5.0, "b": 0.0},
    }
    return StateView.model_validate(
        {
            "minute": 10,
            "horizon": 100,
            "driving_cost_per_km": 1,
            "orders": [
                {
                    "id": "c",
                    "destination": "a",
                    "size": 1,
                    "deadline": 100,
                    "value": 30,
                    "grace": 20,
                    "status": "onboard",
                },
                {
                    "id": "u",
                    "destination": "b",
                    "size": 1,
                    "deadline": 100,
                    "value": 30,
                    "grace": 20,
                    "status": "unassigned",
                },
            ],
            "vehicles": [
                {
                    "id": "v",
                    "capacity": 2,
                    "node": "a",
                    "cargo": ["c"],
                    "route": ["c"],
                    "edge": {"to_node": "b", "remaining_minutes": 2, "remaining_distance_km": 2},
                }
            ],
            "city_map": {"depot": "d", "travel_times": matrix, "distances": matrix},
        }
    )


def test_estimate_preserves_edge_and_visits_depot_for_new_parcel() -> None:
    state = state_view()
    planner = Planner(state)
    van = state.vehicles[0]
    existing = planner.estimate(van, ["c"])
    proposed = planner.estimate(van, ["c", "u"])
    assert existing.finish == 17
    assert existing.distance == 7
    assert proposed.finish == 32  # 2 edge + 10 depot + 5 customer + 5 customer.
    assert proposed.distance == 22
    assert proposed.utility == 38


def test_capacity_cargo_and_unreachable_destinations_are_infeasible() -> None:
    state = state_view()
    planner = Planner(state)
    van = state.vehicles[0]
    assert planner.estimate(van, ["u"]).utility == -float("inf")
    planner.orders["u"].size = 2
    assert planner.insertions(planner.orders["u"], {"v": ["c"]}) == []
    state.city_map.travel_times["b"].pop("a")
    assert planner.estimate(van, ["c"]).utility == -float("inf")


def test_regret_preserves_scarce_capacity_before_easy_order() -> None:
    state = state_view()
    state.minute = 0
    state.orders = [
        OrderView(
            id="big", destination="a", size=2, deadline=100, value=50, grace=20, status="unassigned"
        ),
        OrderView(
            id="small",
            destination="a",
            size=1,
            deadline=100,
            value=60,
            grace=20,
            status="unassigned",
        ),
    ]
    state.vehicles = [
        VehicleView(id="v0", capacity=2, node="d", cargo=[], route=[]),
        VehicleView(id="v1", capacity=1, node="d", cargo=[], route=[]),
    ]
    planner = Planner(state)
    assert planner.build("greedy") == {"v0": ["small"], "v1": []}
    assert planner.build("regret") == {"v0": ["big"], "v1": ["small"]}


def test_local_reordering_improves_economic_objective() -> None:
    state = state_view()
    state.vehicles[0].node = "d"
    state.vehicles[0].edge = None
    state.vehicles[0].cargo = ["c", "u"]
    planner = Planner(state)
    routes = {"v": ["u", "c"]}
    before = planner.estimate(state.vehicles[0], routes["v"]).utility
    planner.improve(routes)
    assert routes == {"v": ["c", "u"]}
    assert planner.estimate(state.vehicles[0], routes["v"]).utility > before


async def rollout(name: str, seed: int) -> tuple[list[dict[str, object]], float]:
    task = DispatchTask()
    policy = get_policy(name, task.spec.ref)
    obs = await task.reset(seed)
    await policy.reset(task.spec, task.tools(), task.instruction())
    calls: list[dict[str, object]] = []
    try:
        for _ in range(task.spec.max_steps):
            if task.done():
                break
            call = await policy.act(obs)
            assert call is not None
            calls.append(call.model_dump())
            result = await task.step(call)
            assert result.ok, (name, seed, call, result.error)
            obs = Observation(last_result=result)
        assert task.done()
        score = await task.grade()
        return calls, score.total
    finally:
        # Resource cleanup only; the policy itself never receives the task.
        simulation = task._simulation  # pyright: ignore[reportPrivateUsage]
        assert simulation is not None
        await simulation.close()


@pytest.mark.parametrize(
    "name",
    [
        "reference",
        "regret_insertion",
        "greedy_insertion",
        "nearest_first",
        "deadline_first",
        "stop_early",
    ],
)
async def test_policies_execute_valid_full_episodes(name: str) -> None:
    calls, score = await rollout(name, 2)
    assert 0 < score < 1
    assert len(calls) == (3 if name == "stop_early" else 13)


async def test_reference_alias_and_replay_are_identical() -> None:
    first = await rollout("reference", 3)
    second = await rollout("reference", 3)
    alias = await rollout("regret_insertion", 3)
    assert first == second == alias
