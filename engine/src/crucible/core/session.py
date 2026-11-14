"""One episode's per-call rules, shared by the in-process loop, HTTP and MCP."""

from crucible.core.jobs import Job
from crucible.core.scoring import Score
from crucible.core.task import Task
from crucible.core.tools import Observation, ToolCall, ToolResult
from crucible.core.transcript import StopReason, TranscriptWriter


class EpisodeSession:
    """Owns the step counter, the budget, the transcript and the final grade of one episode."""

    def __init__(
        self, task: Task, seed: int, writer: TranscriptWriter, *, salt: str | None = None
    ) -> None:
        self._task = task
        self._seed = seed
        self._salt = salt
        self._writer = writer
        self._steps_used = 0
        self._outcome: tuple[StopReason, Score] | None = None

    @property
    def task(self) -> Task:
        return self._task

    @property
    def max_steps(self) -> int:
        return self._task.spec.max_steps

    @property
    def steps_used(self) -> int:
        return self._steps_used

    @property
    def closed(self) -> bool:
        return self._outcome is not None

    async def start(self, *, policy_name: str, job: Job) -> Observation:
        """Write the header, reset the task and return the first observation."""
        self._writer.header(
            task=self._task.spec.ref,
            scoring_hash=self._task.scoring_hash,
            policy=policy_name,
            seed=self._seed,
            repeat=job.repeat,
            job_id=job.job_id,
            salted=self._salt is not None,
        )
        return await self._task.reset(self._seed, salt=self._salt)

    async def step(self, call: ToolCall) -> ToolResult:
        """Run one call. Refusals for a closed, over or out-of-budget episode are not counted."""
        if self.closed:
            return ToolResult(ok=False, error="Episode is closed")
        if self._task.done():
            return ToolResult(ok=False, error="Episode is over")
        if self._steps_used >= self.max_steps:
            return ToolResult(ok=False, error=f"Step budget of {self.max_steps} calls is used up")
        result = await self._task.step(call)
        self._writer.step(self._steps_used, call, result)
        self._steps_used += 1
        return result

    async def close(self, error: str | None = None) -> tuple[StopReason, Score]:
        """Grade, write the footer and close the transcript. Idempotent."""
        if self._outcome is not None:
            return self._outcome
        reason: StopReason
        if error is not None:
            reason, score = "error", Score(total=0.0, flags={"env_error": True})
        else:
            if self._task.done():
                reason = "task_done"
            elif self._steps_used >= self.max_steps:
                reason = "budget_exhausted"
            else:
                reason = "policy_stop"
            graded = await self._task.grade()
            flags = {**graded.flags, "budget_exhausted": reason == "budget_exhausted"}
            score = graded.model_copy(update={"flags": flags})
        self._outcome = (reason, score)
        self._writer.footer(score, reason, error)
        self._writer.close()
        return self._outcome


__all__ = ["EpisodeSession"]
