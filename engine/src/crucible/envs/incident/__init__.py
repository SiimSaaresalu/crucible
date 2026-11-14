"""`incident@1`: diagnose and repair a simulated production incident.

Importing this package registers the task and its policies.
"""

from crucible.envs.incident import policies
from crucible.envs.incident.task import IncidentTask

__all__ = ["IncidentTask", "policies"]
