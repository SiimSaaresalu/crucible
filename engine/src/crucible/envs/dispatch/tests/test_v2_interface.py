"""Public dispatch v2 contract, route estimates, and valid comparison episodes."""

# These tests inspect private records to validate isolation and physical scoring.
# pyright: reportPrivateUsage=false

import json
from typing import cast

import pytest
from pydantic import JsonValue

from crucible.core.tools import Observation, ToolCall
from crucible.envs.dispatch.policies.v2.baselines import V2DispatchPolicy
from crucible.envs.dispatch.policies.v2.routing import Mode, Planner, PublicRouting
from crucible.envs.dispatch.policies.v2.views import MapView, StateView
from crucible.envs.dispatch.v2.task import DispatchTask


async def test_public_reads_are_compact_and_reconstruct_travel() -> None:
    task = DispatchTask()
    try:
        await task.reset(3000, salt="private-v2-fixture")
        assert len(task.tools()) == 4
        result = await task.step(ToolCall(name="get_city_map"))
        city = MapView.model_validate(result.content)
        result = await task.step(ToolCall(name="get_dispatch_state"))
        state = StateView.model_validate(result.content)
        public = json.dumps(result.content)
        assert "city_map" not in public
        assert "roads" not in public
        assert "travel_times" not in public
        assert "private-v2-fixture" not in public
        assert all(o.released_at <= state.minute for o in state.orders)
        assert all(key not in public for key in task.hidden_strings())
        routing = PublicRouting(city, state)
        assert routing.leg(city.depot, city.depot) == (0, 0)
        assert all(routing.leg(city.depot, o.destination)[0] > 0 for o in state.orders)
    finally:
        if task._simulation is not None:
            await task._simulation.close()


@pytest.mark.parametrize(
    "routes",
    cast(
        list[JsonValue],
        [{"v0": ["o000"]}, {"v0": [[]]}, {"unknown": []}, {"v0": [["never-released"]]}],
    ),
)
async def test_invalid_plan_does_not_change_time_state_or_history(routes: JsonValue) -> None:
    task = DispatchTask()
    try:
        await task.reset(3000)
        sim = task._simulation
        assert sim is not None
        before, history = await sim.state(), await sim.backend.history()
        result = await task.step(ToolCall(name="submit_dispatch_plan", args={"routes": routes}))
        assert not result.ok
        assert await sim.state() == before
        assert await sim.backend.history() == history
        assert task._actions == []
    finally:
        if task._simulation is not None:
            await task._simulation.close()


@pytest.mark.parametrize("mode", ["nearest", "deadline", "greedy", "regret"])
async def test_baseline_full_episode_is_valid_and_compact(mode: Mode) -> None:
    task = DispatchTask()
    policy = V2DispatchPolicy(mode)
    try:
        obs = await task.reset(3000)
        await policy.reset(task.spec, task.tools(), task.instruction())
        characters = 0
        calls = 0
        while not task.done():
            call = await policy.act(obs)
            assert call is not None
            result = await task.step(call)
            assert result.ok, result.error
            characters += len(json.dumps(result.content))
            calls += 1
            obs = Observation(last_result=result)
        assert calls == 14
        # The frozen D workload has up to 55 orders. Even whitespace-expanded
        # JSON stays well below the v1 spike's roughly 340,000 characters.
        assert characters < 125_000
        score = await task.grade()
        assert score.breakdown["deliveries"] > 0
        assert score.breakdown["total_utility"] > 0
        assert score.total > 0
    finally:
        if task._simulation is not None:
            await task._simulation.close()


async def test_stop_early_grading_continues_only_reserved_tours() -> None:
    task = DispatchTask()
    try:
        await task.reset(3001)
        sim = task._simulation
        assert sim is not None
        city = MapView.model_validate(sim.city_map())
        state = StateView.model_validate(await sim.state())
        routes = Planner(state, city).build()
        result = await task.step(
            ToolCall(name="submit_dispatch_plan", args={"routes": cast(JsonValue, routes)})
        )
        assert result.ok
        before, history = await sim.state(), await sim.backend.history()
        score = await task.grade()
        assert await sim.state() == before
        assert await sim.backend.history() == history
        assert score.breakdown["deliveries"] > 0
        assert (await task.step(ToolCall(name="finish"))).ok
        assert await task.grade() == score
    finally:
        if task._simulation is not None:
            await task._simulation.close()


async def test_noop_is_exact_zero() -> None:
    task = DispatchTask()
    try:
        await task.reset(3000)
        assert (await task.step(ToolCall(name="finish"))).ok
        score = await task.grade()
        assert score.total == 0
        assert score.breakdown["total_utility"] == 0
        assert score.breakdown["driving_distance_km"] == 0
    finally:
        if task._simulation is not None:
            await task._simulation.close()


async def test_evaluation_regime_override_survives_default_reset() -> None:
    task = DispatchTask(regime="C")
    try:
        await task.reset(3000)
        assert task._regime == "C"
        await task.reset(3000, difficulty=1)
        assert task._regime == "B"
        await task.reset(3000)
        assert task._regime == "C"
    finally:
        if task._simulation is not None:
            await task._simulation.close()
