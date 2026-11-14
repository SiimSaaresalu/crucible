"""`dispatch@1`. Importing this package registers the task and its policies."""

from crucible.envs.dispatch import policies
from crucible.envs.dispatch.task import DispatchTask

__all__ = ["DispatchTask", "policies"]
