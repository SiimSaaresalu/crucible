"""Calibration: scripted solvers must land in their reward bands on seeds 0-29."""

import asyncio
import statistics
from pathlib import Path

import pytest

from nightshift.core.episode import EpisodeResult
from nightshift.core.executor import LocalExecutor
from nightshift.core.jobs import RunSpec, expand
from nightshift.core.policy import Policy
from nightshift.tasks.incident.world import make_world
from tests.core.helpers import play
from tests.incident.scripts import Informed, ScaleOnly, Shotgun, StopEarly

SEEDS = list(range(30))
TASK = "incident@1"
SOLVERS = ("informed", "shotgun", "stop_early", "scale_only")


def run_solver(name: str, out: Path) -> dict[int, EpisodeResult]:
    results: dict[int, EpisodeResult] = {}
    for seed in SEEDS:
        world = make_world(seed)
        policy: Policy
        if name == "informed":
            policy = Informed(world)
        elif name == "shotgun":
            policy = Shotgun(world)
        elif name == "stop_early":
            policy = StopEarly(world, seed)
        else:
            policy = ScaleOnly(world, seed)
        results[seed] = asyncio.run(play(policy, seed, out / f"{name}-{seed}.jsonl", TASK))
    return results


def totals(results: dict[int, EpisodeResult]) -> list[float]:
    return [results[s].score.total for s in SEEDS]


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[int, EpisodeResult]]:
    out = tmp_path_factory.mktemp("calibration")
    return {n: run_solver(n, out) for n in SOLVERS}


def test_informed_is_good_but_not_perfect(runs: dict[str, dict[int, EpisodeResult]]) -> None:
    for seed, r in runs["informed"].items():
        assert r.score.flags["probes_healthy"], seed
        assert r.score.breakdown["diagnosis"] == 1, seed
        assert 0.6 <= r.score.total < 1.0, (seed, r.score.total)
    below = sum(t < 0.97 for t in totals(runs["informed"]))
    assert below >= 0.8 * len(SEEDS), below


def test_shotgun_is_clearly_worse(runs: dict[str, dict[int, EpisodeResult]]) -> None:
    for seed in SEEDS:
        informed = runs["informed"][seed].score.total
        assert runs["shotgun"][seed].score.total < informed - 0.15, seed


def test_stop_early_scores_near_zero(runs: dict[str, dict[int, EpisodeResult]]) -> None:
    assert statistics.mean(totals(runs["stop_early"])) < 0.1


def test_scale_only_is_worse_than_informed(runs: dict[str, dict[int, EpisodeResult]]) -> None:
    for seed in SEEDS:
        assert runs["scale_only"][seed].score.total < runs["informed"][seed].score.total, seed


def test_do_nothing_is_zero_and_concurrency_independent(tmp_path: Path) -> None:
    spec = RunSpec(task=TASK, policy="do_nothing", seeds=list(range(10)))
    wide = asyncio.run(LocalExecutor(concurrency=8).run(expand(spec), tmp_path / "wide"))
    narrow = asyncio.run(LocalExecutor(concurrency=1).run(expand(spec), tmp_path / "narrow"))
    assert [r.score.total for r in wide] == [0.0] * 10
    assert [r.score.total for r in wide] == [r.score.total for r in narrow]
