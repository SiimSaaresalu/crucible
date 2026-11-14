"""Hidden world of `incident@1`: constants, fault catalogue and the seeded `World`.

Everything random is drawn here, once, from the exogenous stream. The world is hidden:
never put it, or any field of it, in a `ToolResult`.
"""

import math
from typing import Final, Literal, get_args

from pydantic import BaseModel, ConfigDict

from nightshift.core.rng import RngStreams

__all__ = [
    "ALERT_API_ERROR_RATE",
    "ALERT_API_ERROR_RATE_HOLD",
    "ALERT_API_LATENCY_HOLD",
    "ALERT_API_LATENCY_MS",
    "ALERT_DB_CPU",
    "ALERT_DB_CPU_HOLD",
    "ALERT_DB_DISK",
    "ALERT_DB_DISK_HOLD",
    "ALERT_DB_WRITE_ERROR_HOLD",
    "ALERT_DB_WRITE_ERROR_RATE",
    "API_OVERLOAD_LATENCY_X",
    "BASE_CPU",
    "BASE_DISK",
    "BASE_ERROR_RATE",
    "BASE_LATENCY_MS",
    "BASE_RPM",
    "CLEAN_DISK_MIN",
    "COLD_CACHE_FACTOR",
    "COLD_CACHE_MIN",
    "CRASHED_ERROR_RATE",
    "DB_OVERLOAD_CPU",
    "DB_OVERLOAD_LATENCY_X",
    "DEMAND_AMPLITUDE",
    "DEMAND_NOISE",
    "DISK_FULL_ERROR_RATE",
    "DISK_FULL_PCT",
    "FAULTS",
    "FAULT_KINDS",
    "FAULT_START_RANGE",
    "HERRING_KINDS",
    "HERRING_LATENCY_X",
    "HERRING_MARGIN",
    "HORIZON",
    "LATENCY_NOISE_GAIN",
    "LOG_LINES",
    "MAX_INSTANCES",
    "MAX_WAIT",
    "METRIC_NAMES",
    "METRIC_NOISE",
    "OLD_BUG_FACTOR",
    "READ_MIN",
    "RESTART_DOWNTIME_MIN",
    "RESTART_MIN",
    "ROLLBACK_MIN",
    "SCALE_MAX_BONUS",
    "SCALE_MIN",
    "SCALE_STEP",
    "SERVICES",
    "TOPOLOGY_EDGES",
    "Deploy",
    "FaultInfo",
    "Herring",
    "HerringKind",
    "MetricName",
    "MetricNoise",
    "RepairTool",
    "RootCause",
    "Service",
    "World",
    "make_world",
]

Service = Literal["api", "cache", "db"]
RootCause = Literal["bad_deploy", "cache_crash", "db_disk_full"]
HerringKind = Literal["cache_eviction_burst", "api_latency_spike", "db_slow_query"]
RepairTool = Literal["restart", "rollback", "clean_disk"]
MetricName = Literal["error_rate", "latency_p99_ms", "cpu", "disk_pct"]

SERVICES: Final[list[Service]] = list(get_args(Service))
FAULT_KINDS: Final[list[RootCause]] = list(get_args(RootCause))
HERRING_KINDS: Final[list[HerringKind]] = list(get_args(HerringKind))
METRIC_NAMES: Final[list[MetricName]] = list(get_args(MetricName))

# Time and demand.
HORIZON: Final = 120
BASE_RPM: Final = 1000.0
DEMAND_AMPLITUDE: Final = 0.15
DEMAND_NOISE: Final = 0.05
METRIC_NOISE: Final = 0.02
FAULT_START_RANGE: Final = (15, 45)
HERRING_START_RANGE: Final = (5, 110)
HERRING_DURATION_RANGE: Final = (5, 10)
HERRING_MARGIN: Final = 3  # herring start is more than this many minutes from fault_start

# Tool durations (minutes) and limits.
READ_MIN: Final = 1
RESTART_MIN: Final = 3
ROLLBACK_MIN: Final = 5
CLEAN_DISK_MIN: Final = 2
SCALE_MIN: Final = 1
MAX_WAIT: Final = 30
LOG_LINES: Final = 20
MAX_INSTANCES: Final = 5

