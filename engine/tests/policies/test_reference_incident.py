"""The incident reference runbook solves every seed using only tool results."""

import asyncio
import json
import statistics
from pathlib import Path

import pytest

from nightshift.core.episode import EpisodeResult
from nightshift.core.transcript import TIME_FIELDS
from nightshift.policies.reference.incident import IncidentReference
from nightshift.tasks.incident.world import make_world
from tests.core.helpers import play

TASK = "incident@1"
SEEDS = list(range(100))
FLAGS = ("root_cause_correct", "first_repair_correct", "evidence_read", "probes_healthy")


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[int, EpisodeResult]:
    out = tmp_path_factory.mktemp("reference")
    return {s: asyncio.run(play(IncidentReference(), s, out / f"{s}.jsonl", TASK)) for s in SEEDS}


def test_reference_solves(runs: dict[int, EpisodeResult]) -> None:
    for seed, r in runs.items():
        for flag in FLAGS:
            assert r.score.flags[flag], (seed, flag)
        assert r.score.breakdown["wrong_repairs"] == 0, seed
        assert r.score.breakdown["healthy_damage_actions"] == 0, seed


def test_score_band(runs: dict[int, EpisodeResult]) -> None:
    # `Informed` is no upper bound: it reads 3 times before repairing.
    totals = [r.score.total for r in runs.values()]
    assert all(0.5 < t < 0.99 for t in totals), (min(totals), max(totals))
    assert statistics.mean(totals) >= 0.75, statistics.mean(totals)


def _normalised(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = [json.loads(ln) for ln in path.read_text().splitlines()]
    for record in records:
        for field in TIME_FIELDS:
            record.pop(field, None)
    return records


def test_deterministic(tmp_path: Path) -> None:
    for seed in (0, 7, 42):
        a = asyncio.run(play(IncidentReference(), seed, tmp_path / f"a{seed}.jsonl", TASK))
        b = asyncio.run(play(IncidentReference(), seed, tmp_path / f"b{seed}.jsonl", TASK))
        assert a.score == b.score
        assert _normalised(tmp_path / f"a{seed}.jsonl") == _normalised(tmp_path / f"b{seed}.jsonl")


def test_herring_before_fault(runs: dict[int, EpisodeResult]) -> None:
    seeds = [s for s in SEEDS if make_world(s).herring.start < make_world(s).fault_start]
    assert seeds
    for seed in seeds:
        assert runs[seed].score.breakdown["wrong_repairs"] == 0, seed
        assert runs[seed].score.flags["root_cause_correct"], seed
