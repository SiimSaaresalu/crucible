"""Exploit baselines for `incident`. Tool results only, never task internals.

`StopEarly` guesses after two reads; `Shotgun` fires every repair without triage. Both exist to
show that neither habit beats honest play.
"""

import hashlib
import json
from collections import Counter

from crucible.core.registry import register_policy
from crucible.core.task import TaskSpec
from crucible.core.tools import Observation, ToolCall, ToolSpec
from crucible.envs.incident.policies.views import call, parse_alerts, parse_deploys

CAUSES = ["bad_deploy", "cache_crash", "db_disk_full"]
SERVICES = ["api", "cache", "db"]
WAIT_STEP = 30
BUDGET = 40


@register_policy("stop_early", "incident")
class StopEarly:
    """Reads alerts and topology, then guesses a cause from a hash of what it saw."""

    name = "stop_early"

    def __init__(self) -> None:
        self._step = 0
        self._seen: list[object] = []

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None:
        self._step = 0
        self._seen = []

    async def act(self, obs: Observation) -> ToolCall | None:
        if self._step > 0 and obs.last_result is not None:
            self._seen.append(obs.last_result.content)
        step = self._step
        self._step += 1
        if step == 0:
            return call("get_alerts")
        if step == 1:
            return call("get_topology")
        if step == 2:
            digest = hashlib.sha256(json.dumps(self._seen, sort_keys=True).encode()).digest()
            return call("resolve_incident", root_cause=CAUSES[int.from_bytes(digest) % 3])
        return None


@register_policy("shotgun", "incident")
class Shotgun:
    """Waits for any alert, tries every repair and rollback, resolves with the likeliest cause."""

    name = "shotgun"

    def __init__(self) -> None:
        self._init()

    def _init(self) -> None:
        self._queue: list[ToolCall] = []
        self._calls = 0
        self._alerted = False
        self._listed = False
        self._done = False

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None:
        self._init()

    def _emit(self, c: ToolCall) -> ToolCall | None:
        if self._calls >= BUDGET:
            self._done = True
            return None
        self._calls += 1
        if c.name == "resolve_incident":
            self._done = True
        return c

    async def act(self, obs: Observation) -> ToolCall | None:
        if self._done:
            return None
        result = obs.last_result
        if not self._alerted:
            if result is None or not result.ok or not parse_alerts(result):
                return self._emit(call("wait", minutes=WAIT_STEP))
            self._alerted = True
            self._queue = [
                *(call("restart", service=s) for s in SERVICES),
                *(call("clean_disk", service=s) for s in SERVICES),
                call("list_deploys"),
            ]
        elif self._listed:
            self._listed = False
            assert result is not None
            ids = [d.id for d in parse_deploys(result)]
            if self._calls + len(ids) + 1 > BUDGET:
                self._done = True
                return None
            implied: Counter[str] = Counter()
            implied["cache_crash"] += 1  # restart cache
            implied["db_disk_full"] += 1  # clean_disk db
            implied["bad_deploy"] += len(ids)  # one rollback per deploy
            top = max(CAUSES, key=lambda k: (implied[k], -CAUSES.index(k)))
            self._queue = [
                *(call("rollback", deploy_id=i) for i in ids),
                call("resolve_incident", root_cause=top),
            ]
        if not self._queue:
            self._done = True
            return None
        nxt = self._queue.pop(0)
        if nxt.name == "list_deploys":
            self._listed = True
        return self._emit(nxt)


__all__ = ["Shotgun", "StopEarly"]
