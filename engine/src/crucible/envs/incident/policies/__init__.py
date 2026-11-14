"""Policies for `incident`. Importing a module registers its policies.

Policies here see tool results only: they never import the task's `task`, `world`, `sim`,
`state` or `grader` modules (an `ast` test enforces it). `views` parses tool results.
"""

from crucible.envs.incident.policies import baselines, reference, views

__all__ = ["baselines", "reference", "views"]
