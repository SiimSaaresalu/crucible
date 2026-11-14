import pytest

from crucible.core.jobs import Job, RunSpec, expand, parse_seeds


def test_job_id_is_stable() -> None:
    job = Job(task="guess@1", policy="reference", seed=3, repeat=0)
    assert job.job_id == Job(task="guess@1", policy="reference", seed=3, repeat=0).job_id
    assert len(job.job_id) == 16
    int(job.job_id, 16)
    # Pinned: an accidental change would break resume of old runs.
    assert job.job_id == "850f104b7813d84e"


def test_job_id_is_unique_per_field() -> None:
    base = Job(task="guess@1", policy="reference", seed=3, repeat=0)
    others = [
        Job(task="guess@2", policy="reference", seed=3, repeat=0),
        Job(task="guess@1", policy="do_nothing", seed=3, repeat=0),
        Job(task="guess@1", policy="reference", seed=4, repeat=0),
        Job(task="guess@1", policy="reference", seed=3, repeat=1),
    ]
    ids = {base.job_id, *(j.job_id for j in others)}
    assert len(ids) == 5


def test_expand_order_and_count() -> None:
    spec = RunSpec(task="guess", policy="reference", seeds=[5, 2], repeats=2)
    jobs = expand(spec)
    assert [(j.seed, j.repeat) for j in jobs] == [(5, 0), (5, 1), (2, 0), (2, 1)]
    assert len({j.job_id for j in jobs}) == 4


def test_run_spec_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        RunSpec(task="t", policy="p", seeds=[1], repeats=0)
    with pytest.raises(ValueError, match="unique"):
        RunSpec(task="t", policy="p", seeds=[1, 1])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0-9,12,20-21", [*range(10), 12, 20, 21]),
        ("7", [7]),
        ("3-3", [3]),
        (" 1 , 4-5 ", [1, 4, 5]),
        ("5,1", [5, 1]),
    ],
)
def test_parse_seeds_happy(text: str, expected: list[int]) -> None:
    assert parse_seeds(text) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "Bad seed range"),
        ("1,,2", "Bad seed range"),
        ("5-2", "Reversed"),
        ("-1", "Bad seed range"),
        ("-3-5", "Bad seed range"),
        ("a", "Bad seed range"),
        ("1-2-3", "Bad seed range"),
        ("1,1", "repeat"),
        ("0-3,2", "repeat"),
    ],
)
def test_parse_seeds_errors(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_seeds(text)
