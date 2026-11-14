"""Scripted solvers for calibration. Test-only: they read the hidden world.

Each solver waits (in 10-minute steps) until a non-herring alert has fired, then plays a fixed
sequence. `wait` stops early at alerts, so the minute of the first fault alert is not known in
advance; the solver decides each call from the last result.
"""

import random
from collections import Counter

from pydantic import JsonValue

from crucible.core.task import TaskSpec
from crucible.core.tools import Observation, ToolCall, ToolSpec
from crucible.envs.incident.world import (
    FAULT_KINDS,
    FAULTS,
    HERRING_KINDS,
    SERVICES,
    RootCause,
    World,
)

__all__ = ["Informed", "ScaleOnly", "Shotgun", "StopEarly"]

WAIT_STEP = 10


def call(name: str, **args: JsonValue) -> ToolCall:
    return ToolCall(name=name, args=dict(args))


def _fault_alert_seen(obs: Observation) -> bool:
    """True if the last result is a `wait` that reported an alert other than the herring."""
    result = obs.last_result
    if result is None or not result.ok or not isinstance(result.content, dict):
        return False
    alerts = result.content.get("new_alerts")
    if not isinstance(alerts, list):
        return False
    return any(isinstance(a, dict) and a.get("name") not in HERRING_KINDS for a in alerts)


def _deploy_ids(obs: Observation) -> list[str]:
    """Deploy ids from a `list_deploys` result."""
    result = obs.last_result
    assert result is not None and isinstance(result.content, dict)
    deploys = result.content["deploys"]
    assert isinstance(deploys, list)
    ids: list[str] = []
    for d in deploys:
        assert isinstance(d, dict) and isinstance(d["id"], str)
        ids.append(d["id"])
    return ids


class _Solver:
    """Waits for the fault alert, then plays `_after_alert`, then stops."""

    name = "solver"

    def __init__(self, world: World) -> None:
        self.world = world
        self._queue: list[ToolCall] = []
        self._alerted = False

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None:
        self._queue = []
        self._alerted = False

    def _after_alert(self) -> list[ToolCall]:
        raise NotImplementedError

    def _on_result(self, obs: Observation) -> None:
        """Hook for solvers that extend the queue from a result."""

    async def act(self, obs: Observation) -> ToolCall | None:
        if not self._alerted:
            if obs.last_result is not None and obs.last_result.content is not None:
                if _fault_alert_seen(obs):
                    self._alerted = True
                    self._queue = self._after_alert()
            if not self._alerted:
                return call("wait", minutes=WAIT_STEP)
        else:
            self._on_result(obs)
        if not self._queue:
            return None
        return self._queue.pop(0)


class Informed(_Solver):
    """Knows the fault: reads evidence of the faulty service, repairs, checks, resolves."""

    name = "informed"

    def _after_alert(self) -> list[ToolCall]:
        w = self.world
        info = FAULTS[w.fault_kind]
        calls = [
            call("get_alerts"),
            call("get_metrics", service=info.service),
            call("read_logs", service=info.service, since=max(0, w.fault_start - 1)),
        ]
        if w.fault_kind == "bad_deploy":
            calls.append(call("list_deploys"))
            calls.append(call("rollback", deploy_id=w.bad_deploy_id))
        elif w.fault_kind == "cache_crash":
            calls.append(call("restart", service="cache"))
        else:
            calls.append(call("clean_disk", service="db"))
        calls += [
            call("wait", minutes=3),
            call("get_alerts"),
            call("resolve_incident", root_cause=w.fault_kind),
        ]
        return calls


class Shotgun(_Solver):
    """Tries every repair, then resolves with the most frequent cause the repairs imply."""

    name = "shotgun"

    def __init__(self, world: World) -> None:
        super().__init__(world)
        self._rolled = False

    def _after_alert(self) -> list[ToolCall]:
        self._rolled = False
        return [
            *(call("restart", service=s) for s in SERVICES),
            *(call("clean_disk", service=s) for s in SERVICES),
            call("list_deploys"),
        ]

    def _on_result(self, obs: Observation) -> None:
        if self._rolled or self._queue:
            return
        # The queue just drained its `list_deploys`; the last result carries the ids.
        ids = _deploy_ids(obs)
        implied: Counter[RootCause] = Counter()
        implied["cache_crash"] += 1  # restart cache
        implied["db_disk_full"] += 1  # clean_disk db
        implied["bad_deploy"] += len(ids)  # one rollback per deploy
        top = max(FAULT_KINDS, key=lambda k: (implied[k], -FAULT_KINDS.index(k)))
        self._queue = [
            *(call("rollback", deploy_id=i) for i in ids),
            call("resolve_incident", root_cause=top),
        ]
        self._rolled = True


class StopEarly(_Solver):
    """Two reads, then a random guess. Never waits for the fault."""

    name = "stop_early"

    def __init__(self, world: World, seed: int) -> None:
        super().__init__(world)
        self._rng = random.Random(seed)

    async def act(self, obs: Observation) -> ToolCall | None:
        if self._alerted:
            return None
        if not self._queue and obs.last_result is None:
            self._queue = [
                call("get_alerts"),
                call("get_topology"),
                call("resolve_incident", root_cause=self._rng.choice(FAULT_KINDS)),
            ]
        if not self._queue:
            return None
        nxt = self._queue.pop(0)
        if nxt.name == "resolve_incident":
            self._alerted = True
        return nxt

    def _after_alert(self) -> list[ToolCall]:
        return []


class ScaleOnly(_Solver):
    """Waits for the alert, scales every service to 5, then guesses the cause."""

    name = "scale_only"

    def __init__(self, world: World, seed: int) -> None:
        super().__init__(world)
        self._rng = random.Random(seed)

    def _after_alert(self) -> list[ToolCall]:
        return [
            *(call("scale", service=s, n=5) for s in SERVICES),
            call("resolve_incident", root_cause=self._rng.choice(FAULT_KINDS)),
        ]
