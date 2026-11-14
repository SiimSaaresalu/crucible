"""Strict HTTP request contracts. Simulator requests require a private capability."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Order(Request):
    id: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    size: int = Field(ge=1, le=10)
    deadline: float = Field(ge=0)
    value: float = Field(gt=0)
    grace: float = Field(gt=0)
    released_at: float = Field(ge=0)


class Vehicle(Request):
    id: str = Field(min_length=1, max_length=80)
    capacity: int = Field(ge=1, le=10)
    node: str = Field(min_length=1, max_length=80)


class Reset(Request):
    vehicles: list[Vehicle] = Field(min_length=1, max_length=4)
    depot: str


class Plan(Request):
    routes: dict[str, list[str]]


class SimulatorEvent(Request):
    type: Literal["clock", "move", "pickup", "deliver"]
    minute: float = Field(ge=0)
    vehicle_id: str | None = None
    node: str | None = None
    edge: dict[str, JsonValue] | None = None
    distance_delta: float = Field(default=0, ge=0)
    order_ids: list[str] = Field(default_factory=list)
    order_id: str | None = None
