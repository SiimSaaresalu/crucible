"""Economic reward tests use independent arithmetic and immutable ledger inputs."""

import pytest

from crucible.envs.dispatch.grader import (
    DeliveryRecord,
    DispatchScoreSpec,
    GradeInput,
    ScoringConfig,
    punctuality_factor,
)


@pytest.mark.parametrize(
    ("delivered_at", "expected"),
    [(90.0, 1.0), (100.0, 1.0), (110.0, 0.5), (120.0, 0.0), (130.0, 0.0)],
)
def test_linear_lateness(delivered_at: float, expected: float) -> None:
    assert punctuality_factor(delivered_at, 100, 20) == expected


def test_zero_grace_disallows_late_credit() -> None:
    assert punctuality_factor(101, 100, 0) == 0
    assert punctuality_factor(100, 100, 0) == 1


def test_utility_uses_full_episode_value_and_physical_distance() -> None:
    episode = GradeInput(
        total_order_value=400,
        generated_orders=4,
        deliveries=(DeliveryRecord(100, 100, 20, 90), DeliveryRecord(80, 100, 20, 110)),
        distance_km=20,
    )
    score = DispatchScoreSpec(ScoringConfig(driving_cost_per_km=0.5)).compute(episode)
    # 100 on time + 40 late credit - 10 driving cost, over all four generated orders.
    assert score.total == pytest.approx(130 / 400)
    assert score.breakdown["total_utility"] == 130
    assert score.breakdown["deliveries"] == 2
    assert score.breakdown["late_deliveries"] == 1
    assert score.breakdown["on_time_percentage"] == 50
    assert score.breakdown["undelivered_orders"] == 2
    assert DispatchScoreSpec(ScoringConfig(driving_cost_per_km=0.5)).compute(episode) == score


def test_no_deliveries_scores_exactly_zero() -> None:
    score = DispatchScoreSpec().compute(GradeInput(100, 2, (), 0))
    assert score.total == 0
    assert score.breakdown["total_utility"] == 0


def test_unprofitable_driving_is_clipped_without_losing_raw_utility() -> None:
    score = DispatchScoreSpec(ScoringConfig(driving_cost_per_km=1)).compute(
        GradeInput(100, 1, (DeliveryRecord(100, 100, 20, 130),), 5)
    )
    assert score.total == 0
    assert score.breakdown["total_utility"] == -5


def test_greater_distance_reduces_economic_reward() -> None:
    delivery = (DeliveryRecord(100, 100, 20, 90),)
    grader = DispatchScoreSpec()
    short = grader.compute(GradeInput(100, 1, delivery, 5))
    long = grader.compute(GradeInput(100, 1, delivery, 10))
    assert short.total > long.total
