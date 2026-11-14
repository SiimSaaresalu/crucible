"""Episode state and public view models of `incident@1`.

`EpisodeState` is the mutable record of one episode. The view models (`ServiceMetrics`,
`Alert`, `LogLine`, `DeployView`, `Topology`) have only explicit public fields, so a
`ToolResult` can be built from them with `model_dump(mode="json")`. None of them carries
hidden world data.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nightshift.tasks.incident.world import SERVICES, TOPOLOGY_EDGES, RootCause, Service

__all__ = [
    "Alert",
    "DeployView",
    "EpisodeState",
    "Intervention",
    "InterventionTool",
    "LogLevel",
    "LogLine",
    "ReadRecord",
    "ServiceMetrics",
    "Topology",
]

InterventionTool = Literal["restart", "rollback", "clean_disk", "scale"]
LogLevel = Literal["INFO", "WARN", "ERROR", "FATAL"]


class Intervention(BaseModel):
    """An action the agent applied. `target` is a service, or a deploy id for `rollback`.

    `n` is the instance count and is used by `scale` only (`None` counts as 1).
    """

    model_config = ConfigDict(frozen=True)

    tool: InterventionTool
    target: str
    start: int
    duration: int
    n: int | None = None


class ReadRecord(BaseModel):
    """One read tool call, kept so the grader can check evidence gathering."""

    model_config = ConfigDict(frozen=True)

    tool: str
    service: Service | None = None
    minute: int


class EpisodeState(BaseModel):
    """Mutable, serialisable record of one episode."""

    minute: int = 0
    interventions: list[Intervention] = Field(default_factory=list[Intervention])
    reads: list[ReadRecord] = Field(default_factory=list[ReadRecord])
    wrong_repairs: int = 0
    healthy_damage_actions: int = 0
    first_repair_correct: bool | None = None
    evidence_before_first_repair: bool = False
    resolved: RootCause | None = None
    steps: int = 0


class ServiceMetrics(BaseModel):
    """Public metrics of one service at one minute."""

    model_config = ConfigDict(frozen=True)

    error_rate: float = Field(ge=0.0, le=1.0)
    latency_p99_ms: float = Field(ge=0.0)
    cpu: float = Field(ge=0.0, le=1.0)
    disk_pct: float = Field(ge=0.0, le=1.0)


class Alert(BaseModel):
    """An active alert. `started_at` is the minute it fired."""

    model_config = ConfigDict(frozen=True)

    name: str
    service: Service
    started_at: int


class LogLine(BaseModel):
    """One log line."""

    model_config = ConfigDict(frozen=True)

    minute: int
    level: LogLevel
    msg: str


class DeployView(BaseModel):
    """A deploy as the agent sees it. There is no 'bad' flag."""

    model_config = ConfigDict(frozen=True)

    id: str
    service: Service
    minute: int


class Topology(BaseModel):
    """Service graph: api calls cache; on a miss or cache down, api calls db."""

    model_config = ConfigDict(frozen=True)

    services: list[Service] = Field(default_factory=lambda: list(SERVICES))
    edges: list[tuple[Service, Service]] = Field(default_factory=lambda: list(TOPOLOGY_EDGES))
