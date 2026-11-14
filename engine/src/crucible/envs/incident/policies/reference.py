"""Runbook reference for `incident`. Uses only tool results, never task internals.

Watch for an alert, read logs upstream along the topology, repair once after a log signature,
confirm, resolve. An alert with no signature in the logs is noise and the policy keeps watching.
"""

import re
from enum import StrEnum

from crucible.core.registry import register_policy
from crucible.core.task import TaskSpec
from crucible.core.tools import Observation, ToolCall, ToolResult, ToolSpec
from crucible.envs.incident.policies.views import (
    AlertView,
    LogLineView,
    call,
    parse_alerts,
    parse_deploys,
    parse_logs,
)

WAIT_STEP = 30
LOOKBACK = 3
CONFIRM_WAIT = 5
CONFIRM_ROUNDS = 3
CALL_GUARD = 36
MAX_TRIAGE_READS = 4

_SUSPECTS = {"api": ["api"], "db": ["cache", "db"], "cache": ["cache"]}
_BUILD = re.compile(r"500 in checkout handler \(build (\S+)\)")


class _Phase(StrEnum):
    WATCH = "watch"
    TRIAGE = "triage"
    DEPLOYS = "deploys"
    CONFIRM_WAIT = "confirm_wait"
    CONFIRM_ALERTS = "confirm_alerts"
    AWAIT_ALERTS = "await_alerts"
    DONE = "done"


def _signature(service: str, lines: list[LogLineView]) -> tuple[str, str | None] | None:
    """(root cause, build id) if the lines carry a fault signature, else None."""
    for line in lines:
        if service == "api" and line.level == "ERROR":
            m = _BUILD.search(line.msg)
            if m:
                return "bad_deploy", m.group(1)
        elif service == "cache" and line.level == "FATAL" and "out of memory" in line.msg:
            return "cache_crash", None
        elif service == "db" and line.level == "ERROR" and "No space left on device" in line.msg:
            return "db_disk_full", None
    return None


@register_policy("reference", "incident")
class IncidentReference:
    """Wait, triage upstream, one repair, confirm, resolve."""

    name = "reference"

    def __init__(self) -> None:
        self._init()

    def _init(self) -> None:
        self._phase = _Phase.WATCH
        self._calls = 0
        self._triaged: set[tuple[str, int]] = set()
        self._noise: set[tuple[str, int]] = set()
        self._alert: AlertView | None = None
        self._suspects: list[str] = []
        self._reads = 0
        self._cause: str | None = None
        self._build: str | None = None
        self._first_error: int | None = None
        self._pending: str | None = None  # service of the read_logs just issued
        self._rounds = 0
        self._resolved = False

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None:
        self._init()

    def _emit(self, c: ToolCall) -> ToolCall:
        self._calls += 1
        return c

    def _read_next(self) -> ToolCall:
        assert self._alert is not None
        service = self._suspects[self._reads]
        self._reads += 1
        self._pending = service
        since = max(0, self._alert.started_at - LOOKBACK)
        self._phase = _Phase.TRIAGE
        return self._emit(call("read_logs", service=service, since=since))

    def _watch(self) -> ToolCall:
        self._phase = _Phase.WATCH
        self._alert = None
        return self._emit(call("wait", minutes=WAIT_STEP))

    def _resolve(self) -> ToolCall:
        self._phase = _Phase.DONE
        self._resolved = True
        return self._emit(call("resolve_incident", root_cause=self._cause or ""))

    def _repair(self) -> ToolCall:
        self._phase = _Phase.CONFIRM_WAIT
        if self._cause == "bad_deploy":
            return self._emit(call("rollback", deploy_id=self._build or ""))
        if self._cause == "cache_crash":
            return self._emit(call("restart", service="cache"))
        return self._emit(call("clean_disk", service="db"))

    async def act(self, obs: Observation) -> ToolCall | None:
        result = obs.last_result
        if self._resolved:
            return None
        if (
            self._cause is not None
            and self._calls >= CALL_GUARD
            and self._phase
            in (
                _Phase.TRIAGE,
                _Phase.DEPLOYS,
            )
        ):
            return self._resolve()
        phase = self._phase

        if phase is _Phase.WATCH:
            if result is not None and result.ok:
                fresh = [
                    a for a in parse_alerts(result) if (a.name, a.started_at) not in self._triaged
                ]
                if fresh:
                    self._alert = max(fresh, key=lambda a: a.started_at)
                    self._triaged.add((self._alert.name, self._alert.started_at))
                    self._suspects = _SUSPECTS.get(self._alert.service, [self._alert.service])
                    self._reads = 0
                    return self._read_next()
            return self._watch()

        if phase is _Phase.TRIAGE:
            assert result is not None and self._pending is not None
            found = _signature(self._pending, parse_logs(result))
            if found is not None:
                self._cause, self._build = found
                if self._cause == "bad_deploy" and self._build is not None:
                    build = self._build
                    self._first_error = min(
                        ln.minute for ln in parse_logs(result) if build in ln.msg
                    )
                    self._phase = _Phase.DEPLOYS
                    return self._emit(call("list_deploys"))
                return self._repair()
            if self._reads < min(len(self._suspects), MAX_TRIAGE_READS):
                return self._read_next()
            assert self._alert is not None
            self._noise.add((self._alert.name, self._alert.started_at))
            return self._watch()

        if phase is _Phase.DEPLOYS:
            assert result is not None
            deploys = parse_deploys(result)
            if self._build not in {d.id for d in deploys}:
                prior = [
                    d
                    for d in deploys
                    if d.service == "api"
                    and (self._first_error is None or d.minute <= self._first_error)
                ]
                if prior:
                    self._build = max(prior, key=lambda d: d.minute).id
            return self._repair()

        if phase is _Phase.CONFIRM_WAIT:
            self._phase = _Phase.CONFIRM_ALERTS
            return self._emit(call("wait", minutes=CONFIRM_WAIT))

        if phase is _Phase.CONFIRM_ALERTS:
            self._rounds += 1
            self._phase = _Phase.AWAIT_ALERTS
            return self._emit(call("get_alerts"))

        if phase is _Phase.AWAIT_ALERTS:
            return self._after_alerts(result)

        return None

    def _after_alerts(self, result: ToolResult | None) -> ToolCall:
        assert result is not None
        active = [a for a in parse_alerts(result) if (a.name, a.started_at) not in self._noise]
        if active and self._rounds < CONFIRM_ROUNDS and self._calls < CALL_GUARD:
            self._phase = _Phase.CONFIRM_ALERTS
            return self._emit(call("wait", minutes=CONFIRM_WAIT))
        return self._resolve()


__all__ = ["IncidentReference"]
