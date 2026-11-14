"""Typed projections of public dispatch tool results, shared by all policies."""

from pydantic import BaseModel, Field


class OrderView(BaseModel):
    id: str
    destination: str
    size: int
    deadline: float
    value: float
    grace: float
    status: str


class EdgeView(BaseModel):
    to_node: str
    remaining_minutes: float
    remaining_distance_km: float


class VehicleView(BaseModel):
    id: str
    capacity: int
    node: str
    cargo: list[str]
    route: list[str]
    edge: EdgeView | None = None


class MapView(BaseModel):
    depot: str
    travel_times: dict[str, dict[str, float]]
    distances: dict[str, dict[str, float]]


class StateView(BaseModel):
    minute: float
    horizon: float
    driving_cost_per_km: float
    orders: list[OrderView]
    vehicles: list[VehicleView]
    city_map: MapView
    decision_interval: float = Field(default=30)
