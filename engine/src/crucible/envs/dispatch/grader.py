"""Economic scoring from private, simulator-verified delivery records.

The denominator is the complete seeded order stream. It is independent of actions and is
never obtained from a public observation. A reference policy does not define the reward scale.
"""

from dataclasses import dataclass
from importlib.resources import as_file, files
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field

from crucible.core.scoring import Score, load_scoring_config


class ScoringConfig(BaseModel):
    """Versioned economic constants."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    driving_cost_per_km: float = Field(ge=0, allow_inf_nan=False)


def _load() -> tuple[ScoringConfig, str]:
    with as_file(files("crucible.envs.dispatch") / "scoring.yaml") as path:
        return load_scoring_config(path, ScoringConfig)


SCORING_CONFIG, SCORING_HASH = _load()


@dataclass(frozen=True)
class DeliveryRecord:
    """Immutable order metadata and a backend-verified completion time."""

    value: float
    deadline: float
    grace: float
    delivered_at: float


@dataclass(frozen=True)
class GradeInput:
    """Private aggregate input, built only from the world and backend ledger."""

    total_order_value: float
    generated_orders: int
    deliveries: tuple[DeliveryRecord, ...]
    distance_km: float


def punctuality_factor(delivered_at: float, deadline: float, grace: float) -> float:
    """Full credit on time, linearly declining partial credit during the grace period."""
    if delivered_at <= deadline:
        return 1.0
    if grace <= 0:
        return 0.0
    return max(0.0, 1.0 - (delivered_at - deadline) / grace)


class DispatchScoreSpec:
    """Grade genuine delivery utility without modifying the episode."""

    def __init__(self, config: ScoringConfig = SCORING_CONFIG) -> None:
        self.config = config

    def compute(self, episode: GradeInput) -> Score:
        if (
            not isfinite(episode.total_order_value)
            or episode.total_order_value <= 0
            or not isfinite(episode.distance_km)
            or episode.distance_km < 0
        ):
            raise ValueError("Dispatch grade requires positive potential value and valid distance")
        delivered = len(episode.deliveries)
        on_time = sum(d.delivered_at <= d.deadline for d in episode.deliveries)
        realized_value = sum(
            d.value * punctuality_factor(d.delivered_at, d.deadline, d.grace)
            for d in episode.deliveries
        )
        driving_cost = episode.distance_km * self.config.driving_cost_per_km
        utility = realized_value - driving_cost
        reward = max(0.0, min(1.0, utility / episode.total_order_value))
        return Score(
            total=reward,
            breakdown={
                "deliveries": float(delivered),
                "on_time_deliveries": float(on_time),
                "on_time_percentage": 100.0 * on_time / delivered if delivered else 0.0,
                "late_deliveries": float(delivered - on_time),
                "generated_orders": float(episode.generated_orders),
                "undelivered_orders": float(episode.generated_orders - delivered),
                "driving_distance_km": episode.distance_km,
                "realized_delivery_value": realized_value,
                "driving_cost": driving_cost,
                "other_operating_penalties": 0.0,
                "total_potential_value": episode.total_order_value,
                "total_utility": utility,
                "normalized_reward": reward,
            },
            flags={"delivered_any": delivered > 0},
        )


__all__ = [
    "SCORING_CONFIG",
    "SCORING_HASH",
    "DeliveryRecord",
    "DispatchScoreSpec",
    "GradeInput",
    "ScoringConfig",
    "punctuality_factor",
]
