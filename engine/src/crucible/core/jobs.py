"""Run specs and jobs. A job is one episode, identified by a stable hash."""

import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Job(BaseModel):
    """One episode to run."""

    model_config = ConfigDict(frozen=True)

    task: str
    policy: str
    seed: int
    repeat: int = 0

    @property
    def job_id(self) -> str:
        """First 16 hex chars of sha256 over the canonical JSON of the four fields."""
        canonical = json.dumps(
            {"task": self.task, "policy": self.policy, "seed": self.seed, "repeat": self.repeat},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class RunSpec(BaseModel):
    """A batch: one task and policy over many seeds, each repeated."""

    model_config = ConfigDict(frozen=True)

    task: str
    policy: str
    seeds: list[int]
    repeats: int = Field(default=1, ge=1)

    @field_validator("seeds")
    @classmethod
    def _unique_seeds(cls, seeds: list[int]) -> list[int]:
        if len(set(seeds)) != len(seeds):
            raise ValueError("seeds must be unique")
        return seeds


_RANGE = re.compile(r"(\d+)(?:-(\d+))?")


def parse_seeds(text: str) -> list[int]:
    """Parse `"0-9,12,20-21"`. Reject empty, reversed, negative or repeated seeds."""
    seeds: list[int] = []
    for part in text.split(","):
        part = part.strip()
        match = _RANGE.fullmatch(part)
        if match is None:
            raise ValueError(f"Bad seed range {part!r} in {text!r}: use e.g. '0-9,12'")
        start = int(match[1])
        end = int(match[2]) if match[2] is not None else start
        if end < start:
            raise ValueError(f"Reversed seed range {part!r}")
        seeds.extend(range(start, end + 1))
    if len(set(seeds)) != len(seeds):
        raise ValueError(f"Seeds repeat in {text!r}")
    return seeds


def expand(spec: RunSpec) -> list[Job]:
    """Jobs in stable order: by seed as listed, then by repeat."""
    return [
        Job(task=spec.task, policy=spec.policy, seed=seed, repeat=repeat)
        for seed in spec.seeds
        for repeat in range(spec.repeats)
    ]


__all__ = ["Job", "RunSpec", "expand", "parse_seeds"]
