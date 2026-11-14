"""`incident@1` task: tools, clock and instruction text.

The world is hidden and kept in a private attribute. Every tool result is built from the public
view models in `state.py` (`model_dump(mode="json")`) plus the call's minute, never from the
world itself. A call that fails validation changes nothing: not the clock, not `steps`.
"""

from collections.abc import Sequence
from typing import ClassVar, get_args

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from nightshift.core.registry import register_task
from nightshift.core.scoring import Score
from nightshift.core.task import Task, TaskChecks, TaskSpec
from nightshift.core.tools import (
    Observation,
    ToolCall,
    ToolResult,
    ToolSpec,
    tool_map,
    validate_call,
)
from nightshift.tasks.incident.grader import SCORING_HASH, GradeInput, IncidentScoreSpec
from nightshift.tasks.incident.sim import (
    alerts_at,
    classify_repair,
    logs_between,
    metrics_at,
    next_alert_start,
    visible_deploys,
)
from nightshift.tasks.incident.state import (
    EpisodeState,
    Intervention,
    InterventionTool,
    ReadRecord,
    Topology,
)
from nightshift.tasks.incident.world import (
    CLEAN_DISK_MIN,
    FAULTS,
    HORIZON,
    LOG_LINES,
    MAX_INSTANCES,
    MAX_WAIT,
    READ_MIN,
    RESTART_MIN,
    ROLLBACK_MIN,
    SCALE_MIN,
    RootCause,
    Service,
    World,
    make_world,
)

__all__ = ["IncidentTask"]

MAX_STEPS = 40


class _Args(BaseModel):
    """Base of all argument models: unknown arguments are an error."""

    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    """Arguments of a tool that takes none."""


class ServiceArgs(_Args):
    """A tool that acts on one service."""

    service: Service = Field(description="Target service.")


class LogsArgs(_Args):
    """Arguments of `read_logs`."""

    service: Service = Field(description="Service whose logs to read.")
    since: int = Field(ge=0, description="First minute to include. Must not be in the future.")


class WaitArgs(_Args):
    """Arguments of `wait`."""

    minutes: int = Field(ge=1, le=MAX_WAIT, description="Minutes to wait, 1 to 30.")


class RollbackArgs(_Args):
    """Arguments of `rollback`."""

    deploy_id: str = Field(min_length=1, description="Id of a deploy from `list_deploys`.")


class ScaleArgs(_Args):
    """Arguments of `scale`."""

    service: Service = Field(description="Service to scale.")
    n: int = Field(ge=1, le=MAX_INSTANCES, description="Instance count, 1 to 5.")


class ResolveArgs(_Args):
    """Arguments of `resolve_incident`."""

    root_cause: RootCause = Field(description="Your diagnosis of the root cause.")


_TOOLS = [
    ToolSpec(
        name="get_topology",
        description="Service graph. Takes 1 minute.",
        kind="read",
        args_model=NoArgs,
    ),
    ToolSpec(
        name="get_alerts",
        description="Alerts firing now, with the minute each one started. Takes 1 minute.",
        kind="read",
        args_model=NoArgs,
    ),
    ToolSpec(
        name="get_metrics",
        description="Current error rate, p99 latency, CPU and disk use of one service. "
        "Takes 1 minute.",
        kind="read",
        args_model=ServiceArgs,
    ),
    ToolSpec(
        name="read_logs",
        description=f"Last {LOG_LINES} log lines of one service from minute `since` up to now. "
        "Takes 1 minute.",
        kind="read",
        args_model=LogsArgs,
    ),
    ToolSpec(
        name="list_deploys",
        description="Deploys so far: id, service and minute. Takes 1 minute.",
        kind="read",
        args_model=NoArgs,
    ),
    ToolSpec(
        name="wait",
        description=f"Let time pass for 1 to {MAX_WAIT} minutes. Stops early when a new alert "
        "fires.",
        kind="clock",
        args_model=WaitArgs,
    ),
    ToolSpec(
        name="restart",
        description=f"Restart a service. Takes {RESTART_MIN} minutes.",
        kind="action",
        args_model=ServiceArgs,
    ),
    ToolSpec(
        name="rollback",
        description=f"Roll back a deploy by id. Takes {ROLLBACK_MIN} minutes.",
        kind="action",
        args_model=RollbackArgs,
    ),
    ToolSpec(
        name="clean_disk",
        description=f"Free disk space on a service. Takes {CLEAN_DISK_MIN} minutes.",
        kind="action",
        args_model=ServiceArgs,
    ),
    ToolSpec(
        name="scale",
        description="Run n instances of a service. Eases load only; it does not repair. "
        f"Costs n-1 instance-minutes per minute. Takes {SCALE_MIN} minute.",
        kind="action",
        args_model=ScaleArgs,
    ),
    ToolSpec(
        name="resolve_incident",
        description="Close the incident with your diagnosis. Call it once; it ends the episode.",
        kind="end",
        args_model=ResolveArgs,
    ),
]
_TOOL_MAP = tool_map(_TOOLS)

