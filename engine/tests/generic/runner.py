"""Run one episode for the generic checks and keep what they need to look at."""

import json
import random
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue

from nightshift.core.episode import EpisodeResult, run_episode
from nightshift.core.jobs import Job
from nightshift.core.policy import Policy
from nightshift.core.registry import get_task
from nightshift.core.task import TaskSpec
from nightshift.core.tools import Observation, ToolCall, ToolSpec
from nightshift.core.transcript import TIME_FIELDS, TranscriptWriter, read_transcript


class Episode(BaseModel):
    """A finished episode: its result, its transcript records and the task's hidden strings."""

    model_config = ConfigDict(frozen=True)

    result: EpisodeResult
    records: list[dict[str, Any]]
    hidden: frozenset[str]
    solved: bool


async def run(task_ref: str, policy: Policy, seed: int, path: Path) -> Episode:
    task = get_task(task_ref)
    job = Job(task=task_ref, policy=policy.name, seed=seed)
    result = await run_episode(task, policy, seed, job=job, writer=TranscriptWriter(path))
    transcript = read_transcript(path)
    records = [
        r.model_dump(mode="json") for r in (transcript.header, *transcript.steps, transcript.footer)
    ]
    return Episode(
        result=result,
        records=records,
        hidden=task.hidden_strings(),
        solved=task.solved(result.score),
    )


def comparable(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records without wall-clock fields."""
    return [{k: v for k, v in r.items() if k not in TIME_FIELDS} for r in records]


def leaks(episode: Episode) -> list[tuple[int, str]]:
    """(step, word) for each hidden word in a successful result that the call did not supply."""
    found: list[tuple[int, str]] = []
    for record in episode.records:
        if record.get("type") != "step" or not record["result"]["ok"]:
            continue
        said = json.dumps(record["result"]["content"])
        asked = json.dumps(record["call"]["args"])
        for word in episode.hidden:
            pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])")
            if pattern.search(said) and not pattern.search(asked):
                found.append((record["step"], word))
    return found


def _resolve(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return defs.get(ref.rsplit("/", 1)[-1], {})
    return schema


def _value(schema: dict[str, Any], defs: dict[str, Any], rng: random.Random) -> JsonValue:
    """A value that usually fits the schema and sometimes does not."""
    schema = _resolve(schema, defs)
    if "anyOf" in schema:
        return _value(rng.choice(schema["anyOf"]), defs, rng)
    if "enum" in schema:
        return rng.choice(
            [*schema["enum"], "not-a-choice"] if rng.random() < 0.1 else schema["enum"]
        )
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if kind == "integer":
        low = int(schema.get("minimum", schema.get("exclusiveMinimum", -5)))
        high = int(schema.get("maximum", schema.get("exclusiveMaximum", low + 200)))
        return rng.randint(low - 2, high + 2)
    if kind == "number":
        return rng.uniform(-10, 1000)
    if kind == "boolean":
        return rng.random() < 0.5
    if kind == "string":
        return rng.choice(["", "x", "api", "db", "cache", f"id-{rng.randint(0, 99)}"])
    if kind == "array":
        return [_value(schema.get("items", {}), defs, rng) for _ in range(rng.randint(0, 3))]
    return None


class Fuzzer:
    """Seeded random tool calls: mostly well-formed, some malformed, some unknown tools."""

    name = "fuzz"

    def __init__(self, seed: int) -> None:
        self._seed = seed
        self._rng = random.Random(seed)
        self._tools: list[ToolSpec] = []

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None:
        self._rng = random.Random(f"fuzz:{task_spec.ref}:{self._seed}")
        self._tools = list(tools)

    async def act(self, obs: Observation) -> ToolCall | None:
        rng = self._rng
        if not self._tools or rng.random() < 0.05:
            return ToolCall(name="no_such_tool", args={})
        spec = rng.choice(self._tools)
        schema = spec.args_model.model_json_schema()
        defs = schema.get("$defs", {})
        args: dict[str, JsonValue] = {}
        for key, prop in schema.get("properties", {}).items():
            if rng.random() < 0.9:
                args[key] = _value(prop, defs, rng)
        return ToolCall(name=spec.name, args=args)


__all__ = ["Episode", "Fuzzer", "comparable", "leaks", "run"]
