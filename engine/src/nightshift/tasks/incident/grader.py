"""Scoring of `incident@1`: config, oracle replay and the 0-1 reward."""

from importlib.resources import as_file, files
from typing import Final, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from nightshift.core.scoring import Score, load_scoring_config
from nightshift.tasks.incident.sim import instance_minutes, probes_healthy, served_series
from nightshift.tasks.incident.state import EpisodeState, Intervention
from nightshift.tasks.incident.world import (
    CLEAN_DISK_MIN,
    FAULTS,
    RESTART_MIN,
    ROLLBACK_MIN,
    World,
)

__all__ = [
    "SCORING_CONFIG",
    "SCORING_HASH",
    "GradeInput",
    "IncidentScoreSpec",
    "ScoringConfig",
    "oracle_interventions",
]


class ScoringConfig(BaseModel):
    """Scoring constants, loaded from `scoring.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    wrong_repair_requests: float = Field(ge=0)
    healthy_damage_requests: float = Field(ge=0)
    scaling_requests_per_instance_minute: float = Field(ge=0)
    base_weight: float = Field(ge=0)
    diagnosis_weight: float = Field(ge=0)


def _load() -> tuple[ScoringConfig, str]:
    with as_file(files("nightshift.tasks.incident") / "scoring.yaml") as path:
        return load_scoring_config(path, ScoringConfig)


SCORING_CONFIG, SCORING_HASH = _load()

_REPAIR_MIN: Final = {
    "restart": RESTART_MIN,
    "rollback": ROLLBACK_MIN,
    "clean_disk": CLEAN_DISK_MIN,
}


class GradeInput(NamedTuple):
    """What the grader needs: the hidden world and the episode record."""

    world: World
    state: EpisodeState


def oracle_interventions(world: World) -> list[Intervention]:
    """The correct repair, applied at `fault_start`."""
    info = FAULTS[world.fault_kind]
    target = world.bad_deploy_id if info.repair_tool == "rollback" else info.service
    if target is None:
        raise ValueError("bad_deploy world without a bad deploy")
    return [
        Intervention(
            tool=info.repair_tool,
            target=target,
            start=world.fault_start,
            duration=_REPAIR_MIN[info.repair_tool],
        )
    ]


class IncidentScoreSpec:
    """Turns a `GradeInput` into a `Score`."""

    def __init__(self, config: ScoringConfig = SCORING_CONFIG) -> None:
        self.config = config

    def compute(self, episode: GradeInput) -> Score:
        world, state = episode
        cfg = self.config
        served_agent = sum(served_series(world, state.interventions))
        served_nothing = sum(served_series(world, []))
        served_oracle = sum(served_series(world, oracle_interventions(world)))
        recovered = served_agent - served_nothing
        oracle_gain = served_oracle - served_nothing
        if oracle_gain <= 0:
            raise ValueError(f"oracle gain must be positive, got {oracle_gain} (seed {world.seed})")

        minutes = instance_minutes(state.interventions)
        penalty = (
            state.wrong_repairs * cfg.wrong_repair_requests
            + state.healthy_damage_actions * cfg.healthy_damage_requests
            + minutes * cfg.scaling_requests_per_instance_minute
        )
        healthy = probes_healthy(world, state.interventions)
        root_cause_correct = state.resolved == world.fault_kind
        first_correct = state.first_repair_correct is True
        diagnosis = (
            1.0
            if root_cause_correct
            and first_correct
            and state.evidence_before_first_repair
            and healthy
            else 0.0
        )
        recovered_frac = recovered / oracle_gain
        penalty_frac = penalty / oracle_gain
        raw = recovered_frac * (cfg.base_weight + cfg.diagnosis_weight * diagnosis) - penalty_frac
        return Score(
            total=min(1.0, max(0.0, raw)),
            breakdown={
                "served_agent": served_agent,
                "served_nothing": served_nothing,
                "served_oracle": served_oracle,
                "recovered": recovered,
                "oracle_gain": oracle_gain,
                "wrong_repairs": float(state.wrong_repairs),
                "healthy_damage_actions": float(state.healthy_damage_actions),
                "instance_minutes": float(minutes),
                "penalty_requests": penalty,
                "recovered_frac": recovered_frac,
                "penalty_frac": penalty_frac,
                "diagnosis": diagnosis,
                "raw": raw,
            },
            flags={
                "resolved": state.resolved is not None,
                "root_cause_correct": root_cause_correct,
                "first_repair_correct": first_correct,
                "evidence_read": state.evidence_before_first_repair,
                "probes_healthy": healthy,
            },
        )
