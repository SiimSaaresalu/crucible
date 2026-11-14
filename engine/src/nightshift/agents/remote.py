"""Client side: play a `Policy` against a running server."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, Self

import httpx
from mcp import Client
from pydantic import BaseModel, ConfigDict

from nightshift.core.policy import Policy
from nightshift.core.scoring import Score
from nightshift.core.task import TaskSpec
from nightshift.core.tools import Observation, ToolCall, ToolResult, ToolSpec, result_text
from nightshift.core.transcript import StopReason


class RemoteOutcome(BaseModel):
    """What the server reports when an episode is closed."""

    model_config = ConfigDict(frozen=True)

    score: Score
    steps: int
    stop_reason: StopReason


class RemoteEpisode(Protocol):
    async def step(self, call: ToolCall) -> ToolResult: ...

    async def close(self) -> RemoteOutcome: ...


class HttpEpisode:
    """One episode on a `/v1` server."""

    def __init__(
        self, client: httpx.AsyncClient, episode_id: str, first_observation: Observation
    ) -> None:
        self._client = client
        self.episode_id = episode_id
        self.first_observation = first_observation

    @classmethod
    async def start(
        cls,
        task: str,
        seed: int,
        *,
        base_url: str = "http://localhost:8000",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> tuple[Self, str]:
        """Start an episode. Return it with the task instruction."""
        client = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=60.0)
        try:
            response = await client.post("/v1/episodes", json={"task_id": task, "seed": seed})
            response.raise_for_status()
        except BaseException:
            await client.aclose()
            raise
        body = response.json()
        first = Observation.model_validate(body["observation"])
        return cls(client, str(body["episode_id"]), first), str(body["instruction"])

    async def step(self, call: ToolCall) -> ToolResult:
        response = await self._client.post(
            f"/v1/episodes/{self.episode_id}/step", json=call.model_dump(mode="json")
        )
        response.raise_for_status()
        return ToolResult.model_validate(response.json())

    async def close(self) -> RemoteOutcome:
        """Close the episode on the server and the HTTP client."""
        try:
            response = await self._client.post(f"/v1/episodes/{self.episode_id}/close")
            response.raise_for_status()
            return RemoteOutcome.model_validate(response.json())
        finally:
            await self._client.aclose()


async def fetch_grade(base_url: str, token: str) -> RemoteOutcome:
    """Read the outcome from a sidecar's `/grade` route. The agent has no token, a harness has."""
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        response = await client.get("/grade", headers={"x-grade-token": token})
        response.raise_for_status()
        body = response.json()
    score = Score(total=body["reward"], breakdown=body["breakdown"], flags=body["flags"])
    return RemoteOutcome(score=score, steps=body["steps"], stop_reason=body["stop_reason"])


class McpEpisode:
    """The one episode of a sidecar, played over MCP.

    The agent cannot grade, so `close()` only closes the connection. With a `grade` callback
    (a harness that has the token, see `fetch_grade`) it returns that outcome. Without one it
    returns an ungraded outcome (`flags["ungraded"]`, score 0, the client's own step count and
    `policy_stop`), which must not be read as a score.
    """

    def __init__(
        self, client: Client, grade: Callable[[], Awaitable[RemoteOutcome]] | None = None
    ) -> None:
        self._client = client
        self._grade = grade
        self.steps = 0

    @classmethod
    async def connect(
        cls, url: str, *, grade: Callable[[], Awaitable[RemoteOutcome]] | None = None
    ) -> tuple[Self, str]:
        """Connect to the MCP endpoint at `url`. Return the episode and the instruction.

        Call `close()` from the same task that called `connect()`.
        """
        client = Client(url)
        await client.__aenter__()
        return cls(client, grade), client.instructions or ""

    async def step(self, call: ToolCall) -> ToolResult:
        self.steps += 1
        raw = await self._client.call_tool(call.name, dict(call.args))
        structured: Any = raw.structured_content
        if isinstance(structured, dict):
            return ToolResult.model_validate(structured)
        text = " ".join(getattr(block, "text", "") for block in raw.content)
        return ToolResult(ok=False, error=text or "Empty MCP result")

    async def close(self) -> RemoteOutcome:
        await self._client.__aexit__(None, None, None)
        if self._grade is not None:
            return await self._grade()
        return RemoteOutcome(
            score=Score(total=0.0, flags={"ungraded": True}),
            steps=self.steps,
            stop_reason="policy_stop",
        )


async def drive(
    policy: Policy,
    remote: RemoteEpisode,
    task_spec: TaskSpec,
    tools: list[ToolSpec],
    instruction: str,
    first_observation: Observation | None = None,
) -> RemoteOutcome:
    """Play `policy` against `remote` with the same loop as the in-process episode."""
    obs = first_observation or Observation()
    await policy.reset(task_spec, tools, instruction)
    for _ in range(task_spec.max_steps):
        call = await policy.act(obs)
        if call is None:
            break
        result = await remote.step(call)
        obs = Observation(text=result_text(result), last_result=result)
    return await remote.close()


__all__ = [
    "HttpEpisode",
    "McpEpisode",
    "RemoteEpisode",
    "RemoteOutcome",
    "drive",
    "fetch_grade",
]
