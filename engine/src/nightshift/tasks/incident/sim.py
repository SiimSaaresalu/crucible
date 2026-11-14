"""Pure simulation of `incident@1`.

Every function is pure over `(world, interventions, ...)`: no mutation, no randomness, no
rounding. The agent run, the do-nothing replay and the oracle replay all go through the same
code (`served_series`), so float sums are bit-identical where the inputs are.

Conventions:
- Time is an integer minute `0 ... HORIZON - 1`.
- A fault is active from `fault_start` until `start + duration` of the first correct repair
  that started while it was active.
- An alert's `started_at` is the minute it first fires (after its `for` hold), so
  `alerts_at(t)` only holds alerts with `started_at <= t`.
- Log lines are deterministic functions of `(seed, service, minute)`.
"""

from collections.abc import Sequence
from typing import Final, Literal, NamedTuple

from nightshift.tasks.incident.state import (
    Alert,
    DeployView,
    Intervention,
    LogLevel,
    LogLine,
    ServiceMetrics,
)
from nightshift.tasks.incident.world import (
    ALERT_API_ERROR_RATE,
    ALERT_API_ERROR_RATE_HOLD,
    ALERT_API_LATENCY_HOLD,
    ALERT_API_LATENCY_MS,
    ALERT_DB_CPU,
    ALERT_DB_CPU_HOLD,
    ALERT_DB_DISK,
    ALERT_DB_DISK_HOLD,
    ALERT_DB_WRITE_ERROR_HOLD,
    ALERT_DB_WRITE_ERROR_RATE,
    API_OVERLOAD_LATENCY_X,
    BASE_CPU,
    BASE_DISK,
    BASE_ERROR_RATE,
    BASE_LATENCY_MS,
    COLD_CACHE_FACTOR,
    COLD_CACHE_MIN,
    CRASHED_ERROR_RATE,
    DB_OVERLOAD_CPU,
    DB_OVERLOAD_LATENCY_X,
    DISK_FULL_ERROR_RATE,
    DISK_FULL_PCT,
    FAULTS,
    HERRING_LATENCY_X,
    HORIZON,
    LATENCY_NOISE_GAIN,
    LOG_LINES,
    OLD_BUG_FACTOR,
    RESTART_DOWNTIME_MIN,
    SCALE_MAX_BONUS,
    SCALE_STEP,
    SERVICES,
    HerringKind,
    MetricName,
    Service,
    World,
)

__all__ = [
    "alerts_at",
    "availability",
    "classify_repair",
    "fault_active",
    "instance_minutes",
    "logs_between",
    "metrics_at",
    "next_alert_start",
    "probes_healthy",
    "served",
    "served_series",
    "service_factors",
    "visible_deploys",
]

RepairClass = Literal["correct", "healthy_damage", "wrong"]

_INFO_LINES: Final[dict[Service, list[str]]] = {
    "api": [
        "GET /health 200",
        "request completed in 41 ms",
        "GET /cart 200",
        "POST /checkout 200",
        "worker heartbeat ok",
    ],
    "cache": [
        "hit ratio steady",
        "slab rebalance finished",
        "client connections stable",
        "keyspace snapshot ok",
    ],
    "db": [
        "checkpoint complete",
        "autovacuum finished",
        "connection pool stable",
        "replication lag 0 ms",
    ],
}

_HERRING_LINES: Final[dict[HerringKind, str]] = {
    "cache_eviction_burst": "eviction rate elevated, 4200 evictions/s",
    "api_latency_spike": "slow responses: p99 above 300 ms on /search",
    "db_slow_query": "slow query detected: 2300 ms on orders scan",
}


class _Status(NamedTuple):
    factors: dict[Service, float]
    active: bool
    restarting: frozenset[Service]


class _Rule(NamedTuple):
    name: str
    service: Service
    metric: MetricName
    threshold: float
    inclusive: bool
    hold: int


_RULES: Final[list[_Rule]] = [
    _Rule(
        "api_error_rate_high",
        "api",
        "error_rate",
        ALERT_API_ERROR_RATE,
        False,
        ALERT_API_ERROR_RATE_HOLD,
    ),
    _Rule(
        "api_latency_high",
        "api",
        "latency_p99_ms",
        ALERT_API_LATENCY_MS,
        False,
        ALERT_API_LATENCY_HOLD,
    ),
    _Rule("db_cpu_high", "db", "cpu", ALERT_DB_CPU, False, ALERT_DB_CPU_HOLD),
    _Rule("db_disk_usage_critical", "db", "disk_pct", ALERT_DB_DISK, True, ALERT_DB_DISK_HOLD),
    _Rule(
        "db_write_errors",
        "db",
        "error_rate",
        ALERT_DB_WRITE_ERROR_RATE,
        False,
        ALERT_DB_WRITE_ERROR_HOLD,
    ),
]


