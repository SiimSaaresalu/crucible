"""Typed projections of the four public dispatch@2 tools."""

from pydantic import BaseModel, Field


class OrderView(BaseModel):
    id: str
    destination: str
    size: int
    deadline: float
    value: float
    grace: float
    released_at: float = 0
    status: str
    assigned_vehicle: str | None = None
    delivered_at: float | None = None


class EdgeView(BaseModel):
    to_node: str
    remaining_minutes: float
    remaining_distance_km: float


class VehicleView(BaseModel):
    id: str
    capacity: int
    node: str
    cargo: list[str]
    tours: list[list[str]]
    tour_active: bool = False
    returning: bool = False
    edge: EdgeView | None = None
    distance: float = 0

    @property
    def locked_route(self) -> list[str]:
        return list(self.tours[0]) if self.tour_active and self.tours else []


class RoadView(BaseModel):
    a: str
    b: str
    distance: float
    minutes: float


class NodeView(BaseModel):
    id: str
    x: float = 0
    y: float = 0


class MapView(BaseModel):
    depot: str
    nodes: list[NodeView]
    roads: list[RoadView]


class TrafficView(BaseModel):
    a: str
    b: str
    multiplier: float = 1
    closed: bool = False


class StateView(BaseModel):
    minute: float
    horizon: float
    depot: str
    driving_cost_per_km: float = 0.35
    decision_interval: float = 30
    load_service_minutes: float = 0
    service_minutes: float = 0
    orders: list[OrderView]
    vehicles: list[VehicleView]
    traffic: list[TrafficView] = Field(default_factory=lambda: list[TrafficView]())
    traffic_revision: str | int = 0
