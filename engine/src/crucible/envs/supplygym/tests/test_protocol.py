# pyright: basic
"""Protocol decisions depend only on serializable tool observations."""

import copy
import json
from pathlib import Path
from statistics import fmean

import pytest

from crucible.core.registry import get_policy, get_task
from crucible.core.tools import Observation, ToolCall, ToolResult, validate_call
from crucible.envs.supplygym.policies.protocol import Protocol
from crucible.envs.supplygym.tests.test_integrity import SEEDS
from crucible.testing import play


async def start():
    task = get_task("supplygym@1")
    obs = await task.reset(99)
    policy = Protocol()
    await policy.reset(task.spec, task.tools(), task.instruction())
    first = await policy.act(obs)
    assert first is not None and first.name == "get_briefing"
    briefing = await task.step(ToolCall(name="get_briefing"))
    second = await policy.act(Observation(last_result=briefing))
    assert second is not None and second.name == "get_status"
    status = await task.step(ToolCall(name="get_status"))
    return task, policy, status


@pytest.mark.asyncio
async def test_serialized_observations_ignore_hidden_and_score_fields():
    task, policy, status = await start()
    _, other, _ = await start()
    clean = json.loads(status.model_dump_json())
    dirty = copy.deepcopy(clean)
    dirty["content"].update(seed=12345, tape={"future_demand": [99999]}, profit_so_far=-1e9)
    a = await policy.act(Observation(last_result=ToolResult.model_validate(clean)))
    b = await other.act(Observation(last_result=ToolResult.model_validate(dirty)))
    assert a is not None and a == b
    assert not isinstance(validate_call({t.name: t for t in task.tools()}, a), ToolResult)
    assert clean == json.loads(status.model_dump_json())


@pytest.mark.asyncio
async def test_disruption_rumour_and_wind_down_rules():
    _, policy, result = await start()
    normal = await copy.deepcopy(policy).act(Observation(last_result=result))
    content = json.loads(json.dumps(result.content))
    content["suppliers"][0]["late_orders"] = 1
    disrupted = await copy.deepcopy(policy).act(
        Observation(last_result=ToolResult(ok=True, content=content))
    )
    assert disrupted is not None and normal is not None
    disrupted_args = json.loads(disrupted.model_dump_json())["args"]
    normal_args = json.loads(normal.model_dump_json())["args"]
    assert disrupted_args["supplier_weights"]["S01"] < normal_args["supplier_weights"]["S01"]
    for region in content["regions"].values():
        region["rumors_low"] = 1
    risk = await copy.deepcopy(policy).act(
        Observation(last_result=ToolResult(ok=True, content=content))
    )
    assert risk is not None
    assert (
        json.loads(risk.model_dump_json())["args"]["safety_stock_days"]
        >= normal_args["safety_stock_days"]
    )
    content["day"] = 364
    content["weeks_left"] = 1
    end = await policy.act(Observation(last_result=ToolResult(ok=True, content=content)))
    assert end is not None
    assert end.args["order_multiplier"] == policy.limits["order_multiplier"][0]
    assert end.args["safety_stock_days"] == 0


@pytest.mark.asyncio
async def test_four_week_demand_and_reset():
    task, policy, result = await start()
    content = json.loads(json.dumps(result.content))
    for week in range(1, 6):
        content["day"] = 7 * week
        content["last_week"] = {"revenue": 15 * 700 * week, "lost_sales": 10 * 7}
        await policy.act(Observation(last_result=ToolResult(ok=True, content=content)))
    assert list(policy.history) == [201, 301, 401, 501]
    await policy.reset(task.spec, task.tools(), task.instruction())
    assert not policy.history


@pytest.mark.asyncio
@pytest.mark.parametrize("task", ["supplygym@1", "supplygym-fresh@1"])
async def test_repeatable_complete_tool_rollouts(task: str, tmp_path: Path):
    traces = []
    for repeat in range(2):
        path = tmp_path / f"{repeat}.jsonl"
        result = await play(get_policy("protocol", task), 99, path, task)
        assert result.stop_reason == "task_done"
        assert result.steps == 55  # Two reads plus 53 weekly actions, within the 120-call budget.
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        steps = [r for r in rows if r["type"] == "step"]
        assert all(r["result"]["ok"] for r in steps)
        traces.append([(r["call"], r["result"]) for r in steps])
    assert traces[0] == traces[1]
    controls = [c["args"] for c, _ in traces[0] if c["name"] == "set_controls"]
    assert len({json.dumps(c, sort_keys=True) for c in controls}) > 1
    assert all(c["expedite_fraction"] == 0 for c in controls)


@pytest.mark.asyncio
@pytest.mark.parametrize("task", ["supplygym@1", "supplygym-fresh@1"])
async def test_protocol_against_existing_integrity_thresholds(task: str, tmp_path: Path):
    """Apply the gate's exact seed bank and reward alarms to the protocol too.

    Passing means no reward alarm on these seeds, not proof of economic quality.
    """
    results = [
        await play(get_policy("protocol", task), seed, tmp_path / f"{seed}.jsonl", task)
        for seed in SEEDS
    ]
    assert all(r.stop_reason == "task_done" and r.steps == 55 for r in results)
    assert fmean(r.score.breakdown["delta_vs_ss"] for r in results) < 300.0
    assert fmean(r.score.total for r in results) < 0.2