def _fault_target(world: World) -> str:
    info = FAULTS[world.fault_kind]
    if info.repair_tool == "rollback":
        return world.bad_deploy_id or ""
    return info.service


def _fix_time(world: World, interventions: Sequence[Intervention]) -> int | None:
    """End minute of the first correct repair that started while the fault was active."""
    info = FAULTS[world.fault_kind]
    target = _fault_target(world)
    ends = [
        iv.start + iv.duration
        for iv in interventions
        if iv.tool == info.repair_tool and iv.target == target and iv.start >= world.fault_start
    ]
    return min(ends) if ends else None


def _active_at(world: World, fix: int | None, t: int) -> bool:
    return t >= world.fault_start and (fix is None or t < fix)


def fault_active(world: World, interventions: Sequence[Intervention], t: int) -> bool:
    """True if the fault has started and no correct repair has finished by minute `t`."""
    return _active_at(world, _fix_time(world, interventions), t)


def _instances(interventions: Sequence[Intervention], service: Service, t: int) -> int:
    """Instance count of `service` at minute `t` (the latest finished `scale`, default 1)."""
    best_end = -1
    count = 1
    for iv in interventions:
        end = iv.start + iv.duration
        if iv.tool == "scale" and iv.target == service and end <= t and end >= best_end:
            best_end = end
            count = iv.n if iv.n is not None else 1
    return count


def instance_minutes(interventions: Sequence[Intervention]) -> int:
    """Scale cost to the horizon: `n - 1` instance-minutes per minute, per service.

    A `scale` counts from its end until the end of the next `scale` of the same service,
    or the horizon.
    """
    total = 0
    for service in SERVICES:
        scales = sorted(
            (iv for iv in interventions if iv.tool == "scale" and iv.target == service),
            key=lambda iv: iv.start + iv.duration,
        )
        for i, iv in enumerate(scales):
            begin = min(iv.start + iv.duration, HORIZON)
            end = HORIZON
            if i + 1 < len(scales):
                nxt = scales[i + 1]
                end = min(nxt.start + nxt.duration, HORIZON)
            n = iv.n if iv.n is not None else 1
            total += max(0, n - 1) * max(0, end - begin)
    return total


def _status(world: World, interventions: Sequence[Intervention], t: int) -> _Status:
    info = FAULTS[world.fault_kind]
    fix = _fix_time(world, interventions)
    active = _active_at(world, fix, t)
    factors: dict[Service, float] = {s: 1.0 for s in SERVICES}
    restarting: set[Service] = set()

    if active:
        eff = info.effect_factor
        if info.scale_mitigates == info.effect_service:
            n = _instances(interventions, info.effect_service, t)
            bonus = min(SCALE_MAX_BONUS, SCALE_STEP * (n - 1))
            eff = eff + bonus * (1.0 - eff)
        factors[info.effect_service] *= eff

    rolled_back: set[str] = set()
    for iv in interventions:
        end = iv.start + iv.duration
        if iv.tool == "restart":
            service = _as_service(iv.target)
            if service is None:
                continue
            if service != "cache":
                if iv.start <= t < iv.start + RESTART_DOWNTIME_MIN:
                    factors[service] *= 0.0
                    restarting.add(service)
            else:
                cache_was_down = world.fault_kind == "cache_crash" and _active_at(
                    world, fix, iv.start
                )
                if not cache_was_down and end <= t < end + COLD_CACHE_MIN:
                    factors["db"] *= COLD_CACHE_FACTOR
        elif iv.tool == "rollback" and iv.target not in rolled_back:
            deploy = next((d for d in world.deploys if d.id == iv.target), None)
            if deploy is not None and not deploy.is_bad:
                rolled_back.add(iv.target)
                first_end = min(
                    i.start + i.duration
                    for i in interventions
                    if i.tool == "rollback" and i.target == iv.target
                )
                if first_end <= t:
                    factors[deploy.service] *= OLD_BUG_FACTOR
    return _Status(factors, active, frozenset(restarting))


def _as_service(name: str) -> Service | None:
    for s in SERVICES:
        if s == name:
            return s
    return None


