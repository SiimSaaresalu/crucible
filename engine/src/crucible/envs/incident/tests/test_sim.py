"""White-box tests of the pure simulation. They read the hidden world freely."""

import math
from functools import cache

import pytest

from crucible.envs.incident.sim import (
    alerts_at,
    availability,
    classify_repair,
    fault_active,
    instance_minutes,
    logs_between,
    metrics_at,
    next_alert_start,
    probes_healthy,
    served,
    served_series,
    service_factors,
    visible_deploys,
)
from crucible.envs.incident.state import EpisodeState, Intervention
from crucible.envs.incident.world import (
    CLEAN_DISK_MIN,
    COLD_CACHE_FACTOR,
    COLD_CACHE_MIN,
    FAULT_KINDS,
    FAULTS,
    HORIZON,
    LOG_LINES,
    OLD_BUG_FACTOR,
    RESTART_DOWNTIME_MIN,
    RESTART_MIN,
    ROLLBACK_MIN,
    SCALE_MAX_BONUS,
    SCALE_MIN,
    SCALE_STEP,
    SERVICES,
    RepairTool,
    RootCause,
    Service,
    World,
    make_world,
)

DURATIONS: dict[RepairTool, int] = {
    "restart": RESTART_MIN,
    "rollback": ROLLBACK_MIN,
    "clean_disk": CLEAN_DISK_MIN,
}
REPAIR_TOOLS: list[RepairTool] = ["restart", "rollback", "clean_disk"]
ROOT_CAUSES = {"bad_deploy", "cache_crash", "db_disk_full"}


@cache
def world_of(kind: RootCause, nth: int = 0) -> World:
    found = [w for w in (make_world(s) for s in range(60)) if w.fault_kind == kind]
    return found[nth]


def correct(world: World, start: int) -> Intervention:
    info = FAULTS[world.fault_kind]
    target = world.bad_deploy_id if info.repair_tool == "rollback" else info.service
    assert target is not None
    return Intervention(
        tool=info.repair_tool, target=target, start=start, duration=DURATIONS[info.repair_tool]
    )


def scale(service: Service, n: int, start: int) -> Intervention:
    return Intervention(tool="scale", target=service, start=start, duration=SCALE_MIN, n=n)


def good_deploy(world: World):
    return next(d for d in world.deploys if not d.is_bad)


@pytest.mark.parametrize("kind", FAULT_KINDS)
def test_do_nothing_availability(kind: RootCause) -> None:
    w = world_of(kind)
    for t in range(w.fault_start):
        assert availability(w, [], t) == 1.0
        assert not fault_active(w, [], t)
    for t in range(w.fault_start, HORIZON):
        assert availability(w, [], t) < 1.0
        assert fault_active(w, [], t)


@pytest.mark.parametrize("kind", FAULT_KINDS)
def test_same_code_path_for_series(kind: RootCause) -> None:
    w = world_of(kind)
    assert served_series(w, []) == [served(w, [], t) for t in range(HORIZON)]
    assert served_series(w, []) == served_series(w, [])
    # An intervention that changes nothing at all keeps the series bit-identical.
    noop = [scale("api", 1, 0)]
    assert served_series(w, noop) == served_series(w, [])


@pytest.mark.parametrize("kind", FAULT_KINDS)
def test_correct_repair_fixes_at_end(kind: RootCause) -> None:
    w = world_of(kind)
    m = w.fault_start + 7
    fix = correct(w, m)
    ivs = [fix]
    end = m + fix.duration
    assert fault_active(w, ivs, end - 1)
    assert not fault_active(w, ivs, end)
    assert availability(w, ivs, end) == 1.0
    assert probes_healthy(w, ivs)
    assert not probes_healthy(w, [])


