"""Client side: play a `Policy` against a running server."""

from typing import Protocol, Self

import httpx
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


__all__ = ["HttpEpisode", "RemoteEpisode", "RemoteOutcome", "drive"]
