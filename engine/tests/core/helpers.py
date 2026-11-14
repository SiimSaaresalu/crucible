"""Shared helpers for the core tests."""

from crucible.core.tools import ToolCall
from crucible.testing import ScriptedPolicy, play


def guess_call(n: int) -> ToolCall:
    return ToolCall(name="guess", args={"n": n})


__all__ = ["ScriptedPolicy", "guess_call", "play"]
