"""Independent economics, stopping semantics and real MCP isolation for v2."""

# Tests reconcile public outcomes with private authoritative records.
# pyright: reportPrivateUsage=false

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from crucible.agents.remote import McpEpisode, drive, fetch_grade
from crucible.core.jobs import Job
from crucible.core.registry import get_policy
from crucible.core.scoring import Score
from crucible.core.session import EpisodeSession
from crucible.core.tools import Observation, ToolCall
from crucible.core.transcript import TranscriptWriter
from crucible.envs.dispatch.tests.live import TOKEN, _live
from crucible.envs.dispatch.tests.test_world_v2 import fixed_world
from crucible.envs.dispatch.v2.task import DispatchTask
from crucible.envs.dispatch.world import Order, Road, World
from crucible.server.settings import Settings


def economics_world() -> World:
    return World(
        (
            Order("a", "n13", 1, 10, 100, 10, 0),
            Order("b", "n13", 1, 40, 100, 20, 0),
            Order("future", "n13", 1, 240, 300, 20, 120),
        ),
        (Road("n12", "n13", 2, 15),),
        (),
        (1,),
    )


async def close_task(task: DispatchTask) -> None:
    if task._simulation is not None:
        await task._simulation.close()


async def test_two_tours_grade_from_ledger_and_private_generated_denominator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "crucible.envs.dispatch.v2.world.make_world", fixed_world(economics_world())
    )
    task = DispatchTask()
    try:
        await task.reset(3000, salt="private-economic-fixture")
        sim = task._simulation
        assert sim is not None
        assert len((await sim.state())["orders"]) == 2  # future denominator remains private
        call = ToolCall(name="submit_dispatch_plan", args={"routes": {"v0": [["a"], ["b"]]}})
        assert (await task.step(call)).ok
        live, history_before_grade = await sim.state(), await sim.backend.history()
        stopped_grade = await task.grade()
        assert await sim.state() == live
        assert await sim.backend.history() == history_before_grade
        assert (await task.step(ToolCall(name="finish"))).ok
        final, history = await sim.state(), await sim.backend.history()
        score = await task.grade()
        assert score == stopped_grade
        deliveries = [event for event in history if event["type"] == "deliver"]
        assert [(event["order_id"], event["minute"]) for event in deliveries] == [
            ("a", 15),
            ("b", 45),
        ]
        assert len([event for event in history if event["type"] == "pickup"]) == 2
        assert len([event for event in history if event["type"] == "depot_return"]) == 2
        assert final["vehicles"][0]["cargo"] == []
        assert final["orders"][2]["status"] == "unassigned"
        distance = sum(event["distance_delta"] for event in history if event["type"] == "move")
        assert distance == pytest.approx(8)
        # a earns 50; b earns 75; the unassigned future order contributes only denominator.
        assert score.breakdown["realized_delivery_value"] == 125
        assert score.breakdown["total_potential_value"] == 500
        assert score.breakdown["driving_cost"] == pytest.approx(8 * 0.35)
        assert score.breakdown["total_utility"] == pytest.approx(122.2)
        assert score.total == pytest.approx(122.2 / 500)
    finally:
        await close_task(task)


async def test_final_return_is_charged_when_shift_ends_inside_road_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(
        (Order("a", "n13", 1, 250, 100, 20, 0),),
        (Road("n12", "n13", 10, 200),),
        (),
        (1,),
    )
    monkeypatch.setattr("crucible.envs.dispatch.v2.world.make_world", fixed_world(world))
    task = DispatchTask()
    try:
        await task.reset(3001)
        assert (
            await task.step(ToolCall(name="submit_dispatch_plan", args={"routes": {"v0": [["a"]]}}))
        ).ok
        assert (await task.step(ToolCall(name="finish"))).ok
        sim = task._simulation
        assert sim is not None
        final = await sim.state()
        vehicle = final["vehicles"][0]
        assert final["minute"] == 360
        assert final["orders"][0]["delivered_at"] == 200
        assert vehicle["returning"] is True
        assert vehicle["edge"]["to_node"] == "n12"
        assert vehicle["edge"]["remaining_minutes"] == 40
        assert vehicle["distance"] == pytest.approx(18, abs=1e-8)
        score = await task.grade()
        assert score.breakdown["driving_distance_km"] == pytest.approx(18, abs=1e-8)
        assert score.breakdown["total_utility"] == pytest.approx(93.7, abs=1e-8)
        assert score.total == pytest.approx(0.937, abs=1e-8)
    finally:
        await close_task(task)


