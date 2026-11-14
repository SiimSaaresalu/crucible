"""Parsers of `incident@1` tool results, run on real task results."""

import pytest
from pydantic import ValidationError

from crucible.core.registry import get_task
from crucible.core.tools import ToolResult
from crucible.envs.incident.policies.views import (
    AlertView,
    call,
    parse_alerts,
    parse_deploys,
    parse_logs,
    result_minute,
)
from crucible.envs.incident.task import IncidentTask

SEEDS = range(12)


async def _started(seed: int) -> IncidentTask:
    task = get_task("incident@1")
    assert isinstance(task, IncidentTask)
    await task.reset(seed)
    return task


def test_call_builds_tool_call() -> None:
    c = call("read_logs", service="api", since=3)
    assert c.name == "read_logs"
    assert c.args == {"service": "api", "since": 3}
    assert call("get_alerts").args == {}


async def test_parse_alerts_and_wait_on_real_results() -> None:
    saw_alert = saw_new = False
    for seed in SEEDS:
        task = await _started(seed)
        waited = await task.step(call("wait", minutes=30))
        new = parse_alerts(waited)
        assert result_minute(waited) == 0
        saw_new |= bool(new)
        res = await task.step(call("get_alerts"))
        alerts = parse_alerts(res)
        saw_alert |= bool(alerts)
        for a in alerts + new:
            assert a.name and a.service
            assert 0 <= a.started_at <= 120
    assert saw_alert and saw_new


async def test_parse_deploys_on_real_result() -> None:
    task = await _started(0)
    res = await task.step(call("list_deploys"))
    deploys = parse_deploys(res)
    assert deploys
    assert all(d.id and d.service for d in deploys)
    assert result_minute(res) == 0


async def test_parse_logs_on_real_result() -> None:
    seen = 0
    for seed in SEEDS:
        task = await _started(seed)
        await task.step(call("wait", minutes=30))
        res = await task.step(call("read_logs", service="api", since=0))
        lines = parse_logs(res)
        seen += len(lines)
        assert result_minute(res) >= 1
        assert all(line.level and line.msg for line in lines)
    assert seen > 0


async def test_result_minute_tracks_clock() -> None:
    task = await _started(0)
    first = await task.step(call("get_alerts"))
    second = await task.step(call("get_alerts"))
    assert (result_minute(first), result_minute(second)) == (0, 1)


async def test_failed_results_raise_value_error() -> None:
    task = await _started(0)
    bad = await task.step(call("read_logs", service="nope", since=0))
    assert not bad.ok
    for fn in (parse_alerts, parse_deploys, parse_logs, result_minute):
        with pytest.raises(ValueError, match="not ok"):
            fn(bad)


def test_wrong_shapes_raise_value_error() -> None:
    wrong_key = ToolResult(ok=True, content={"minute": 0, "lines": []})
    with pytest.raises(ValueError, match="alerts"):
        parse_alerts(wrong_key)
    with pytest.raises(ValueError, match="deploys"):
        parse_deploys(wrong_key)
    with pytest.raises(ValueError, match="Malformed alerts"):
        parse_alerts(ToolResult(ok=True, content={"alerts": [{"name": "x"}]}))
    with pytest.raises(ValueError, match="not an object"):
        parse_logs(ToolResult(ok=True, content=[1]))
    with pytest.raises(ValueError, match="integer minute"):
        result_minute(ToolResult(ok=True, content={"minute": "3"}))
    with pytest.raises(ValueError, match="integer minute"):
        result_minute(ToolResult(ok=True, content={"minute": True}))


def test_views_are_frozen() -> None:
    a = AlertView(name="n", service="api", started_at=1)
    with pytest.raises(ValidationError):
        a.name = "other"
