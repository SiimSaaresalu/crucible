"""Geographic dispatch and stop-early baselines."""

from crucible.core.registry import register_policy
from crucible.envs.dispatch.policies.planning import Mode
from crucible.envs.dispatch.policies.reference import DispatchPolicy


@register_policy("nearest_first", "dispatch")
class NearestFirst(DispatchPolicy):
    name = "nearest_first"
    mode: Mode = "nearest"


@register_policy("stop_early", "dispatch")
class StopEarly(DispatchPolicy):
    name = "stop_early"
    stop_after_first = True
