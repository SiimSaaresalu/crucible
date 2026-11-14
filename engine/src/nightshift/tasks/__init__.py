"""Built-in tasks. Importing a task package registers it."""

from nightshift.tasks import guess, incident

__all__ = ["guess", "incident"]
