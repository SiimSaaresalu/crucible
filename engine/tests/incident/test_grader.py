from pathlib import Path

import pytest

from nightshift.core.scoring import load_scoring_config
from nightshift.tasks.incident.grader import (
    SCORING_CONFIG,
    SCORING_HASH,
    GradeInput,
    IncidentScoreSpec,
    ScoringConfig,
    oracle_interventions,
)
from nightshift.tasks.incident.state import EpisodeState, Intervention
from nightshift.tasks.incident.world import World, make_world

SEEDS = range(50)
SPEC = IncidentScoreSpec(SCORING_CONFIG)


def _oracle_state(world: World) -> EpisodeState:
    return EpisodeState(
        interventions=oracle_interventions(world),
        first_repair_correct=True,
        evidence_before_first_repair=True,
        resolved=world.fault_kind,
    )


def _grade(world: World, state: EpisodeState, spec: IncidentScoreSpec = SPEC):
    return spec.compute(GradeInput(world, state))


@pytest.mark.parametrize("seed", SEEDS)
def test_do_nothing_scores_exactly_zero(seed: int) -> None:
    score = _grade(make_world(seed), EpisodeState())
    assert score.total == 0.0
    assert score.breakdown["recovered"] == 0.0
    assert score.breakdown["diagnosis"] == 0.0
    assert score.breakdown["oracle_gain"] > 0


@pytest.mark.parametrize("seed", SEEDS)
def test_oracle_scores_one(seed: int) -> None:
    world = make_world(seed)
    score = _grade(world, _oracle_state(world))
    assert score.total == pytest.approx(1.0, abs=1e-9)
    assert score.breakdown["diagnosis"] == 1.0
    assert all(score.flags.values())


def test_total_clamped_negative_raw_kept() -> None:
    world = make_world(0)
    score = _grade(world, EpisodeState(wrong_repairs=50))
    assert score.total == 0.0
    assert score.breakdown["raw"] < 0
    for seed in SEEDS:
        w = make_world(seed)
        assert 0.0 <= _grade(w, _oracle_state(w)).total <= 1.0


def _broken(world: World, case: str) -> EpisodeState:
    state = _oracle_state(world)
    if case == "resolved_wrong":
        wrong = "cache_crash" if world.fault_kind != "cache_crash" else "bad_deploy"
        return state.model_copy(update={"resolved": wrong})
    if case == "first_repair":
        return state.model_copy(update={"first_repair_correct": False})
    if case == "evidence":
        return state.model_copy(update={"evidence_before_first_repair": False})
    # probes unhealthy: the fault is never repaired
    return state.model_copy(update={"interventions": []})


@pytest.mark.parametrize("case", ["resolved_wrong", "first_repair", "evidence", "probes"])
def test_each_diagnosis_condition_alone_zeroes_diagnosis(case: str) -> None:
    world = make_world(3)
    score = _grade(world, _broken(world, case))
    assert score.breakdown["diagnosis"] == 0.0


def test_unhealthy_probes_after_good_rollback() -> None:
    # Rolling back a good deploy leaves the old bug, so probes stay unhealthy.
    world = next(w for w in map(make_world, SEEDS) if w.fault_kind == "bad_deploy")
    good = next(d for d in world.deploys if not d.is_bad)
    state = _oracle_state(world)
    state = state.model_copy(
        update={
            "interventions": [
                *state.interventions,
                Intervention(
                    tool="rollback", target=good.id, start=world.fault_start + 10, duration=5
                ),
            ]
        }
    )
    score = _grade(world, state)
    assert score.flags["probes_healthy"] is False
    assert score.breakdown["diagnosis"] == 0.0


def test_penalties_scale_with_config() -> None:
    world = make_world(1)
    base = _oracle_state(world)
    state = base.model_copy(update={"wrong_repairs": 1, "healthy_damage_actions": 2})
    cfg = ScoringConfig(
        wrong_repair_requests=100,
        healthy_damage_requests=10,
        scaling_requests_per_instance_minute=0,
        base_weight=0.7,
        diagnosis_weight=0.3,
    )
    a = _grade(world, state, IncidentScoreSpec(cfg))
    doubled = cfg.model_copy(update={"wrong_repair_requests": 200, "healthy_damage_requests": 20})
    b = _grade(world, state, IncidentScoreSpec(doubled))
    assert a.breakdown["penalty_requests"] == 120
    assert b.breakdown["penalty_requests"] == 240
    assert b.breakdown["penalty_frac"] == pytest.approx(2 * a.breakdown["penalty_frac"])


def test_scaling_penalty_uses_instance_minutes() -> None:
    world = make_world(1)
    state = _oracle_state(world)
    scale = Intervention(tool="scale", target="api", start=10, duration=1, n=3)
    state = state.model_copy(update={"interventions": [*state.interventions, scale]})
    score = _grade(world, state)
    minutes = score.breakdown["instance_minutes"]
    assert minutes > 0
    expected = minutes * SCORING_CONFIG.scaling_requests_per_instance_minute
    assert score.breakdown["penalty_requests"] == pytest.approx(expected)


def test_bad_yaml_raises_value_error(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text(
        "wrong_repair_requests: 1\nhealthy_damage_requests: 1\n"
        "scaling_requests_per_instance_minute: 1\nbase_weight: 0.7\n"
        "diagnosis_weight: 0.3\nextra_key: 1\n"
    )
    with pytest.raises(ValueError, match="extra_key"):
        load_scoring_config(unknown, ScoringConfig)
    wrong_type = tmp_path / "type.yaml"
    wrong_type.write_text(
        "wrong_repair_requests: lots\nhealthy_damage_requests: 1\n"
        "scaling_requests_per_instance_minute: 1\nbase_weight: 0.7\ndiagnosis_weight: 0.3\n"
    )
    with pytest.raises(ValueError, match="wrong_repair_requests"):
        load_scoring_config(wrong_type, ScoringConfig)
    negative = tmp_path / "neg.yaml"
    negative.write_text(
        "wrong_repair_requests: -1\nhealthy_damage_requests: 1\n"
        "scaling_requests_per_instance_minute: 1\nbase_weight: 0.7\ndiagnosis_weight: 0.3\n"
    )
    with pytest.raises(ValueError, match="wrong_repair_requests"):
        load_scoring_config(negative, ScoringConfig)


def test_hash_is_stable() -> None:
    path = Path(__file__).parents[2] / "src/nightshift/tasks/incident/scoring.yaml"
    _, digest = load_scoring_config(path, ScoringConfig)
    assert digest == SCORING_HASH
    assert len(SCORING_HASH) == 64