async def test_budget_exhaustion_executes_reserved_tours_without_extra_penalty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "crucible.envs.dispatch.v2.world.make_world", fixed_world(economics_world())
    )
    task = DispatchTask()
    job = Job(task="dispatch@2", policy="budget-fixture", seed=3000)
    session = EpisodeSession(task, 3000, TranscriptWriter(tmp_path / "budget.jsonl"))
    try:
        await session.start(policy_name=job.policy, job=job)
        assert (
            await session.step(
                ToolCall(name="submit_dispatch_plan", args={"routes": {"v0": [["a"], ["b"]]}})
            )
        ).ok
        for _ in range(task.spec.max_steps - 1):
            assert (await session.step(ToolCall(name="get_dispatch_state"))).ok
        rejected = await session.step(ToolCall(name="finish"))
        assert not rejected.ok
        assert session.steps_used == 40
        reason, score = await session.close()
        assert reason == "budget_exhausted"
        assert score.flags["budget_exhausted"] is True
        assert score.breakdown["total_utility"] == pytest.approx(122.2)
        assert score.breakdown["other_operating_penalties"] == 0
        sim = task._simulation
        assert sim is not None and sim.minute == 30
    finally:
        await close_task(task)


@pytest.mark.parametrize("field", ["edge", "cargo", "delivered_at", "value", "node"])
async def test_public_plan_cannot_inject_authoritative_fields(field: str) -> None:
    task = DispatchTask()
    try:
        await task.reset(3000)
        sim = task._simulation
        assert sim is not None
        state, history = await sim.state(), await sim.backend.history()
        result = await task.step(
            ToolCall(name="submit_dispatch_plan", args={"routes": {}, field: "fabricated"})
        )
        assert not result.ok
        assert await sim.state() == state
        assert await sim.backend.history() == history
        assert task._actions == []
        fake = await task.step(ToolCall(name="simulator_event", args={"type": "deliver"}))
        assert not fake.ok
        assert await sim.backend.history() == history
    finally:
        await close_task(task)


async def local_reference(seed: int, salt: str, transcript: Path) -> tuple[Score, int]:
    task = DispatchTask()
    policy = get_policy("reference", "dispatch@2")
    job = Job(task="dispatch@2", policy="reference", seed=seed)
    session = EpisodeSession(task, seed, TranscriptWriter(transcript), salt=salt)
    try:
        obs = await session.start(policy_name="reference", job=job)
        await policy.reset(task.spec, task.tools(), task.instruction())
        while not task.done():
            call = await policy.act(obs)
            assert call is not None
            result = await session.step(call)
            assert result.ok, result.error
            obs = Observation(last_result=result)
        _, score = await session.close()
        return score, session.steps_used
    finally:
        await close_task(task)


async def test_v2_salted_reference_matches_real_mcp_and_grade_remains_private(
    tmp_path: Path,
) -> None:
    seed, salt = 3002, "private-v2-mcp-parity"
    local_score, local_steps = await local_reference(seed, salt, tmp_path / "local.jsonl")
    settings = Settings(
        mode="sidecar",
        grade_token=TOKEN,
        sidecar_task="dispatch@2",
        sidecar_seed=seed,
        sidecar_salt=salt,
        runs_dir=tmp_path / "remote",
        log_level="warning",
    )
    task = DispatchTask()
    async with _live(settings) as url:
        async with httpx.AsyncClient() as http:
            assert (await http.get(f"{url}/grade")).status_code == 403
            for path in ("/state", "/history", "/events", "/reset", "/orders", "/plans"):
                assert (await http.get(f"{url}{path}")).status_code == 404
        episode, instruction = await McpEpisode.connect(
            f"{url}/mcp", grade=lambda: fetch_grade(url, TOKEN)
        )
        outcome = await drive(
            get_policy("reference", "dispatch@2"), episode, task.spec, task.tools(), instruction
        )
        async with httpx.AsyncClient() as http:
            body: dict[str, Any] = (
                await http.get(f"{url}/grade", headers={"x-grade-token": TOKEN})
            ).json()
    assert outcome.score == local_score
    assert outcome.steps == local_steps == 14
    assert body["breakdown"] == local_score.breakdown
    assert body["reward"] == local_score.total
    public_steps = [record["result"] for record in body["transcript"] if record["type"] == "step"]
    public = json.dumps(public_steps)
    assert salt not in public
    assert "simulator_token" not in public
    assert "disruption_schedule" not in public