@pytest.mark.parametrize("kind", FAULT_KINDS)
def test_wrong_repair_does_not_fix(kind: RootCause) -> None:
    w = world_of(kind)
    info = FAULTS[kind]
    wrong_tool: RepairTool = next(t for t in REPAIR_TOOLS if t != info.repair_tool)
    target = "api" if wrong_tool != "rollback" else good_deploy(w).id
    wrong = Intervention(
        tool=wrong_tool,
        target=target,
        start=w.fault_start + 5,
        duration=DURATIONS[wrong_tool],
    )
    assert fault_active(w, [wrong], HORIZON - 1)
    # Right tool, wrong target.
    other = next(s for s in SERVICES if s != info.service)
    if info.repair_tool != "rollback":
        off = Intervention(
            tool=info.repair_tool,
            target=other,
            start=w.fault_start + 5,
            duration=DURATIONS[info.repair_tool],
        )
        assert fault_active(w, [off], HORIZON - 1)


def test_repair_before_fault_does_not_prefix_fix() -> None:
    w = world_of("cache_crash")
    early = correct(w, 0)
    assert fault_active(w, [early], w.fault_start)
    assert classify_repair(w, [early], early) == "healthy_damage"


def test_second_correct_repair_is_wrong_and_first_fix_stands() -> None:
    w = world_of("db_disk_full")
    a, b = correct(w, w.fault_start + 1), correct(w, w.fault_start + 20)
    ivs = [a, b]
    assert classify_repair(w, ivs, a) == "correct"
    assert classify_repair(w, ivs, b) == "wrong"
    assert not fault_active(w, ivs, w.fault_start + 3)


def test_classify_repair_cases() -> None:
    w = world_of("bad_deploy")
    assert w.bad_deploy_id is not None
    good = good_deploy(w).id
    wrong_target = Intervention(
        tool="rollback", target=good, start=w.fault_start + 1, duration=ROLLBACK_MIN
    )
    wrong_tool = Intervention(
        tool="restart", target="api", start=w.fault_start + 1, duration=RESTART_MIN
    )
    assert classify_repair(w, [], correct(w, w.fault_start)) == "correct"
    assert classify_repair(w, [], wrong_target) == "wrong"
    assert classify_repair(w, [], wrong_tool) == "wrong"
    healthy = Intervention(tool="restart", target="db", start=0, duration=RESTART_MIN)
    assert classify_repair(w, [], healthy) == "healthy_damage"
    with pytest.raises(ValueError):
        classify_repair(w, [], scale("api", 2, 0))


def test_restart_api_and_db_downtime() -> None:
    w = world_of("db_disk_full")
    for service in ("api", "db"):
        iv = Intervention(tool="restart", target=service, start=0, duration=RESTART_MIN)
        for t in range(RESTART_DOWNTIME_MIN):
            assert service_factors(w, [iv], t)[service] == 0.0
            assert served(w, [iv], t) == 0.0
        assert service_factors(w, [iv], RESTART_DOWNTIME_MIN)[service] == 1.0
        assert availability(w, [iv], RESTART_DOWNTIME_MIN) == 1.0


def test_cold_cache_after_restarting_healthy_cache() -> None:
    w = world_of("bad_deploy")
    iv = Intervention(tool="restart", target="cache", start=0, duration=RESTART_MIN)
    end = RESTART_MIN
    assert service_factors(w, [iv], end - 1)["db"] == 1.0
    for t in range(end, end + COLD_CACHE_MIN):
        assert service_factors(w, [iv], t)["db"] == COLD_CACHE_FACTOR
    assert service_factors(w, [iv], end + COLD_CACHE_MIN)["db"] == 1.0
    assert probes_healthy(w, [iv]) is False  # bad_deploy is still active


def test_correct_cache_restart_has_no_cold_cache() -> None:
    w = world_of("cache_crash")
    fix = correct(w, w.fault_start + 2)
    end = fix.start + fix.duration
    for t in range(end, end + COLD_CACHE_MIN):
        assert service_factors(w, [fix], t)["db"] == 1.0


def test_restart_cache_after_fix_is_cold() -> None:
    w = world_of("cache_crash")
    fix = correct(w, w.fault_start + 2)
    again = Intervention(tool="restart", target="cache", start=fix.start + 10, duration=RESTART_MIN)
    assert classify_repair(w, [fix, again], again) == "wrong"
    assert service_factors(w, [fix, again], again.start + RESTART_MIN)["db"] == COLD_CACHE_FACTOR


