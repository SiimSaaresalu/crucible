"""Observation-only policies. Importing this package registers every comparison policy."""

from crucible.envs.dispatch.policies import baselines, deadline, greedy, reference
from crucible.envs.dispatch.policies.v2 import solver

__all__ = ["baselines", "deadline", "greedy", "reference", "solver"]
