"""Observation-only policies. Importing this package registers every comparison policy."""

from crucible.envs.dispatch.policies import baselines, deadline, greedy, reference

__all__ = ["baselines", "deadline", "greedy", "reference"]