def test_old_bug_after_good_rollback_lasts_to_horizon() -> None:
    w = world_of("db_disk_full")
    d = good_deploy(w)
    rb = Intervention(tool="rollback", target=d.id, start=w.fault_start + 1, duration=ROLLBACK_MIN)
    fix = correct(w, w.fault_start + 10)
    ivs = [rb, fix]
    end = rb.start + ROLLBACK_MIN
    assert (
        service_factors(w, ivs, end - 1)[d.service] == service_factors(w, [fix], end - 1)[d.service]
    )
    for t in (end, end + 30, HORIZON - 1):
        assert service_factors(w, [rb], t)[d.service] == OLD_BUG_FACTOR
    assert not fault_active(w, ivs, HORIZON - 1)
    assert not probes_healthy(w, ivs)
    assert classify_repair(w, ivs, rb) == "wrong"


def test_rollback_same_good_deploy_twice_applies_once() -> None:
    w = world_of("db_disk_full")
    d = good_deploy(w)
    a = Intervention(tool="rollback", target=d.id, start=1, duration=ROLLBACK_MIN)
    b = Intervention(tool="rollback", target=d.id, start=10, duration=ROLLBACK_MIN)
    assert service_factors(w, [a, b], 60)[d.service] == OLD_BUG_FACTOR


@pytest.mark.parametrize("kind", ["bad_deploy", "cache_crash"])
def test_scale_mitigates_at_most_30_percent(kind: RootCause) -> None:
    w = world_of(kind)
    info = FAULTS[kind]
    assert info.scale_mitigates is not None
    t = w.fault_start + 10
    base = service_factors(w, [], t)[info.scale_mitigates]
    lost = 1.0 - base
    previous = base
    for n in range(1, 6):
        iv = scale(info.scale_mitigates, n, w.fault_start)
        f = service_factors(w, [iv], t)[info.scale_mitigates]
        bonus = min(SCALE_MAX_BONUS, SCALE_STEP * (n - 1))
        assert f == pytest.approx(base + bonus * lost)
        assert f >= previous
        assert (f - base) <= SCALE_MAX_BONUS * lost + 1e-12
        previous = f
    # Not yet effective during the action itself.
    iv = scale(info.scale_mitigates, 5, w.fault_start)
    assert service_factors(w, [iv], w.fault_start)[info.scale_mitigates] == base
    assert fault_active(w, [iv], HORIZON - 1)


def test_scale_only_mitigates_the_right_service_and_active_fault() -> None:
    w = world_of("bad_deploy")
    t = w.fault_start + 5
    base = service_factors(w, [], t)
    for service in ("cache", "db"):
        assert service_factors(w, [scale(service, 5, 0)], t) == base
    assert service_factors(w, [scale("api", 5, 0)], w.fault_start - 1)["api"] == 1.0
    wd = world_of("db_disk_full")
    for service in SERVICES:
        assert service_factors(wd, [scale(service, 5, 0)], wd.fault_start + 5) == service_factors(
            wd, [], wd.fault_start + 5
        )


def test_later_scale_replaces_earlier() -> None:
    w = world_of("bad_deploy")
    a, b = scale("api", 5, w.fault_start), scale("api", 2, w.fault_start + 10)
    t = w.fault_start + 20
    assert service_factors(w, [a, b], t)["api"] == pytest.approx(
        FAULTS["bad_deploy"].effect_factor + SCALE_STEP * (1 - FAULTS["bad_deploy"].effect_factor)
    )


