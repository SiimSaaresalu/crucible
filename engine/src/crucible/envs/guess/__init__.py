"""`guess@1`: throwaway number-guessing task used to exercise the core loop.

Importing this package registers the task and its policies.
"""

from crucible.envs.guess import policies
from crucible.envs.guess.task import GuessNumber

__all__ = ["GuessNumber", "policies"]