_REPAIR_TOOLS = ("restart", "rollback", "clean_disk")


def _dump(model: BaseModel) -> dict[str, JsonValue]:
    return model.model_dump(mode="json")


def _dump_all(models: Sequence[BaseModel]) -> list[JsonValue]:
    return [m.model_dump(mode="json") for m in models]


def _fail(message: str) -> ToolResult:
    return ToolResult(ok=False, error=message)


@register_task
class IncidentTask(Task):
    """Diagnose and repair a simulated outage within 120 simulated minutes."""

    spec: ClassVar[TaskSpec] = TaskSpec(
        id="incident",
        version=1,
        max_steps=MAX_STEPS,
        title="Night Shift: on-call incident",
        description="On-call incident response: find the root cause of an outage and repair it.",
        keywords=("incident-response",),
    )
    scoring_hash: ClassVar[str] = SCORING_HASH
    checks: ClassVar[TaskChecks] = TaskChecks(exploit_policies=("stop_early", "shotgun"))

    def __init__(self) -> None:
        self._world: World | None = None
        self._state = EpisodeState()

    async def reset(
        self, seed: int, difficulty: int = 0, *, salt: str | None = None
    ) -> Observation:
        self._world = make_world(seed, salt)
        self._state = EpisodeState()
        return Observation(text=self.instruction(), data={"minute": 0})

    def tools(self) -> list[ToolSpec]:
        return list(_TOOLS)

    def hidden_strings(self) -> frozenset[str]:
        """Root-cause names and world field names: tools never say them unless the agent did."""
        return frozenset({*get_args(RootCause), "fault_kind", "fault_start", "herring", "is_bad"})

    def solved(self, score: Score) -> bool:
        return score.flags.get("probes_healthy", False) and score.flags.get(
            "root_cause_correct", False
        )

    def done(self) -> bool:
        return self._state.resolved is not None or self._state.minute >= HORIZON

    async def step(self, call: ToolCall) -> ToolResult:
        world = self._world
        if world is None:
            return _fail("Episode not started: call reset first")
        if self.done():
            return _fail("Episode is over")
        parsed = validate_call(_TOOL_MAP, call)
        if isinstance(parsed, ToolResult):
            return parsed
        state = self._state
        now = state.minute

        if isinstance(parsed, LogsArgs):
            if parsed.since > now:
                return _fail(f"read_logs: since={parsed.since} is in the future (now is {now})")
            return self._read(
                "read_logs",
                parsed.service,
                {
                    "service": parsed.service,
                    "lines": _dump_all(
                        logs_between(world, state.interventions, parsed.service, parsed.since, now)
                    ),
                },
            )
        if isinstance(parsed, RollbackArgs):
            known = [d.id for d in visible_deploys(world, now)]
            if parsed.deploy_id not in known:
                return _fail(
                    f"rollback: unknown deploy id {parsed.deploy_id!r}. "
                    f"Known deploy ids: {', '.join(known) or 'none'}"
                )
            return self._repair(
                "rollback", parsed.deploy_id, ROLLBACK_MIN, f"rolled back {parsed.deploy_id}"
            )
        if isinstance(parsed, ScaleArgs):
            state.steps += 1
            state.interventions.append(
                Intervention(
                    tool="scale",
                    target=parsed.service,
                    start=now,
                    duration=SCALE_MIN,
                    n=parsed.n,
                )
            )
            self._advance(SCALE_MIN)
            return self._ok(now, message=f"{parsed.service} now has {parsed.n} instances")
        if isinstance(parsed, WaitArgs):
            return self._wait(world, parsed.minutes)
        if isinstance(parsed, ResolveArgs):
            state.steps += 1
            state.resolved = parsed.root_cause
            return self._ok(now, message="incident closed")
        if isinstance(parsed, ServiceArgs):
            if call.name == "get_metrics":
                metrics = metrics_at(world, state.interventions, parsed.service, now)
                return self._read(
                    "get_metrics",
                    parsed.service,
                    {"service": parsed.service, **_dump(metrics)},
                )
            if call.name == "restart":
                return self._repair(
                    "restart", parsed.service, RESTART_MIN, f"restarted {parsed.service}"
                )
            return self._repair(
                "clean_disk", parsed.service, CLEAN_DISK_MIN, f"cleaned disk on {parsed.service}"
            )
        # No-argument reads.
        state.steps += 1
        extra: dict[str, JsonValue]
        if call.name == "get_topology":
            extra = _dump(Topology())
        elif call.name == "get_alerts":
            extra = {"alerts": _dump_all(alerts_at(world, state.interventions, now))}
        else:
            extra = {"deploys": _dump_all(visible_deploys(world, now))}
        self._advance(READ_MIN)
        return self._ok(now, **extra)

    def _ok(self, minute: int, **fields: JsonValue) -> ToolResult:
        return ToolResult(ok=True, content={"minute": minute, **fields})

    def _advance(self, minutes: int) -> None:
        self._state.minute = min(HORIZON, self._state.minute + minutes)

    def _read(self, tool: str, service: Service, fields: dict[str, JsonValue]) -> ToolResult:
        state = self._state
        now = state.minute
        state.steps += 1
        state.reads.append(ReadRecord(tool=tool, service=service, minute=now))
        self._advance(READ_MIN)
        return self._ok(now, **fields)

    def _repair(
        self, tool: InterventionTool, target: str, duration: int, message: str
    ) -> ToolResult:
        world = self._world
        assert world is not None and tool in _REPAIR_TOOLS
        state = self._state
        now = state.minute
        iv = Intervention(tool=tool, target=target, start=now, duration=duration)
        state.steps += 1
        state.interventions.append(iv)
        verdict = classify_repair(world, state.interventions, iv)
        if verdict == "wrong":
            state.wrong_repairs += 1
        elif verdict == "healthy_damage":
            state.healthy_damage_actions += 1
        if state.first_repair_correct is None:
            state.first_repair_correct = verdict == "correct"
            faulty = FAULTS[world.fault_kind].service
            state.evidence_before_first_repair = any(
                r.tool in ("get_metrics", "read_logs")
                and r.service == faulty
                and r.minute >= world.fault_start
                for r in state.reads
            )
        self._advance(duration)
        return self._ok(now, message=message)

    def _wait(self, world: World, minutes: int) -> ToolResult:
        state = self._state
        now = state.minute
        state.steps += 1
        until = min(now + minutes, HORIZON)
        nxt = next_alert_start(world, state.interventions, now, until)
        end = nxt if nxt is not None else until
        waited = end - now
        new_alerts = (
            [
                a
                for a in alerts_at(world, state.interventions, min(end, HORIZON - 1))
                if a.started_at > now
            ]
            if waited > 0
            else []
        )
        self._advance(waited)
        return self._ok(now, waited=waited, new_alerts=_dump_all(new_alerts))

    async def grade(self) -> Score:
        if self._world is None:
            raise ValueError("Episode not started: call reset first")
        return IncidentScoreSpec().compute(GradeInput(self._world, self._state))

    def instruction(self) -> str:
        edges = "api -> cache -> db (api calls cache; on a cache miss or cache down, api calls db)"
        return f"""You are the on-call engineer for a small web service. An incident may start at \
any time during the next {HORIZON} simulated minutes. Find the root cause, repair it, and close \
the incident.

Services: api, cache, db. Topology: {edges}.

Time: you start at minute 0 and the episode ends at minute {HORIZON}. Read tools take \
{READ_MIN} minute each. Actions take their stated time: restart {RESTART_MIN}, rollback \
{ROLLBACK_MIN}, clean_disk {CLEAN_DISK_MIN}, scale {SCALE_MIN}. `wait` takes 1 to {MAX_WAIT} \
minutes and stops early when a new alert fires. You have {MAX_STEPS} tool calls. Calls with \
invalid arguments are refused and take no simulated time, but still count toward the \
{MAX_STEPS} calls.

Tools:
- get_topology, get_alerts, list_deploys: read the service graph, firing alerts, deploy history.
- get_metrics(service): error rate, p99 latency, CPU and disk use of one service now.
- read_logs(service, since): the last {LOG_LINES} log lines from minute `since` up to now.
- wait(minutes): let time pass.
- restart(service), clean_disk(service), rollback(deploy_id): repair actions.
- scale(service, n): run n instances (1 to {MAX_INSTANCES}). It eases load but repairs nothing, \
and costs instance-minutes.
- resolve_incident(root_cause): close the incident with your diagnosis. Call it once; it ends \
the episode. Valid causes: bad_deploy, cache_crash, db_disk_full.

Runbook (the incident has exactly one cause; some alerts are unrelated noise):
- bad_deploy: a new api deploy has broken the checkout handler. Usual symptoms: api errors and \
slow responses soon after a deploy, with api error logs naming the build. Repair: rollback the \
deploy that was made just before the trouble started.
- cache_crash: the cache process died. Usual symptoms: the db is overloaded (high CPU, too many \
connections) and the api suffers; the cache logs show it exited. Repair: restart the cache.
- db_disk_full: the db disk is full and writes fail. Usual symptoms: db write errors and disk \
use at the limit, with db logs saying there is no space left. Repair: clean_disk on the db.

Scoring (0 to 1): you earn the share of requests served, compared with doing nothing, relative \
to an engineer who repairs the fault the moment it starts. You lose points for each wrong repair, \
for each repair made before any fault started (it breaks a healthy system), and for scaling \
(per instance-minute). Wrong repairs can leave side effects, such as downtime or an old bug \
coming back. You earn more if you read the metrics or logs of the faulty service before your \
first repair, if that first repair is correct, if the system is fully healthy at the end, and if \
your resolve_incident cause is right."""
