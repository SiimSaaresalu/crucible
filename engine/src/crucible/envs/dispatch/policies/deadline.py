"""Earliest-deadline-first dispatch baseline."""

from crucible.core.registry import register_policy
from crucible.envs.dispatch.policies.planning import Mode
from crucible.envs.dispatch.policies.reference import DispatchPolicy


@register_policy("deadline_first", "dispatch")
class DeadlineFirst(DispatchPolicy):
    name = "deadline_first"
    mode: Mode = "deadline"
