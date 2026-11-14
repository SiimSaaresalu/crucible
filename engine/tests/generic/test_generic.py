"""Checks that every registered task must pass. A task opts out through `Task.checks`.

Each check runs over `checks.seeds`. Tasks are found in the registry, so a new task (for example
one made by `crucible new-env`) is checked without editing this file.
"""

import asyncio
from pathlib import Path

import pytest

from crucible.core.policy import Policy
from crucible.core.registry import get_policy, get_task, list_policies, list_tasks
from crucible.core.task import TaskChecks
from tests.generic.runner import Episode, Fuzzer, comparable, leaks, run

TASKS = list_tasks()


def _checks(ref: str) -> TaskChecks:
    return type(get_task(ref)).checks


def _need(ref: str, flag: str) -> TaskChecks:
    checks = _checks(ref)
    if not getattr(checks, flag):
        pytest.skip(f"{ref} opts out of {flag}")
    return checks


def _play(ref: str, make: str | Policy, seed: int, path: Path) -> Episode:
    policy = get_policy(make, ref) if isinstance(make, str) else make
    return asyncio.run(run(ref, policy, seed, path))


def _all(ref: str, name: str, seeds: tuple[int, ...], out: Path) -> dict[int, Episode]:
    return {s: _play(ref, name, s, out / f"{name}-{s}.jsonl") for s in seeds}


@pytest.mark.parametrize("ref", TASKS)
def test_determinism(ref: str, tmp_path: Path) -> None:
    checks = _need(ref, "determinism")
    for seed in checks.seeds:
        for label in ("reference", "fuzz"):
            a, b = (
                _play(ref, "reference" if label == "reference" else Fuzzer(seed), seed, path)
                for path in (tmp_path / f"{label}-{seed}-{x}.jsonl" for x in "ab")
            )
            assert comparable(a.records) == comparable(b.records), (ref, label, seed)
            assert a.result.score == b.result.score, (ref, label, seed)


@pytest.mark.parametrize("ref", TASKS)
def test_do_nothing_scores_zero(ref: str, tmp_path: Path) -> None:
    checks = _need(ref, "do_nothing_zero")
    for seed, episode in _all(ref, "do_nothing", checks.seeds, tmp_path).items():
        assert episode.result.score.total == 0, (ref, seed, episode.result.score)


@pytest.mark.parametrize("ref", TASKS)
def test_reward_within_0_and_1(ref: str, tmp_path: Path) -> None:
    checks = _need(ref, "reward_bounds")
    names = ["reference", "do_nothing", *checks.exploit_policies]
    for seed in checks.seeds:
        episodes = [_play(ref, n, seed, tmp_path / f"{n}-{seed}.jsonl") for n in names]
        episodes.append(_play(ref, Fuzzer(seed), seed, tmp_path / f"fuzz-{seed}.jsonl"))
        for episode in episodes:
            total = episode.result.score.total
            assert 0.0 <= total <= 1.0, (ref, episode.result.policy, seed, total)


@pytest.mark.parametrize("ref", TASKS)
def test_no_hidden_state_in_tool_results(ref: str, tmp_path: Path) -> None:
    checks = _need(ref, "no_hidden_state")
    names = ["reference", *checks.exploit_policies]
    for seed in checks.seeds:
        episodes = [_play(ref, n, seed, tmp_path / f"{n}-{seed}.jsonl") for n in names]
        episodes.append(_play(ref, Fuzzer(seed), seed, tmp_path / f"fuzz-{seed}.jsonl"))
        for episode in episodes:
            assert not leaks(episode), (ref, episode.result.policy, seed, leaks(episode))


@pytest.mark.parametrize("ref", TASKS)
def test_reference_solves_every_seed(ref: str, tmp_path: Path) -> None:
    checks = _need(ref, "reference_solves")
    for seed, episode in _all(ref, "reference", checks.seeds, tmp_path).items():
        assert episode.solved, (ref, seed, episode.result.score)


@pytest.mark.parametrize("ref", TASKS)
def test_exploits_score_below_reference(ref: str, tmp_path: Path) -> None:
    checks = _need(ref, "exploits_below_reference")
    if not checks.exploit_policies:
        pytest.skip(f"{ref} names no exploit policies")
    reference = _all(ref, "reference", checks.seeds, tmp_path)
    for name in checks.exploit_policies:
        for seed, episode in _all(ref, name, checks.seeds, tmp_path).items():
            got, best = episode.result.score.total, reference[seed].result.score.total
            assert got < best, (ref, name, seed, got, best)


@pytest.mark.parametrize("ref", TASKS)
def test_oracle_scores_one(ref: str, tmp_path: Path) -> None:
    checks = _need(ref, "oracle_perfect")
    if "oracle" not in list_policies(ref):
        pytest.skip(f"{ref} defines no oracle policy")
    for seed, episode in _all(ref, "oracle", checks.seeds, tmp_path).items():
        assert episode.result.score.total == 1.0, (ref, seed, episode.result.score)


def test_exploit_policies_are_registered() -> None:
    for ref in TASKS:
        for name in _checks(ref).exploit_policies:
            assert name in list_policies(ref), (ref, name)