# Side effects of repairs.
RESTART_DOWNTIME_MIN: Final = 3  # restart api/db: factor 0 for this long
COLD_CACHE_FACTOR: Final = 0.85  # db factor after restarting a healthy cache
COLD_CACHE_MIN: Final = 3
OLD_BUG_FACTOR: Final = 0.92  # rolled-back good deploy: service factor until the horizon

# Scale mitigation: factor f becomes f + min(SCALE_MAX_BONUS, SCALE_STEP * (n - 1)) * (1 - f).
SCALE_STEP: Final = 0.10
SCALE_MAX_BONUS: Final = 0.30

# Topology: api calls cache; on a cache miss or cache down, api calls db.
TOPOLOGY_EDGES: Final[list[tuple[Service, Service]]] = [
    ("api", "cache"),
    ("cache", "db"),
    ("api", "db"),
]

# Healthy metric baselines (noise from the world is added on top).
BASE_ERROR_RATE: Final[dict[Service, float]] = {"api": 0.01, "cache": 0.002, "db": 0.002}
BASE_LATENCY_MS: Final[dict[Service, float]] = {"api": 180.0, "cache": 4.0, "db": 35.0}
BASE_CPU: Final[dict[Service, float]] = {"api": 0.40, "cache": 0.20, "db": 0.35}
BASE_DISK: Final[dict[Service, float]] = {"api": 0.30, "cache": 0.10, "db": 0.55}
LATENCY_NOISE_GAIN: Final = 10.0  # latency = base * (1 + noise * gain)

# Metric shapes while a fault or side effect is active.
CRASHED_ERROR_RATE: Final = 1.0  # crashed or restarting service
DB_OVERLOAD_CPU: Final = 0.97
DB_OVERLOAD_LATENCY_X: Final = 3.0
API_OVERLOAD_LATENCY_X: Final = 4.0  # api latency while the db is overloaded
DISK_FULL_PCT: Final = 1.0
DISK_FULL_ERROR_RATE: Final = 0.6  # db error rate when writes fail
HERRING_LATENCY_X: Final = 1.5  # small latency blip on the herring service

# Alert rules (metric threshold and `for` hold in minutes, like Prometheus).
ALERT_API_ERROR_RATE: Final = 0.05
ALERT_API_ERROR_RATE_HOLD: Final = 2
ALERT_API_LATENCY_MS: Final = 400.0
ALERT_API_LATENCY_HOLD: Final = 2
ALERT_DB_CPU: Final = 0.90
ALERT_DB_CPU_HOLD: Final = 1
ALERT_DB_DISK: Final = 0.95
ALERT_DB_DISK_HOLD: Final = 0
ALERT_DB_WRITE_ERROR_RATE: Final = 0.10
ALERT_DB_WRITE_ERROR_HOLD: Final = 1


class FaultInfo(BaseModel):
    """One catalogue entry: what breaks, what fixes it, what scaling can mitigate."""

    model_config = ConfigDict(frozen=True)

    root_cause: RootCause
    service: Service  # the faulty service
    repair_tool: RepairTool
    effect_service: Service  # service whose factor drops while the fault is active
    effect_factor: float
    service_down: bool  # the faulty service itself is down (cache_crash)
    scale_mitigates: Service | None


FAULTS: Final[dict[RootCause, FaultInfo]] = {
    "bad_deploy": FaultInfo(
        root_cause="bad_deploy",
        service="api",
        repair_tool="rollback",
        effect_service="api",
        effect_factor=0.45,
        service_down=False,
        scale_mitigates="api",
    ),
    "cache_crash": FaultInfo(
        root_cause="cache_crash",
        service="cache",
        repair_tool="restart",
        effect_service="db",
        effect_factor=0.50,
        service_down=True,
        scale_mitigates="db",
    ),
    "db_disk_full": FaultInfo(
        root_cause="db_disk_full",
        service="db",
        repair_tool="clean_disk",
        effect_service="db",
        effect_factor=0.35,
        service_down=False,
        scale_mitigates=None,
    ),
}