def test_instance_minutes() -> None:
    assert instance_minutes([]) == 0
    assert instance_minutes([scale("api", 1, 0)]) == 0
    # Scale ends at minute 1, runs to the horizon with 2 extra instances.
    assert instance_minutes([scale("api", 3, 0)]) == 2 * (HORIZON - 1)
    # Replaced at minute 10 (effective at 11) by n=2; other service independent.
    ivs = [scale("api", 5, 0), scale("api", 2, 10), scale("db", 2, 50)]
    assert instance_minutes(ivs) == 4 * (11 - 1) + 1 * (HORIZON - 11) + 1 * (HORIZON - 51)
    ivs = [scale("api", 5, 0), scale("api", 1, 10)]
    assert instance_minutes(ivs) == 4 * 10


@pytest.mark.parametrize("kind", FAULT_KINDS)
def test_fault_alert_within_five_minutes(kind: RootCause) -> None:
    for nth in range(3):
        w = world_of(kind, nth)
        names = {a.name for t in range(w.fault_start + 6) for a in alerts_at(w, [], t)}
        for t in range(w.fault_start):
            assert all(a.name == w.herring.kind for a in alerts_at(w, [], t))
        fault_alerts = [a for a in alerts_at(w, [], w.fault_start + 5) if a.name != w.herring.kind]
        assert fault_alerts
        assert min(a.started_at for a in fault_alerts) <= w.fault_start + 5
        assert names - {w.herring.kind}


def test_cache_crash_first_alert_is_db_alert() -> None:
    for nth in range(5):
        w = world_of("cache_crash", nth)
        first = None
        for t in range(w.fault_start, HORIZON):
            fault_alerts = [a for a in alerts_at(w, [], t) if a.name != w.herring.kind]
            if fault_alerts:
                first = min(fault_alerts, key=lambda a: (a.started_at, a.name))
                break
        assert first is not None
        assert first.service == "db"
        assert first.name == "db_cpu_high"


def test_alert_names_never_root_causes() -> None:
    for seed in range(30):
        w = make_world(seed)
        for t in range(HORIZON):
            for a in alerts_at(w, [], t):
                assert a.name not in ROOT_CAUSES
        for rc in ROOT_CAUSES:
            assert rc != w.herring.kind


def test_alerts_clear_after_fix() -> None:
    w = world_of("db_disk_full")
    fix = correct(w, w.fault_start + 3)
    end = fix.start + fix.duration
    later = [a for a in alerts_at(w, [fix], end + 3) if a.name != w.herring.kind]
    assert later == []
    assert any(a.name == "db_disk_usage_critical" for a in alerts_at(w, [fix], end - 1))


def test_alert_started_at_not_in_future() -> None:
    w = world_of("bad_deploy")
    for t in range(HORIZON):
        assert all(a.started_at <= t for a in alerts_at(w, [], t))


def test_metrics_shapes() -> None:
    w = world_of("cache_crash")
    t = w.fault_start + 5
    cache_m = metrics_at(w, [], "cache", t)
    assert cache_m.error_rate == 1.0
    assert cache_m.cpu == 0.0
    assert metrics_at(w, [], "db", t).cpu > 0.9
    assert metrics_at(w, [], "api", t).latency_p99_ms > 400
    wd = world_of("db_disk_full")
    t = wd.fault_start + 5
    assert metrics_at(wd, [], "db", t).disk_pct > 0.95
    assert metrics_at(wd, [], "db", t).error_rate > 0.5
    wb = world_of("bad_deploy")
    api = metrics_at(wb, [], "api", wb.fault_start + 5)
    assert api.error_rate == pytest.approx(1 - availability(wb, [], wb.fault_start + 5), abs=0.05)
    for w2 in (w, wd, wb):
        for s in SERVICES:
            for tt in range(HORIZON):
                m = metrics_at(w2, [], s, tt)
                assert 0 <= m.error_rate <= 1
                assert 0 <= m.cpu <= 1
                assert 0 <= m.disk_pct <= 1
                assert m.latency_p99_ms >= 0


def test_herring_has_no_effect_on_served() -> None:
    w = world_of("bad_deploy")
    h = w.herring
    t = h.start
    if t < w.fault_start:
        assert availability(w, [], t) == 1.0
    assert any(a.name == h.kind for a in alerts_at(w, [], h.start))
    assert not any(a.name == h.kind for a in alerts_at(w, [], h.start + h.duration))


