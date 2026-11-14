from crucible.envs.incident.world import (
    FAULT_KINDS,
    FAULTS,
    HERRING_MARGIN,
    HORIZON,
    SERVICES,
    RootCause,
    make_world,
)

PINNED_KIND_START = ("bad_deploy", 45)
PINNED_FIRST_DEPLOY = "d-9f1f"
PINNED_DEMAND_0 = 1025.085795


def test_same_seed_equal_world() -> None:
    assert make_world(5) == make_world(5)


def test_different_seeds_differ() -> None:
    assert make_world(1) != make_world(2)


def test_pinned_values_for_seed_0() -> None:
    w = make_world(0)
    assert (w.fault_kind, w.fault_start) == PINNED_KIND_START
    assert w.deploys[0].id == PINNED_FIRST_DEPLOY
    assert round(w.demand[0], 6) == PINNED_DEMAND_0


def test_shapes() -> None:
    w = make_world(0)
    assert len(w.demand) == HORIZON
    for s in SERVICES:
        for series in (
            w.noise[s].error_rate,
            w.noise[s].latency_p99_ms,
            w.noise[s].cpu,
            w.noise[s].disk_pct,
        ):
            assert len(series) == HORIZON


def test_seeds_0_to_29_invariants() -> None:
    kinds: set[RootCause] = set()
    for seed in range(30):
        w = make_world(seed)
        kinds.add(w.fault_kind)
        assert 15 <= w.fault_start <= 45
        ids = [d.id for d in w.deploys]
        assert len(ids) == len(set(ids))
        assert all(d.id.startswith("d-") and len(d.id) == 6 for d in w.deploys)
        api_at_fault = [d for d in w.deploys if d.service == "api" and d.minute == w.fault_start]
        if w.fault_kind == "bad_deploy":
            assert len(api_at_fault) == 1
            assert w.bad_deploy_id == api_at_fault[0].id
        else:
            assert api_at_fault == []
            assert w.bad_deploy_id is None
        assert abs(w.herring.start - w.fault_start) > HERRING_MARGIN
        assert 5 <= w.herring.start <= 110
        assert 5 <= w.herring.duration <= 10
    assert kinds == set(FAULT_KINDS)


def test_catalogue_covers_all_faults() -> None:
    assert set(FAULTS) == set(FAULT_KINDS)
    assert all(f.root_cause == k for k, f in FAULTS.items())