class Deploy(BaseModel):
    """A deploy. `minute` is negative for deploys before the episode. `is_bad` is hidden."""

    model_config = ConfigDict(frozen=True)

    id: str
    service: Service
    minute: int
    is_bad: bool = False


class Herring(BaseModel):
    """A red-herring alert: noise in logs and metrics, no effect on served requests."""

    model_config = ConfigDict(frozen=True)

    kind: HerringKind
    service: Service
    start: int
    duration: int


class MetricNoise(BaseModel):
    """Per-minute noise for one service, one list of `HORIZON` values per metric."""

    model_config = ConfigDict(frozen=True)

    error_rate: list[float]
    latency_p99_ms: list[float]
    cpu: list[float]
    disk_pct: list[float]


class World(BaseModel):
    """The whole hidden world of one episode, fixed by the seed."""

    model_config = ConfigDict(frozen=True)

    seed: int
    fault_kind: RootCause
    fault_start: int
    demand: list[float]
    noise: dict[Service, MetricNoise]
    deploys: list[Deploy]
    herring: Herring

    @property
    def bad_deploy_id(self) -> str | None:
        """Id of the bad deploy, or None if the fault is not `bad_deploy`."""
        return next((d.id for d in self.deploys if d.is_bad), None)


def _deploy_id(rng: RngStreams, used: set[str]) -> str:
    while True:
        candidate = f"d-{rng.exogenous.getrandbits(16):04x}"
        if candidate not in used:
            used.add(candidate)
            return candidate


def make_world(seed: int, salt: str | None = None) -> World:
    """Build the world for `seed` (and `salt`). Draw order is fixed; a test pins it."""
    rng = RngStreams(seed, salt)
    ex = rng.exogenous

    fault_kind = ex.choice(FAULT_KINDS)
    fault_start = ex.randint(*FAULT_START_RANGE)

    phase = ex.uniform(0, 2 * math.pi)
    demand = [
        BASE_RPM
        * (1 + DEMAND_AMPLITUDE * math.sin(2 * math.pi * t / HORIZON + phase))
        * (1 + ex.uniform(-DEMAND_NOISE, DEMAND_NOISE))
        for t in range(HORIZON)
    ]

    cols: dict[Service, dict[MetricName, list[float]]] = {
        s: {m: [] for m in METRIC_NAMES} for s in SERVICES
    }
    for _ in range(HORIZON):
        for s in SERVICES:
            for m in METRIC_NAMES:
                cols[s][m].append(ex.uniform(-METRIC_NOISE, METRIC_NOISE))
    noise: dict[Service, MetricNoise] = {
        s: MetricNoise(
            error_rate=cols[s]["error_rate"],
            latency_p99_ms=cols[s]["latency_p99_ms"],
            cpu=cols[s]["cpu"],
            disk_pct=cols[s]["disk_pct"],
        )
        for s in SERVICES
    }

    used: set[str] = set()
    deploys: list[Deploy] = []
    for _ in range(ex.randint(2, 3)):
        minute = ex.randint(-90, -1)
        service = ex.choice(SERVICES)
        deploys.append(Deploy(id=_deploy_id(rng, used), service=service, minute=minute))
    if ex.randint(0, 1):
        minute = ex.randint(1, fault_start - 1)
        service = ex.choice(SERVICES)
        deploys.append(Deploy(id=_deploy_id(rng, used), service=service, minute=minute))
    if fault_kind == "bad_deploy":
        deploys.append(
            Deploy(id=_deploy_id(rng, used), service="api", minute=fault_start, is_bad=True)
        )

    herring_service = ex.choice(SERVICES)
    while True:
        herring_start = ex.randint(*HERRING_START_RANGE)
        if abs(herring_start - fault_start) > HERRING_MARGIN:
            break
    herring = Herring(
        kind=ex.choice(HERRING_KINDS),
        service=herring_service,
        start=herring_start,
        duration=ex.randint(*HERRING_DURATION_RANGE),
    )

    return World(
        seed=seed,
        fault_kind=fault_kind,
        fault_start=fault_start,
        demand=demand,
        noise=noise,
        deploys=deploys,
        herring=herring,
    )