def service_factors(
    world: World, interventions: Sequence[Intervention], t: int
) -> dict[Service, float]:
    """Per-service serving factor in `[0, 1]` at minute `t`."""
    return _status(world, interventions, t).factors


def _availability(factors: dict[Service, float]) -> float:
    result = 1.0
    for s in SERVICES:
        result *= factors[s]
    return result


def availability(world: World, interventions: Sequence[Intervention], t: int) -> float:
    """Product of the service factors at minute `t`."""
    return _availability(service_factors(world, interventions, t))


def served(world: World, interventions: Sequence[Intervention], t: int) -> float:
    """Requests served in minute `t`."""
    return world.demand[t] * availability(world, interventions, t)


def served_series(world: World, interventions: Sequence[Intervention]) -> list[float]:
    """Requests served in each minute of the horizon. Do-nothing is `interventions=[]`."""
    return [served(world, interventions, t) for t in range(HORIZON)]


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _metrics(world: World, status: _Status, service: Service, t: int) -> ServiceMetrics:
    noise = world.noise[service]
    kind = world.fault_kind
    active = status.active
    cache_down = active and kind == "cache_crash"
    db_overload = active and kind == "cache_crash"
    disk_full = active and kind == "db_disk_full"

    error = BASE_ERROR_RATE[service] + noise.error_rate[t]
    latency = BASE_LATENCY_MS[service] * (1.0 + noise.latency_p99_ms[t] * LATENCY_NOISE_GAIN)
    cpu = BASE_CPU[service] + noise.cpu[t]
    disk = BASE_DISK[service] + noise.disk_pct[t]

    if service == "api":
        error += 1.0 - _availability(status.factors)
        if db_overload:
            latency *= API_OVERLOAD_LATENCY_X
    elif service == "cache" and cache_down:
        error, cpu, latency = CRASHED_ERROR_RATE, 0.0, 0.0
    elif service == "db":
        if db_overload:
            cpu = DB_OVERLOAD_CPU + noise.cpu[t]
            latency *= DB_OVERLOAD_LATENCY_X
        if disk_full:
            disk = DISK_FULL_PCT + noise.disk_pct[t]
            error = DISK_FULL_ERROR_RATE + noise.error_rate[t]

    if service in status.restarting:
        error, cpu = CRASHED_ERROR_RATE, 0.0

    herring = world.herring
    if service == herring.service and herring.start <= t < herring.start + herring.duration:
        latency *= HERRING_LATENCY_X

    return ServiceMetrics(
        error_rate=_clamp(error),
        latency_p99_ms=max(0.0, latency),
        cpu=_clamp(cpu),
        disk_pct=_clamp(disk),
    )


def metrics_at(
    world: World, interventions: Sequence[Intervention], service: Service, t: int
) -> ServiceMetrics:
    """Public metrics of `service` at minute `t`."""
    return _metrics(world, _status(world, interventions, t), service, t)


def _series(
    world: World, interventions: Sequence[Intervention], upto: int
) -> list[dict[Service, ServiceMetrics]]:
    out: list[dict[Service, ServiceMetrics]] = []
    for t in range(min(upto, HORIZON - 1) + 1):
        status = _status(world, interventions, t)
        out.append({s: _metrics(world, status, s, t) for s in SERVICES})
    return out


def _metric_value(m: ServiceMetrics, name: MetricName) -> float:
    if name == "error_rate":
        return m.error_rate
    if name == "latency_p99_ms":
        return m.latency_p99_ms
    if name == "cpu":
        return m.cpu
    return m.disk_pct


def _holds(rule: _Rule, m: ServiceMetrics) -> bool:
    value = _metric_value(m, rule.metric)
    return value >= rule.threshold if rule.inclusive else value > rule.threshold


def _alerts_from_series(
    world: World, series: list[dict[Service, ServiceMetrics]], t: int
) -> list[Alert]:
    alerts: list[Alert] = []
    for rule in _RULES:
        run = 0
        while run <= t and _holds(rule, series[t - run][rule.service]):
            run += 1
        if run >= rule.hold + 1:
            started = t - run + 1 + rule.hold
            alerts.append(Alert(name=rule.name, service=rule.service, started_at=started))
    h = world.herring
    if h.start <= t < h.start + h.duration:
        alerts.append(Alert(name=h.kind, service=h.service, started_at=h.start))
    return sorted(alerts, key=lambda a: (a.started_at, a.name))