def test_visible_deploys_no_leak() -> None:
    for kind in ("bad_deploy",):
        for nth in range(3):
            w = world_of(kind, nth)
            before = visible_deploys(w, w.fault_start - 1)
            assert w.bad_deploy_id not in {d.id for d in before}
            after = visible_deploys(w, w.fault_start)
            assert w.bad_deploy_id in {d.id for d in after}
    w = world_of("bad_deploy")
    assert all("is_bad" not in d.model_dump() for d in visible_deploys(w, HORIZON))
    assert all(d.minute <= 10 for d in visible_deploys(w, 10))


def test_logs_evidence_lines() -> None:
    w = world_of("bad_deploy")
    t = w.fault_start + 3
    lines = logs_between(w, [], "api", 0, t)
    errors = [ln for ln in lines if ln.level == "ERROR"]
    assert errors
    assert all(f"build {w.bad_deploy_id}" in ln.msg and ln.minute >= w.fault_start for ln in errors)
    assert not any(ln.level == "ERROR" for ln in logs_between(w, [], "api", 0, w.fault_start))

    wc = world_of("cache_crash")
    cl = logs_between(wc, [], "cache", 0, HORIZON)
    fatal = [ln for ln in cl if ln.level == "FATAL"]
    assert len(fatal) == 1
    assert fatal[0].minute == wc.fault_start
    assert not [ln for ln in cl if ln.minute >= wc.fault_start and ln.level == "INFO"]
    assert any("too many connections" in ln.msg for ln in logs_between(wc, [], "db", 0, HORIZON))

    wd = world_of("db_disk_full")
    assert any("No space left" in ln.msg for ln in logs_between(wd, [], "db", 0, HORIZON))
    assert not any("No space left" in ln.msg for ln in logs_between(wd, [], "api", 0, HORIZON))


def test_logs_since_filter_and_cap() -> None:
    w = world_of("db_disk_full")
    all_lines = logs_between(w, [], "db", 0, HORIZON)
    assert len(all_lines) == LOG_LINES
    assert all_lines == sorted(all_lines, key=lambda ln: ln.minute)
    some = logs_between(w, [], "db", 5, 9)
    assert some
    assert all(5 <= ln.minute < 9 for ln in some)
    assert logs_between(w, [], "db", 9, 9) == []
    assert logs_between(w, [], "api", 0, 3) != logs_between(w, [], "db", 0, 3)


def test_logs_herring_and_side_effects() -> None:
    w = world_of("db_disk_full")
    h = w.herring
    herring_lines = [
        ln for ln in logs_between(w, [], h.service, h.start, h.start + 1) if ln.level == "WARN"
    ]
    assert herring_lines
    iv = Intervention(tool="restart", target="api", start=0, duration=RESTART_MIN)
    msgs = [ln.msg for ln in logs_between(w, [iv], "api", 0, RESTART_MIN + 1)]
    assert "restarting api" in msgs and "api restarted" in msgs
    d = good_deploy(w)
    rb = Intervention(tool="rollback", target=d.id, start=0, duration=ROLLBACK_MIN)
    rolled = [ln.msg for ln in logs_between(w, [rb], d.service, 0, ROLLBACK_MIN + 1)]
    assert any(d.id in m for m in rolled)
    sc = scale("api", 3, 0)
    assert any("3 instances" in ln.msg for ln in logs_between(w, [sc], "api", 0, 3))


def test_logs_cache_resumes_after_restart() -> None:
    w = world_of("cache_crash")
    fix = correct(w, w.fault_start + 2)
    end = fix.start + fix.duration
    lines = logs_between(w, [fix], "cache", end, end + 2)
    assert any(ln.level == "INFO" for ln in lines)


