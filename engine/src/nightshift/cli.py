"""The `nightshift` command line: list tasks and policies, run an eval and print a table."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import BaseModel, ConfigDict
from rich.console import Console
from rich.table import Table

from nightshift.core.episode import EpisodeResult
from nightshift.core.executor import LocalExecutor
from nightshift.core.jobs import Job, RunSpec, expand, parse_seeds
from nightshift.core.registry import get_policy, get_task, list_policies, list_tasks

app = typer.Typer(
    name="nightshift",
    help="Run and compare policies on Night Shift Gym tasks.",
    no_args_is_help=True,
    add_completion=False,
)

_SHARE_FLAGS = ("root_cause_correct", "probes_healthy", "budget_exhausted", "env_error")


class Row(BaseModel):
    """One policy's line in the summary table. A share is `None` when no episode has the flag."""

    model_config = ConfigDict(frozen=True)

    policy: str
    episodes: int
    mean: float
    min: float
    max: float
    shares: dict[str, float | None]


def summarise(results: Sequence[EpisodeResult], policies: Sequence[str]) -> list[Row]:
    """One row per policy, in the order given. Policies with no results get no row."""
    rows: list[Row] = []
    for policy in policies:
        mine = [r for r in results if r.job.policy == policy]
        if not mine:
            continue
        totals = [r.score.total for r in mine]
        shares: dict[str, float | None] = {}
        for flag in _SHARE_FLAGS:
            seen = [r.score.flags[flag] for r in mine if flag in r.score.flags]
            shares[flag] = sum(seen) / len(mine) if seen else None
        rows.append(
            Row(
                policy=policy,
                episodes=len(mine),
                mean=sum(totals) / len(totals),
                min=min(totals),
                max=max(totals),
                shares=shares,
            )
        )
    return rows


def _fail(message: str) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(2)


def _share(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _summary_table(rows: Sequence[Row]) -> Table:
    table = Table(title="Results")
    table.add_column("policy", no_wrap=True)
    for name in ("episodes", "mean", "min", "max", *_SHARE_FLAGS):
        table.add_column(name, justify="right", no_wrap=True, overflow="fold")
    for row in rows:
        table.add_row(
            row.policy,
            str(row.episodes),
            f"{row.mean:.3f}",
            f"{row.min:.3f}",
            f"{row.max:.3f}",
            *(_share(row.shares[f]) for f in _SHARE_FLAGS),
        )
    return table


def _episode_table(results: Sequence[EpisodeResult]) -> Table:
    table = Table(title="Episodes")
    for name in ("policy", "seed", "repeat", "score", "steps", "stop_reason"):
        table.add_column(name, justify="left" if name in ("policy", "stop_reason") else "right")
    for r in results:
        table.add_row(
            r.job.policy,
            str(r.seed),
            str(r.job.repeat),
            f"{r.score.total:.3f}",
            str(r.steps),
            r.stop_reason,
        )
    return table


def _build_jobs(task: str, policies: list[str], seeds: list[int], repeats: int) -> list[Job]:
    try:
        get_task(task)
        for policy in policies:
            get_policy(policy, task)
        return [
            job
            for policy in policies
            for job in expand(RunSpec(task=task, policy=policy, seeds=seeds, repeats=repeats))
        ]
    except (KeyError, ValueError) as exc:
        _fail(str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc))


def _default_out(task: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return Path("runs") / f"{task.replace('@', '-')}-{stamp}"


@app.command("tasks")
def tasks_command() -> None:
    """List registered tasks."""
    for ref in list_tasks():
        typer.echo(ref)


@app.command("policies")
def policies_command(
    task: Annotated[str, typer.Option("--task", help="Task ref, e.g. incident@1.")],
) -> None:
    """List the policies available for a task."""
    try:
        get_task(task)
        names = list_policies(task)
    except (KeyError, ValueError) as exc:
        _fail(str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc))
    for name in names:
        typer.echo(name)


@app.command("eval")
def eval_command(
    task: Annotated[str, typer.Option("--task", help="Task ref, e.g. incident@1.")],
    policy: Annotated[str, typer.Option(help="Comma-separated policy names.")] = "reference",
    seeds: Annotated[str, typer.Option(help="Seeds, e.g. '0-9,12'.")] = "0-9",
    repeats: Annotated[int, typer.Option(min=1, help="Episodes per seed.")] = 1,
    concurrency: Annotated[int, typer.Option(min=1, help="Episodes run at once.")] = 1,
    out: Annotated[Path | None, typer.Option(help="Output folder (reuse to resume).")] = None,
    per_seed: Annotated[bool, typer.Option("--per-seed", help="Also print each episode.")] = False,
) -> None:
    """Run policies on a task and print one results table."""
    policies = [p.strip() for p in policy.split(",") if p.strip()]
    if not policies:
        _fail("--policy needs at least one name")
    if len(set(policies)) != len(policies):
        _fail(f"--policy repeats a name: {policy!r}")
    try:
        seed_list = parse_seeds(seeds)
    except ValueError as exc:
        _fail(str(exc))
    jobs = _build_jobs(task, policies, seed_list, repeats)
    out_dir = out if out is not None else _default_out(task)
    results = asyncio.run(LocalExecutor(concurrency=concurrency).run(jobs, out_dir))

    console = Console(width=max(Console().width, 130))  # keep the table readable when piped
    console.print(_summary_table(summarise(results, policies)))
    if per_seed:
        console.print(_episode_table(results))
    console.print(f"Output folder: {out_dir}")
    console.print(f"Summary CSV: {out_dir / 'summary.csv'}", soft_wrap=True)
    if any(r.score.flags.get("env_error", False) for r in results):
        typer.echo("Error: at least one episode ended with env_error", err=True)
        raise typer.Exit(1)


__all__ = ["Row", "app", "summarise"]
