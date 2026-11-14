from crucible.core.rng import RngStreams

EXO = [25, 989, 969]
ACT = [297, 553, 285]
EXTRA = [15, 698, 227]


def test_pinned_values_for_seed_0() -> None:
    rng = RngStreams(0)
    assert [rng.exogenous.randint(1, 1000) for _ in range(3)] == EXO
    assert [rng.actions.randint(1, 1000) for _ in range(3)] == ACT
    assert [rng.stream("extra").randint(1, 1000) for _ in range(3)] == EXTRA


def test_same_seed_same_values() -> None:
    a, b = RngStreams(7), RngStreams(7)
    assert [a.exogenous.random() for _ in range(5)] == [b.exogenous.random() for _ in range(5)]


def test_different_seeds_differ() -> None:
    assert RngStreams(1).exogenous.random() != RngStreams(2).exogenous.random()


def test_streams_are_independent() -> None:
    rng = RngStreams(0)
    assert rng.exogenous is not rng.actions
    assert rng.stream("exogenous") is rng.exogenous
    assert rng.exogenous.random() != RngStreams(0).actions.random()


def test_actions_do_not_change_exogenous() -> None:
    quiet = RngStreams(3)
    expected = [quiet.exogenous.random() for _ in range(5)]
    busy = RngStreams(3)
    for _ in range(100):
        busy.actions.random()
    assert [busy.exogenous.random() for _ in range(5)] == expected
