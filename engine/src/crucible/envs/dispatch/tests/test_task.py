"""Public tools, private scoring and deterministic continuation integration checks."""

# These integrity tests deliberately inspect private state to prove that tools cannot change it.
# pyright: reportPrivateUsage=false

import json
from pathlib import Path

import pytest

from crucible.core.jobs import Job
from crucible.core.registry import get_task, list_tasks
from crucible.core.session import EpisodeSession
from crucible.core.tools import ToolCall
from crucible.core.transcript import TranscriptWriter
from crucible.envs.dispatch.task import DispatchTask


def test_registered_and_small_tool_contract() -> None:
    assert "dispatch@1" in list_tasks()
    task = get_task("dispatch@1")
    assert isinstance(task, DispatchTask)
    assert task.spec.max_steps == 40
    assert {tool.name for tool in task.tools()} == {
        "get_dispatch_state",
        "get_city_map",
        "submit_dispatch_plan",
        "finish",
    }


async def test_no_future_orders_or_private_data_are_observable() -> None:
    task = DispatchTask()
    try:
        await task.reset(7, salt="private-world-salt")
        simulation = task._simulation
        assert simulation is not None
        result = await task.step(ToolCall(name="get_dispatch_state"))
        assert result.ok
        state = await simulation.state()
        assert 8 <= len(state["orders"]) <= 12
        assert len(state["orders"]) < len(simulation.world.orders)
        assert all(order["released_at"] <= state["minute"] for order in state["orders"])
        serialized = json.dumps(result.content)
        assert "private-world-salt" not in serialized
        for private_field in task.hidden_strings():
            assert private_field not in serialized
    finally:
        if task._simulation is not None:
            await task._simulation.close()


@pytest.mark.parametrize(
    "call",
    [
        ToolCall(name="get_dispatch_state", args={"future": True}),
        ToolCall(name="submit_dispatch_plan", args={"routes": {"unknown": []}}),
        ToolCall(name="submit_dispatch_plan", args={"routes": {"v0": ["o999"]}}),
        ToolCall(name="submit_dispatch_plan", args={"routes": {"v0": ["o000", "o000"]}}),
        ToolCall(name="deliver", args={"order_id": "o000", "delivered_at": 0}),
        ToolCall(name="submit_dispatch_plan", args={"routes": {}, "delivered_at": 0}),
    ],
)
async def test_rejected_tools_leave_world_and_backend_unchanged(call: ToolCall) -> None:
    task = DispatchTask()
    try:
        await task.reset(0)
        simulation = task._simulation
        assert simulation is not None
        before = await simulation.state()
        ledger = await simulation.backend.history()
        result = await task.step(call)
        assert not result.ok
        assert await simulation.state() == before
        assert await simulation.backend.history() == ledger
        assert task._actions == []
    finally:
        if task._simulation is not None:
            await task._simulation.close()


async def test_grading_replays_existing_routes_without_mutating_live_episode() -> None:
    task = DispatchTask()
    try:
        await task.reset(0)
        simulation = task._simulation
        assert simulation is not None
        result = await task.step(
            ToolCall(name="submit_dispatch_plan", args={"routes": {"v0": ["o000"]}})
        )
        assert result.ok
        assert simulation.minute == 30
        before = await simulation.state()
        ledger = await simulation.backend.history()
        score = await task.grade()
        assert score.total > 0
        assert score.breakdown["deliveries"] == 1
        assert score == await task.grade()
        assert await simulation.state() == before
        assert await simulation.backend.history() == ledger
        assert not task.done()
        await task.step(ToolCall(name="finish"))
        assert task.done()
        assert await task.grade() == score
    finally:
        if task._simulation is not None:
            await task._simulation.close()


async def test_finish_without_assignments_is_zero_and_advances_to_horizon() -> None:
    task = DispatchTask()
    try:
        await task.reset(2)
        result = await task.step(ToolCall(name="finish"))
        assert result.ok
        assert task.done()
        assert (await task.grade()).total == 0
        rejected = await task.step(ToolCall(name="get_dispatch_state"))
        assert not rejected.ok
    finally:
        if task._simulation is not None:
            await task._simulation.close()


async def test_reset_isolates_orders_and_assignments() -> None:
    task = DispatchTask()
    try:
        await task.reset(0)
        await task.step(ToolCall(name="submit_dispatch_plan", args={"routes": {"v0": ["o000"]}}))
        await task.reset(0)
        assert task._simulation is not None
        state = await task._simulation.state()
        assert state["minute"] == 0
        assert all(not vehicle["route"] and not vehicle["cargo"] for vehicle in state["vehicles"])
        assert (await task.grade()).total == 0
    finally:
        if task._simulation is not None:
            await task._simulation.close()


async def test_session_enforces_40_call_budget(tmp_path: Path) -> None:
    task = DispatchTask()
    session = EpisodeSession(task, 0, TranscriptWriter(tmp_path / "budget.jsonl"))
    try:
        await session.start(policy_name="reads", job=Job(task="dispatch@1", policy="reads", seed=0))
        for _ in range(40):
            assert (await session.step(ToolCall(name="get_dispatch_state"))).ok
        result = await session.step(ToolCall(name="submit_dispatch_plan", args={"routes": {}}))
        assert not result.ok
        assert task._simulation is not None
        assert task._simulation.minute == 0
        reason, score = await session.close()
        assert reason == "budget_exhausted"
        assert score.total == 0
    finally:
        if task._simulation is not None:
            await task._simulation.close()
