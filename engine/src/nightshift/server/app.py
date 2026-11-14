"""The FastAPI app factory and the HTTP v1 API."""

from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from nightshift.core.jobs import Job
from nightshift.core.registry import get_task, list_tasks
from nightshift.core.scoring import Score
from nightshift.core.session import EpisodeSession
from nightshift.core.task import Task
from nightshift.core.tools import Observation, ToolCall, ToolKind, ToolResult
from nightshift.core.transcript import StopReason, TranscriptWriter
from nightshift.server.logging import configure_logging
from nightshift.server.settings import Settings
from nightshift.server.store import Entry, InMemorySessionStore, SessionStore


class ToolInfo(BaseModel):
    name: str
    description: str
    kind: ToolKind
    input_schema: dict[str, JsonValue]


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    seed: int
    difficulty: int = Field(default=0, ge=0, le=0)


class StartResponse(BaseModel):
    episode_id: str
    instruction: str
    observation: Observation
    max_steps: int


class CloseResponse(BaseModel):
    score: Score
    steps: int
    stop_reason: StopReason


def _lookup(task_ref: str) -> Task:
    try:
        return get_task(task_ref)
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc.args[0])) from None
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from None


def create_app(settings: Settings | None = None, store: SessionStore | None = None) -> FastAPI:
    """Build the app. `store` lets tests inject a store with a fake clock."""
    settings = settings or Settings()
    logger = configure_logging(settings.log_level)
    sessions: SessionStore = store or InMemorySessionStore(settings.session_ttl_seconds)
    app = FastAPI(title="Night Shift Gym", docs_url=None, redoc_url=None, openapi_url=None)

    def entry_for(episode_id: str) -> Entry:
        entry = sessions.get(episode_id)
        if entry is None:
            raise HTTPException(404, detail="Unknown episode")
        return entry

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:  # pyright: ignore[reportUnusedFunction]
        return "ok"

    @app.get("/v1/tasks")
    async def tasks() -> list[str]:  # pyright: ignore[reportUnusedFunction]
        return list_tasks()

    @app.get("/v1/tasks/{task_id}/tools")
    async def tools(task_id: str) -> list[ToolInfo]:  # pyright: ignore[reportUnusedFunction]
        return [
            ToolInfo(
                name=t.name,
                description=t.description,
                kind=t.kind,
                input_schema=t.json_schema(),
            )
            for t in _lookup(task_id).tools()
        ]

    @app.post("/v1/episodes")
    async def start(body: StartRequest) -> StartResponse:  # pyright: ignore[reportUnusedFunction]
        task = _lookup(body.task_id)
        episode_id = uuid4().hex
        path: Path = settings.runs_dir / "episodes" / f"{episode_id}.jsonl"
        session = EpisodeSession(task, body.seed, TranscriptWriter(path))
        job = Job(task=task.spec.ref, policy="external", seed=body.seed, repeat=0)
        observation = await session.start(policy_name="external", job=job)
        sessions.put(episode_id, Entry(session))
        logger.info("episode started", extra={"episode_id": episode_id})
        return StartResponse(
            episode_id=episode_id,
            instruction=task.instruction(),
            observation=observation,
            max_steps=session.max_steps,
        )

    @app.post("/v1/episodes/{episode_id}/step")
    async def step(episode_id: str, call: ToolCall) -> ToolResult:  # pyright: ignore[reportUnusedFunction]
        entry = entry_for(episode_id)
        async with entry.lock:
            result = await entry.session.step(call)
        logger.info("step", extra={"episode_id": episode_id, "tool": call.name, "ok": result.ok})
        return result

    @app.post("/v1/episodes/{episode_id}/close")
    async def close(episode_id: str) -> CloseResponse:  # pyright: ignore[reportUnusedFunction]
        entry = entry_for(episode_id)
        async with entry.lock:
            reason, score = await entry.session.close()
            steps = entry.session.steps_used
        logger.info("episode closed", extra={"episode_id": episode_id})
        return CloseResponse(score=score, steps=steps, stop_reason=reason)

    return app


__all__ = ["create_app"]
