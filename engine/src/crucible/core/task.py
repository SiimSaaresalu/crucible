"""Task definition: the spec and the abstract environment."""

from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from crucible.core.scoring import Score
from crucible.core.tools import Observation, ToolCall, ToolResult, ToolSpec


class TaskSpec(BaseModel):
    """Static identity of a task, plus the metadata exports show (title, description, keywords)."""

    model_config = ConfigDict(frozen=True)

    id: str
    version: int
    max_steps: int
    title: str = Field(min_length=1, pattern=r"^[^\r\n]+$")
    description: str = Field(min_length=1)
    keywords: tuple[str, ...] = ()

    @field_validator("keywords")
    @classmethod
    def _keywords_not_blank(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not k.strip() for k in value):
            raise ValueError("keywords must not be blank")
        return value

    @property
    def ref(self) -> str:
        """Versioned reference, `"id@version"`."""
        return f"{self.id}@{self.version}"


class Task(ABC):
    """One environment. A fresh instance is made per episode."""

    spec: ClassVar[TaskSpec]
    scoring_hash: ClassVar[str] = "none"

    @abstractmethod
    async def reset(self, seed: int, difficulty: int = 0) -> Observation:
        """Start an episode and return the first observation."""

    @abstractmethod
    def tools(self) -> list[ToolSpec]:
        """Tools a policy may call."""

    @abstractmethod
    async def step(self, call: ToolCall) -> ToolResult:
        """Run one tool call."""

    @abstractmethod
    def done(self) -> bool:
        """True when the episode is over."""

    @abstractmethod
    async def grade(self) -> Score:
        """Score the finished episode."""

    @abstractmethod
    def instruction(self) -> str:
        """Instruction text shown to the policy."""


__all__ = ["Task", "TaskSpec"]
