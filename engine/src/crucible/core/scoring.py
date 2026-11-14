"""Score record, pluggable score calculators and scoring config loading."""

import hashlib
import json
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Score(BaseModel):
    """Result of grading one episode."""

    model_config = ConfigDict(frozen=True)

    total: float
    breakdown: dict[str, float] = Field(default_factory=dict)
    flags: dict[str, bool] = Field(default_factory=dict)


class ScoreSpec[E](Protocol):
    """A calculator that turns a task's episode record into a `Score`."""

    def compute(self, episode: E) -> Score: ...


def load_scoring_config[T: BaseModel](path: Path, model: type[T]) -> tuple[T, str]:
    """Load and validate YAML. The hash covers the validated model, not the file text."""
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in scoring config {path}: {exc}") from exc
    try:
        config = model.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid scoring config {path}:\n{exc}") from exc
    canonical = json.dumps(
        config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return config, hashlib.sha256(canonical.encode()).hexdigest()
