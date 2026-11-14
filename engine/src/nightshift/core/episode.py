"""The one episode loop."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from nightshift.core.jobs import Job
from nightshift.core.policy import Policy
from nightshift.core.scoring import Score
from nightshift.core.task import Task
from nightshift.core.tools import Observation, ToolResult
from nightshift.core.transcript import StopReason, TranscriptWriter


class InfraError(Exception):
    """Transient infrastructure failure. The only error the executor retries."""


class EpisodeResult(BaseModel):
    """Outcome of one job."""

    model_config = ConfigDict(frozen=True)

    job: Job
    task: str
    policy: str
    seed: int
    score: Score
    steps: int
    stop_reason: StopReason
    transcript_path: Path
    retried: int = 0


def _result_text(result: ToolResult) -> str:
    """Text form of a tool result for `Observation.text`."""
    if not result.ok:
        return f"Error: {result.error}"
    if isinstance(result.content, str):
        return result.content
    return json.dumps(result.content, sort_keys=True)


async def _play(
    task: Task, policy: Policy, seed: int, writer: TranscriptWriter
) -> tuple[StopReason, Score]:
    obs = await task.reset(seed)
    await policy.reset(task.spec, task.tools(), task.instruction())
    reason: StopReason = "budget_exhausted"
    for step in range(task.spec.max_steps):
        if task.done():
            reason = "task_done"
            break
        call = await policy.act(obs)
        if call is None:
            reason = "policy_stop"
            break
        result = await task.step(call)
        writer.step(step, call, result)
        obs = Observation(text=_result_text(result), last_result=result)
    else:
        if task.done():
            reason = "task_done"
    score = await task.grade()
    flags = {**score.flags, "budget_exhausted": reason == "budget_exhausted"}
    return reason, score.model_copy(update={"flags": flags})


async def run_episode(
    task: Task,
    policy: Policy,
    seed: int,
    *,
    job: Job,
    writer: TranscriptWriter,
    record_infra_errors: bool = False,
) -> EpisodeResult:
    """Run one episode and write its transcript.

    Any exception ends the episode as `stop_reason="error"` with a zero score and
    `flags["env_error"]`, except `InfraError`: it is re-raised (and the partial transcript
    dropped) so the executor can retry, unless `record_infra_errors` is set (last attempt).
    """
    try:
        writer.header(
            task=task.spec.ref,
            scoring_hash=task.scoring_hash,
            policy=policy.name,
            seed=seed,
            repeat=job.repeat,
            job_id=job.job_id,
        )
        error: str | None = None
        try:
            reason, score = await _play(task, policy, seed, writer)
        except InfraError as exc:
            if not record_infra_errors:
                raise
            reason, score, error = "error", Score(total=0.0, flags={"env_error": True}), _name(exc)
        except Exception as exc:
            reason, score, error = "error", Score(total=0.0, flags={"env_error": True}), _name(exc)
        writer.footer(score, reason, error)
        writer.close()
    except BaseException:
        writer.abort()
        raise
    return EpisodeResult(
        job=job,
        task=task.spec.ref,
        policy=policy.name,
        seed=seed,
        score=score,
        steps=writer.step_count,
        stop_reason=reason,
        transcript_path=writer.path,
    )


def _name(exc: Exception) -> str:
    return type(exc).__name__


__all__ = ["EpisodeResult", "InfraError", "run_episode"]