def test_logs_do_not_name_hidden_things() -> None:
    forbidden = ["bad_deploy", "cache_crash", "db_disk_full", "fault_kind", "herring", "is_bad"]
    for seed in range(30):
        w = make_world(seed)
        for s in SERVICES:
            for ln in logs_between(w, [], s, 0, HORIZON):
                assert not any(f in ln.msg for f in forbidden)


def test_next_alert_start() -> None:
    for seed in range(10):
        w = make_world(seed)
        first = next_alert_start(w, [], -1, HORIZON - 1)
        assert first is not None
        assert first <= min(w.herring.start, w.fault_start + 5)
        assert next_alert_start(w, [], w.herring.start - 1, w.herring.start) == w.herring.start
        fault_first = min(
            a.started_at
            for t in range(HORIZON)
            for a in alerts_at(w, [], t)
            if a.name != w.herring.kind
        )
        assert next_alert_start(w, [], fault_first - 1, fault_first) == fault_first
        assert next_alert_start(w, [], fault_first, fault_first) is None
        assert next_alert_start(w, [], HORIZON, HORIZON + 5) is None


def test_next_alert_start_matches_alerts_at() -> None:
    w = world_of("cache_crash")
    started: list[int] = []
    seen: set[tuple[str, int]] = set()
    for t in range(HORIZON):
        for a in alerts_at(w, [], t):
            if (a.name, a.started_at) not in seen:
                seen.add((a.name, a.started_at))
                started.append(a.started_at)
    cursor = -1
    found: list[int] = []
    while (m := next_alert_start(w, [], cursor, HORIZON - 1)) is not None:
        found.append(m)
        cursor = m
    assert found == sorted(set(started))


def test_probes_healthy_cases() -> None:
    w = world_of("bad_deploy")
    assert not probes_healthy(w, [])
    fix = correct(w, w.fault_start)
    assert probes_healthy(w, [fix])
    late_restart = Intervention(
        tool="restart", target="api", start=HORIZON - 2, duration=RESTART_MIN
    )
    assert not probes_healthy(w, [fix, late_restart])
    healthy_world = world_of("db_disk_full")
    assert probes_healthy(healthy_world, [correct(healthy_world, healthy_world.fault_start)])


def test_oracle_beats_nothing_and_agent_series_uses_same_path() -> None:
    for kind in FAULT_KINDS:
        w = world_of(kind)
        nothing = sum(served_series(w, []))
        oracle = sum(served_series(w, [correct(w, w.fault_start)]))
        assert oracle > nothing
        assert math.isclose(
            sum(served_series(w, [correct(w, w.fault_start)])),
            sum(served_series(w, [correct(w, w.fault_start)])),
            abs_tol=0.0,
        )


def test_purity() -> None:
    w = world_of("bad_deploy")
    d = good_deploy(w)
    ivs = [
        Intervention(tool="rollback", target=d.id, start=1, duration=ROLLBACK_MIN),
        scale("api", 3, 10),
        correct(w, w.fault_start + 4),
    ]
    snap_w, snap_i = w.model_dump(), [iv.model_dump() for iv in ivs]
    t = w.fault_start + 6

    def run() -> list[object]:
        return [
            fault_active(w, ivs, t),
            service_factors(w, ivs, t),
            availability(w, ivs, t),
            served(w, ivs, t),
            served_series(w, ivs),
            [metrics_at(w, ivs, s, t) for s in SERVICES],
            alerts_at(w, ivs, t),
            [logs_between(w, ivs, s, 0, t) for s in SERVICES],
            visible_deploys(w, t),
            next_alert_start(w, ivs, 0, HORIZON - 1),
            probes_healthy(w, ivs),
            instance_minutes(ivs),
            classify_repair(w, ivs, ivs[0]),
        ]

    assert run() == run()
    assert w.model_dump() == snap_w
    assert [iv.model_dump() for iv in ivs] == snap_i


def test_episode_state_roundtrip() -> None:
    s = EpisodeState()
    s.interventions.append(Intervention(tool="restart", target="api", start=0, duration=3))
    assert EpisodeState.model_validate(s.model_dump()) == s
    assert EpisodeState().interventions is not s.interventions
