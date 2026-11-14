"""Greedy immediate economic-gain insertion baseline."""

from crucible.core.registry import register_policy
from crucible.envs.dispatch.policies.planning import Mode
from crucible.envs.dispatch.policies.reference import DispatchPolicy


@register_policy("greedy_insertion", "dispatch")
class GreedyInsertion(DispatchPolicy):
    name = "greedy_insertion"
    mode: Mode = "greedy"
