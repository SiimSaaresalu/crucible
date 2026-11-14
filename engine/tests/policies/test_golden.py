"""Policy scores are pinned per (task ref, policy, seed); a change needs a task version bump."""

import asyncio
import json
import os
import platform
from pathlib import Path

import pytest

from crucible.core.executor import LocalExecutor
from crucible.core.jobs import Job
from crucible.core.registry import get_task, list_tasks

GOLDEN = Path(__file__).parent / "golden_scores.json"
RUNTIMES = json.loads((GOLDEN.parent / "golden_runtimes.json").read_text())
SEEDS = list(range(10))
# Every registered task: its reference, do_nothing and the exploits its checks name.
POLICIES: dict[str, list[str]] = {
    ref: ["reference", *type(get_task(ref)).checks.exploit_policies, "do_nothing"]
    for ref in list_tasks()
}
CASES = [(task, policy) for task, names in POLICIES.items() for policy in names]
MESSAGE = (
    "Score changed: bump the task version or, if the change is intended for this version, "
    "regenerate with CRUCIBLE_UPDATE_GOLDEN=1."
)

Scores = dict[str, dict[str, dict[str, float]]]


def _runtime_supported(task: str) -> bool:
    required = RUNTIMES.get(task)
    if required is None:
        return True
    return (
        platform.system() == required["system"]
        and platform.machine() == required["machine"]
        and platform.python_version() == required["python"]
    )


@pytest.fixture(scope="module")
def measured(tmp_path_factory: pytest.TempPathFactory) -> Scores:
    jobs = [Job(task=t, policy=p, seed=s) for t, p in CASES for s in SEEDS]
    out = tmp_path_factory.mktemp("golden")
    results = asyncio.run(LocalExecutor(concurrency=8).run(jobs, out))
    scores: Scores = {}
    for r in results:
        scores.setdefault(r.job.task, {}).setdefault(r.job.policy, {})[str(r.seed)] = r.score.total
    return scores


@pytest.fixture(scope="module")
def golden(measured: Scores) -> Scores:
    if os.environ.get("CRUCIBLE_UPDATE_GOLDEN") == "1":
        unsupported = [task for task in measured if not _runtime_supported(task)]
        assert not unsupported, (
            f"Cannot replace canonical goldens on an unsupported inference runtime: {unsupported}"
        )
        GOLDEN.write_text(json.dumps(measured, indent=2, sort_keys=True) + "\n")
    return json.loads(GOLDEN.read_text())


def test_every_registered_task_has_golden_scores(golden: Scores) -> None:
    missing = [ref for ref in list_tasks() if ref not in golden]
    assert not missing, f"No golden scores for {missing}: regenerate with CRUCIBLE_UPDATE_GOLDEN=1."


@pytest.mark.parametrize(("task", "policy"), CASES)
def test_scores_match_golden(measured: Scores, golden: Scores, task: str, policy: str) -> None:
    if not _runtime_supported(task):
        pytest.skip(
            "Canonical golden requires its pinned inference runtime; see golden_runtimes.json "
            "and spikes/dynamic-dispatch-v2/runtime-linux.json"
        )
    if task not in golden:
        pytest.skip(f"{task} has no golden scores yet (see the test above)")
    expected = golden[task][policy]
    actual = measured[task][policy]
    assert actual.keys() == expected.keys()
    for seed, score in actual.items():
        where = f"{task} {policy} seed {seed}"
        assert score == pytest.approx(expected[seed], abs=1e-9), f"{where}. {MESSAGE}"
