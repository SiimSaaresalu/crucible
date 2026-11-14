"""Tests of `incident@1` as a task: tools, clock, validation, leaks, determinism."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import JsonValue

from nightshift.core.registry import get_policy, get_task
from nightshift.core.scoring import Score
from nightshift.core.tools import ToolCall, ToolResult
from nightshift.core.transcript import TIME_FIELDS, read_transcript
from nightshift.tasks.incident.sim import next_alert_start
from nightshift.tasks.incident.task import IncidentTask
from nightshift.tasks.incident.world import FAULTS, HORIZON, MAX_WAIT, RootCause, World, make_world
from tests.core.helpers import ScriptedPolicy, play

SERVICES = ["api", "cache", "db"]
CAUSES = ["bad_deploy", "cache_crash", "db_disk_full"]
DEPLOY_KEYS = {"id", "service", "minute"}
HIDDEN_WORDS = [*CAUSES, "fault_kind", "fault_start", "herring", "is_bad"]


def call(name: str, **args: JsonValue) -> ToolCall:
    return ToolCall(name=name, args=dict(args))


def rows(value: JsonValue) -> list[dict[str, JsonValue]]:
    """A JSON list of objects, checked."""
    assert isinstance(value, list)
    out: list[dict[str, JsonValue]] = []
    for item in value:
        assert isinstance(item, dict)
        out.append(item)
    return out


def num(value: JsonValue) -> int:
    assert isinstance(value, int)
    return value


class Driver:
    """Drives an `IncidentTask` directly and exposes its hidden world (white-box)."""

    def __init__(self, seed: int) -> None:
        self.task = IncidentTask()
        self.seed = seed
        asyncio.run(self.task.reset(seed))

    @property
    def world(self) -> World:
        world = self.task._world  # pyright: ignore[reportPrivateUsage]
        assert world is not None
        return world

    def dump(self) -> dict[str, object]:
        return self.task._state.model_dump()  # pyright: ignore[reportPrivateUsage]

    @property
    def minute(self) -> int:
        return self.task._state.minute  # pyright: ignore[reportPrivateUsage]

    def do(self, name: str, **args: JsonValue) -> ToolResult:
        return self.run_call(call(name, **args))

    def run_call(self, tool_call: ToolCall) -> ToolResult:
        return asyncio.run(self.task.step(tool_call))

    def content(self, name: str, **args: JsonValue) -> dict[str, JsonValue]:
        result = self.do(name, **args)
        assert result.ok, result.error
        assert isinstance(result.content, dict)
        return result.content

    def grade(self) -> Score:
        return asyncio.run(self.task.grade())

    def wait_until(self, minute: int) -> None:
        while self.minute < minute:
            self.content("wait", minutes=min(MAX_WAIT, minute - self.minute))


def first_seed(kind: str, start: int = 0) -> int:
    return next(s for s in range(start, start + 60) if make_world(s).fault_kind == kind)


# ---------------------------------------------------------------- registry and schema


def test_registry() -> None:
    task = get_task("incident")
    assert isinstance(task, IncidentTask)
    assert task.spec.ref == "incident@1"
    assert task.spec.max_steps == 40
    assert get_task("incident@1").scoring_hash == task.scoring_hash != "none"
    assert get_policy("do_nothing", "incident@1").name == "do_nothing"


def test_do_nothing_scores_zero(tmp_path: Path) -> None:
    policy = get_policy("do_nothing", "incident@1")
    result = asyncio.run(play(policy, 3, tmp_path / "t.jsonl", "incident@1"))
    assert result.score.total == 0.0
    assert result.stop_reason == "policy_stop"


def test_tool_schemas() -> None:
    schemas = {t.name: t.json_schema() for t in IncidentTask().tools()}
    assert set(schemas) == {
        "get_topology",
        "get_alerts",
        "get_metrics",
        "read_logs",
        "list_deploys",
        "wait",
        "restart",
        "rollback",
        "clean_disk",
        "scale",
        "resolve_incident",
    }
    for name in ("get_metrics", "restart", "clean_disk"):
        assert schemas[name]["properties"]["service"]["enum"] == SERVICES
    assert schemas["read_logs"]["properties"]["service"]["enum"] == SERVICES
    assert schemas["read_logs"]["properties"]["since"]["minimum"] == 0
    assert schemas["scale"]["properties"]["service"]["enum"] == SERVICES
    assert schemas["scale"]["properties"]["n"]["minimum"] == 1
    assert schemas["scale"]["properties"]["n"]["maximum"] == 5
    assert schemas["wait"]["properties"]["minutes"]["minimum"] == 1
    assert schemas["wait"]["properties"]["minutes"]["maximum"] == 30
    assert schemas["resolve_incident"]["properties"]["root_cause"]["enum"] == CAUSES


def test_reset_observation_and_instruction() -> None:
    task = IncidentTask()
    obs = asyncio.run(task.reset(0))
    assert obs.data == {"minute": 0}
    text = obs.text
    assert text == task.instruction()
    for word in ("120", "40", "api", "cache", "db", *CAUSES, "resolve_incident"):
        assert word in text
    # No alert thresholds.
    for word in ("0.05", "400", "0.90", "0.95", "threshold"):
        assert word not in text


def test_instruction_does_not_name_this_seed_fault() -> None:
    # The same text for every seed: it cannot depend on the hidden world.
    texts = {asyncio.run(_reset_text(s)) for s in range(10)}
    assert len(texts) == 1


async def _reset_text(seed: int) -> str:
    return (await IncidentTask().reset(seed)).text


# ---------------------------------------------------------------- clock


def test_read_takes_one_minute_and_reports_call_minute() -> None:
    d = Driver(0)
    assert d.content("get_alerts")["minute"] == 0
    assert d.minute == 1
    assert d.content("get_metrics", service="api")["minute"] == 1
    assert d.content("get_topology")["minute"] == 2
    assert d.content("list_deploys")["minute"] == 3
    assert d.content("read_logs", service="db", since=0)["minute"] == 4
    assert d.minute == 5


def test_action_durations() -> None:
    d = Driver(0)
    d.content("scale", service="api", n=2)
    assert d.minute == 1
    d.content("clean_disk", service="cache")
    assert d.minute == 3
    d.content("restart", service="api")
    assert d.minute == 6
    d2 = Driver(0)
    d2.content("rollback", deploy_id=d2.world.deploys[0].id)
    assert d2.minute == 5


def test_topology_content() -> None:
    content = Driver(0).content("get_topology")
    assert content["edges"] == [["api", "cache"], ["cache", "db"], ["api", "db"]]


def test_clock_never_passes_horizon() -> None:
    d = Driver(0)
    d.wait_until(HORIZON - 2)
    assert d.minute == HORIZON - 2
    result = d.content("restart", service="db")
    assert result["minute"] == HORIZON - 2
    assert d.minute == HORIZON
    assert d.task.done()
    assert not d.do("get_alerts").ok

    d = Driver(0)
    d.wait_until(HORIZON - 1)
    assert d.content("wait", minutes=30)["waited"] == 1
    assert d.minute == HORIZON

    d = Driver(0)
    d.wait_until(HORIZON - 1)
    d.content("rollback", deploy_id=d.world.deploys[0].id)
    assert d.minute == HORIZON


def test_wait_runs_the_full_amount_without_alert() -> None:
    d = Driver(0)
    until = 3
    assert next_alert_start(d.world, [], 0, until) is None
    content = d.content("wait", minutes=until)
    assert content["waited"] == until
    assert content["new_alerts"] == []
    assert d.minute == until


@pytest.mark.parametrize("seed", range(20))
def test_wait_stops_early_at_new_alert(seed: int) -> None:
    d = Driver(seed)
    first = next_alert_start(d.world, [], 0, MAX_WAIT)
    content = d.content("wait", minutes=MAX_WAIT)
    if first is None:
        assert content["waited"] == MAX_WAIT
        assert content["new_alerts"] == []
        return
    assert content["minute"] == 0
    assert content["waited"] == first
    assert d.minute == first
    alerts = content["new_alerts"]
    assert isinstance(alerts, list)
    assert alerts
    for alert in alerts:
        assert isinstance(alert, dict)
        assert alert["started_at"] == first


# ---------------------------------------------------------------- validation


def _future_deploy_id() -> tuple[int, str]:
    seed = first_seed("bad_deploy")
    world = make_world(seed)
    assert world.bad_deploy_id is not None
    return seed, world.bad_deploy_id


@pytest.mark.parametrize(
    ("name", "args", "message"),
    [
        ("nope", {}, "Unknown tool"),
        ("get_metrics", {"service": "web"}, "service"),
        ("get_metrics", {}, "service"),
        ("get_metrics", {"service": "api", "extra": 1}, "extra"),
        ("read_logs", {"service": "api", "since": -1}, "since"),
        ("read_logs", {"service": "api", "since": "x"}, "since"),
        ("read_logs", {"service": "api", "since": 5}, "future"),
        ("wait", {"minutes": 0}, "minutes"),
        ("wait", {"minutes": 31}, "minutes"),
        ("wait", {"minutes": 2.5}, "minutes"),
        ("scale", {"service": "api", "n": 0}, "n"),
        ("scale", {"service": "api", "n": 6}, "n"),
        ("restart", {"service": "web"}, "service"),
        ("clean_disk", {}, "service"),
        ("rollback", {"deploy_id": ""}, "deploy_id"),
        ("rollback", {"deploy_id": "d-zzzz"}, "unknown deploy id"),
        ("resolve_incident", {"root_cause": "gremlins"}, "root_cause"),
    ],
)
def test_invalid_calls_change_nothing(name: str, args: dict[str, JsonValue], message: str) -> None:
    d = Driver(0)
    d.content("get_alerts")
    before = d.dump()
    result = d.do(name, **args)
    assert not result.ok
    assert result.error is not None
    assert message in result.error
    assert d.dump() == before
    assert not d.task.done()


def test_rollback_of_future_deploy_is_refused_without_naming_it() -> None:
    seed, bad_id = _future_deploy_id()
    d = Driver(seed)
    before = d.dump()
    result = d.do("rollback", deploy_id=bad_id)
    assert not result.ok
    assert result.error is not None
    assert "unknown deploy id" in result.error
    assert bad_id not in result.error.split("Known deploy ids:")[1]
    assert d.dump() == before
    # Once visible it is accepted.
    d.wait_until(d.world.fault_start)
    assert d.do("rollback", deploy_id=bad_id).ok


def test_rollback_error_lists_only_visible_ids() -> None:
    seed, _ = _future_deploy_id()
    d = Driver(seed)
    error = d.do("rollback", deploy_id="d-zzzz").error
    assert error is not None
    visible = [x.id for x in d.world.deploys if x.minute <= 0]
    for deploy in d.world.deploys:
        assert (deploy.id in error) == (deploy.id in visible)


def test_calls_before_reset_fail() -> None:
    result = asyncio.run(IncidentTask().step(call("get_alerts")))
    assert not result.ok


# ---------------------------------------------------------------- end of episode


def test_resolve_ends_the_episode() -> None:
    d = Driver(0)
    d.content("get_alerts")
    content = d.content("resolve_incident", root_cause="db_disk_full")
    assert content == {"minute": 1, "message": "incident closed"}
    assert d.task.done()
    assert d.minute == 1
    steps = d.dump()["steps"]
    after: list[ToolCall] = [
        call("get_alerts"),
        call("wait", minutes=1),
        call("resolve_incident", root_cause="bad_deploy"),
    ]
    for tool_call in after:
        result = d.run_call(tool_call)
        assert not result.ok
        assert result.error == "Episode is over"
    assert d.dump()["steps"] == steps


def test_calls_after_horizon_fail() -> None:
    d = Driver(0)
    d.wait_until(HORIZON)
    assert d.task.done()
    assert not d.do("get_alerts").ok


def test_budget_exhausted(tmp_path: Path) -> None:
    calls = [call("get_alerts") for _ in range(45)]
    result = asyncio.run(play(ScriptedPolicy(calls), 0, tmp_path / "t.jsonl", "incident@1"))
    assert result.steps == 40
    assert result.stop_reason == "budget_exhausted"
    assert result.score.flags["budget_exhausted"]


def test_task_done_at_horizon(tmp_path: Path) -> None:
    calls = [call("wait", minutes=30) for _ in range(6)]
    result = asyncio.run(play(ScriptedPolicy(calls), 0, tmp_path / "t.jsonl", "incident@1"))
    assert result.stop_reason == "task_done"
    assert result.steps >= 4  # a wait may stop early at an alert


# ---------------------------------------------------------------- reads and classification


def test_logs_are_bounded_and_never_from_the_future() -> None:
    d = Driver(first_seed("bad_deploy"))
    d.wait_until(d.world.fault_start + 20)
    now = d.minute
    lines = d.content("read_logs", service="api", since=0)["lines"]
    assert isinstance(lines, list)
    assert 0 < len(lines) <= 20
    for line in lines:
        assert isinstance(line, dict)
        minute = line["minute"]
        assert isinstance(minute, int)
        assert minute < now
    later = rows(d.content("read_logs", service="api", since=d.minute - 3)["lines"])
    assert later
    assert all(num(x["minute"]) >= now - 3 for x in later)


def test_reads_record_service_and_minute() -> None:
    d = Driver(0)
    d.content("get_metrics", service="db")
    d.content("get_alerts")
    d.content("read_logs", service="cache", since=0)
    reads = d.task._state.reads  # pyright: ignore[reportPrivateUsage]
    assert [(r.tool, r.service, r.minute) for r in reads] == [
        ("get_metrics", "db", 0),
        ("read_logs", "cache", 2),
    ]


def _correct_call(world: World) -> ToolCall:
    info = FAULTS[world.fault_kind]
    if info.repair_tool == "rollback":
        assert world.bad_deploy_id is not None
        return call("rollback", deploy_id=world.bad_deploy_id)
    return call(info.repair_tool, service=info.service)


def test_correct_repair_with_evidence() -> None:
    d = Driver(first_seed("db_disk_full"))
    d.wait_until(d.world.fault_start + 3)
    d.content("get_metrics", service="db")
    d.run_call(_correct_call(d.world))
    state = d.task._state  # pyright: ignore[reportPrivateUsage]
    assert state.first_repair_correct is True
    assert state.evidence_before_first_repair is True
    assert (state.wrong_repairs, state.healthy_damage_actions) == (0, 0)
    # Repeating a correct repair after the fix is a wrong repair.
    d.wait_until(d.minute + 3)
    d.run_call(_correct_call(d.world))
    assert state.wrong_repairs == 1
    assert state.first_repair_correct is True


def test_repair_before_fault_is_healthy_damage() -> None:
    d = Driver(first_seed("cache_crash"))
    d.content("restart", service="db")
    state = d.task._state  # pyright: ignore[reportPrivateUsage]
    assert state.healthy_damage_actions == 1
    assert state.wrong_repairs == 0
    assert state.first_repair_correct is False
    assert state.evidence_before_first_repair is False


def test_wrong_repair_and_evidence_needs_faulty_service_after_start() -> None:
    d = Driver(first_seed("cache_crash"))
    d.wait_until(d.world.fault_start)
    d.content("get_metrics", service="api")  # wrong service
    d.content("restart", service="db")  # wrong repair
    state = d.task._state  # pyright: ignore[reportPrivateUsage]
    assert state.wrong_repairs == 1
    assert state.first_repair_correct is False
    assert state.evidence_before_first_repair is False
    # Evidence read after the first repair does not change the first-repair record.
    d.content("get_metrics", service="cache")
    d.do("restart", service="cache")
    assert state.first_repair_correct is False
    assert state.evidence_before_first_repair is False


def test_evidence_before_fault_start_does_not_count() -> None:
    d = Driver(first_seed("cache_crash"))
    d.content("get_metrics", service="cache")  # minute 0 < fault_start
    d.wait_until(d.world.fault_start)
    d.do("restart", service="cache")
    state = d.task._state  # pyright: ignore[reportPrivateUsage]
    assert state.first_repair_correct is True
    assert state.evidence_before_first_repair is False


def test_scale_is_recorded_not_classified() -> None:
    d = Driver(0)
    d.content("scale", service="db", n=3)
    state = d.task._state  # pyright: ignore[reportPrivateUsage]
    assert state.interventions[0].n == 3
    assert state.first_repair_correct is None
    assert state.wrong_repairs == 0


def test_grade_does_not_mutate_state() -> None:
    d = Driver(first_seed("bad_deploy"))
    d.content("get_alerts")
    d.content("resolve_incident", root_cause="bad_deploy")
    before = d.dump()
    first = d.grade()
    assert d.dump() == before
    assert d.grade() == first
    assert first.total == 0.0


def test_full_solution_scores_high() -> None:
    kinds: list[RootCause] = ["bad_deploy", "cache_crash", "db_disk_full"]
    for kind in kinds:
        d = Driver(first_seed(kind))
        d.wait_until(d.world.fault_start + 2)
        info = FAULTS[kind]
        d.content("get_metrics", service=info.service)
        if info.repair_tool == "rollback":
            d.content("list_deploys")
            args: dict[str, JsonValue] = {"deploy_id": d.world.bad_deploy_id}
        else:
            args = {"service": info.service}
        assert d.do(info.repair_tool, **args).ok
        d.wait_until(d.minute + 3)
        d.content("resolve_incident", root_cause=kind)
        score = d.grade()
        assert score.flags["root_cause_correct"]
        assert score.total > 0.5


# ---------------------------------------------------------------- hidden state


def _mixed_script(d: Driver) -> list[tuple[ToolCall, ToolResult]]:
    """Calls every tool, across the fault start, using only ids the agent could see."""
    out: list[tuple[ToolCall, ToolResult]] = []

    def go(name: str, **args: JsonValue) -> ToolResult:
        tool_call = call(name, **args)
        result = d.run_call(tool_call)
        out.append((tool_call, result))
        return result

    go("get_topology")
    go("get_alerts")
    go("list_deploys")
    for service in SERVICES:
        go("get_metrics", service=service)
        go("read_logs", service=service, since=0)
    for _ in range(4):
        go("wait", minutes=MAX_WAIT // 3)
        go("get_alerts")
        go("list_deploys")
        for service in SERVICES:
            go("get_metrics", service=service)
            go("read_logs", service=service, since=max(0, d.minute - 15))
    listed = go("list_deploys").content
    assert isinstance(listed, dict)
    deploys = listed["deploys"]
    assert isinstance(deploys, list) and deploys
    first = deploys[0]
    assert isinstance(first, dict)
    go("rollback", deploy_id=first["id"])
    go("scale", service="api", n=2)
    go("clean_disk", service="db")
    go("restart", service="cache")
    go("wait", minutes=MAX_WAIT)
    go("get_alerts")
    go("read_logs", service="api", since=0)
    return out


@pytest.mark.parametrize("seed", range(20))
def test_no_hidden_leak_in_results(seed: int) -> None:
    d = Driver(seed)
    world = d.world
    bad_id = world.bad_deploy_id
    seen_calls = 0
    for tool_call, result in _mixed_script(d):
        if tool_call.name == "resolve_incident" or not result.ok:
            continue
        seen_calls += 1
        text = json.dumps(result.content)
        for word in HIDDEN_WORDS:
            assert word not in text, (tool_call, word)
        assert isinstance(result.content, dict)
        minute = result.content["minute"]
        assert isinstance(minute, int)
        # Nothing the agent reads may name the bad deploy before it exists.
        if bad_id is not None and minute < world.fault_start:
            assert bad_id not in text, (tool_call, minute)
        # Log lines and alerts are never from the future.
        if tool_call.name == "read_logs":
            assert all(num(x["minute"]) < minute for x in rows(result.content["lines"]))
        if tool_call.name == "list_deploys":
            deploys = rows(result.content["deploys"])
            assert all(num(x["minute"]) <= minute for x in deploys)
            assert all(set(x) == DEPLOY_KEYS for x in deploys)
        if tool_call.name == "get_alerts":
            alerts = rows(result.content["alerts"])
            assert all(num(x["started_at"]) <= minute for x in alerts)
    assert seen_calls > 30


def test_tool_names_and_schemas_do_not_leak() -> None:
    task = IncidentTask()
    text = json.dumps([(t.name, t.description, t.json_schema()) for t in task.tools()])
    for word in ("fault_kind", "fault_start", "herring", "is_bad"):
        assert word not in text


# ---------------------------------------------------------------- determinism

_CALLS = [
    call("get_topology"),
    call("get_alerts"),
    call("list_deploys"),
    call("get_metrics", service="api"),
    call("get_metrics", service="cache"),
    call("get_metrics", service="db"),
    call("read_logs", service="api", since=0),
    call("read_logs", service="db", since=0),
    call("read_logs", service="cache", since=5),
    call("wait", minutes=1),
    call("wait", minutes=10),
    call("wait", minutes=30),
    call("restart", service="api"),
    call("restart", service="cache"),
    call("clean_disk", service="db"),
    call("scale", service="db", n=3),
    call("scale", service="api", n=5),
    call("rollback", deploy_id="d-0000"),
    call("resolve_incident", root_cause="cache_crash"),
    call("resolve_incident", root_cause="nope"),
    call("wait", minutes=0),
    call("get_metrics", service="web"),
    call("nope"),
    call("read_logs", service="api", since=500),
]


def _normalised(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = [json.loads(ln) for ln in path.read_text().splitlines()]
    for record in records:
        for field in TIME_FIELDS:
            record.pop(field, None)
    return records


@settings(max_examples=50, deadline=None)
@given(
    seed=st.integers(min_value=0, max_value=1000),
    calls=st.lists(st.sampled_from(_CALLS), max_size=15),
)
def test_same_input_same_transcript_and_score(seed: int, calls: list[ToolCall]) -> None:
    runs: list[tuple[list[dict[str, object]], Score]] = []
    for _ in range(2):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            result = asyncio.run(play(ScriptedPolicy(list(calls)), seed, path, "incident@1"))
            assert read_transcript(path).footer.score == result.score
            runs.append((_normalised(path), result.score))
    assert runs[0] == runs[1]