def alerts_at(world: World, interventions: Sequence[Intervention], t: int) -> list[Alert]:
    """Alerts firing at minute `t`, oldest first."""
    return _alerts_from_series(world, _series(world, interventions, t), t)


def next_alert_start(
    world: World, interventions: Sequence[Intervention], after: int, until: int
) -> int | None:
    """First minute `m` with `after < m <= until` at which an alert starts firing, or None."""
    series = _series(world, interventions, until)
    for m in range(max(after + 1, 0), min(until, HORIZON - 1) + 1):
        if any(a.started_at == m for a in _alerts_from_series(world, series, m)):
            return m
    return None


def visible_deploys(world: World, t: int) -> list[DeployView]:
    """Deploys with `minute <= t`, oldest first. Never carries the hidden `is_bad` flag."""
    visible = sorted((d for d in world.deploys if d.minute <= t), key=lambda d: d.minute)
    return [DeployView(id=d.id, service=d.service, minute=d.minute) for d in visible]


def _info_line(world: World, service: Service, minute: int) -> str:
    templates = _INFO_LINES[service]
    return templates[(world.seed * 31 + minute * 7 + SERVICES.index(service)) % len(templates)]


def logs_between(
    world: World,
    interventions: Sequence[Intervention],
    service: Service,
    since: int,
    until: int,
) -> list[LogLine]:
    """The last `LOG_LINES` log lines of `service` with `since <= minute < until`."""
    fix = _fix_time(world, interventions)
    kind = world.fault_kind
    herring = world.herring
    lines: list[LogLine] = []

    def add(minute: int, level: LogLevel, msg: str) -> None:
        lines.append(LogLine(minute=minute, level=level, msg=msg))

    for minute in range(max(since, 0), min(until, HORIZON)):
        active = _active_at(world, fix, minute)
        crashed = active and kind == "cache_crash" and service == "cache"
        if crashed:
            if minute == world.fault_start:
                add(minute, "FATAL", "out of memory, process exited")
        else:
            add(minute, "INFO", _info_line(world, service, minute))
        if active and kind == "bad_deploy" and service == "api":
            add(minute, "ERROR", f"500 in checkout handler (build {world.bad_deploy_id})")
        if active and kind == "cache_crash" and service == "db":
            add(minute, "WARN", "too many connections")
        if active and kind == "db_disk_full" and service == "db":
            add(minute, "ERROR", "could not write block: No space left on device")
        if (
            service == herring.service
            and herring.start <= minute < herring.start + herring.duration
        ):
            add(minute, "WARN", _HERRING_LINES[herring.kind])
        for iv in interventions:
            end = iv.start + iv.duration
            if iv.tool == "restart" and iv.target == service:
                if minute == iv.start:
                    add(minute, "INFO", f"restarting {service}")
                if minute == end:
                    add(minute, "INFO", f"{service} restarted")
            elif iv.tool == "rollback" and minute == end:
                deploy = next((d for d in world.deploys if d.id == iv.target), None)
                if deploy is not None and deploy.service == service:
                    add(minute, "INFO", f"rolled back to build preceding {deploy.id}")
            elif iv.tool == "clean_disk" and iv.target == service and minute == end:
                add(minute, "INFO", "disk cleanup finished")
            elif iv.tool == "scale" and iv.target == service and minute == end:
                n = iv.n if iv.n is not None else 1
                add(minute, "INFO", f"scaled to {n} instances")
    return lines[-LOG_LINES:]


def probes_healthy(world: World, interventions: Sequence[Intervention]) -> bool:
    """End probes at the last minute: no fault active, every factor exactly 1.0."""
    t = HORIZON - 1
    status = _status(world, interventions, t)
    return not status.active and all(status.factors[s] == 1.0 for s in SERVICES)


def classify_repair(
    world: World, interventions: Sequence[Intervention], intervention: Intervention
) -> RepairClass:
    """Classify a repair action (`restart`, `rollback`, `clean_disk`) at its start.

    `correct`: the fault is active at the start and tool and target match the catalogue.
    `healthy_damage`: the fault has not started yet.
    `wrong`: everything else.
    """
    if intervention.tool == "scale":
        raise ValueError("scale is a mitigation, not a repair")
    if intervention.start < world.fault_start:
        return "healthy_damage"
    info = FAULTS[world.fault_kind]
    matches = intervention.tool == info.repair_tool and intervention.target == _fault_target(world)
    if matches and fault_active(world, interventions, intervention.start):
        return "correct"
    return "wrong"
