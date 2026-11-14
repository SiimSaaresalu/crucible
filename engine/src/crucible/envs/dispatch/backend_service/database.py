"""SQLite dispatch ledger. Every accepted operation commits atomically with its history."""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from crucible.envs.dispatch.backend_service.models import Order, Plan, Reset, SimulatorEvent


class DispatchError(ValueError):
    """A dispatch operation violates the public physical or lifecycle rules."""


class DispatchStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.RLock()
        self.connection.executescript(
            "CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "payload TEXT NOT NULL);"
        )

    def close(self) -> None:
        self.connection.close()

    def state(self) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT payload FROM state WHERE id=1").fetchone()
            if row is None:
                raise DispatchError("Dispatch service has not been reset")
            return json.loads(row[0])

    def history(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                json.loads(row[0])
                for row in self.connection.execute("SELECT payload FROM events ORDER BY id")
            ]

    def _write(self, state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        self.connection.execute(
            "INSERT OR REPLACE INTO state(id,payload) VALUES(1,?)", (json.dumps(state),)
        )
        self.connection.execute("INSERT INTO events(payload) VALUES(?)", (json.dumps(event),))
        return state

    def reset(self, request: Reset) -> dict[str, Any]:
        ids = [v.id for v in request.vehicles]
        if len(set(ids)) != len(ids):
            raise DispatchError("Vehicle identifiers must be unique")
        state: dict[str, Any] = {
            "minute": 0.0,
            "depot": request.depot,
            "orders": [],
            "vehicles": [
                {
                    **v.model_dump(),
                    "cargo": [],
                    "route": [],
                    "edge": None,
                    "locked_stop": None,
                    "distance": 0.0,
                    "distance_km": 0.0,
                }
                for v in request.vehicles
            ],
        }
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM events")
            return self._write(state, {"type": "reset", **request.model_dump()})

    def create_order(self, request: Order) -> dict[str, Any]:
        with self.lock, self.connection:
            state = self.state()
            if any(o["id"] == request.id for o in state["orders"]):
                raise DispatchError("Order identifier already exists")
            if request.released_at > state["minute"]:
                raise DispatchError("Order is not released yet")
            state["orders"].append(
                {
                    **request.model_dump(),
                    "status": "unassigned",
                    "assigned_vehicle": None,
                    "delivered_at": None,
                }
            )
            return self._write(state, {"type": "order_created", **request.model_dump()})

    def submit_plan(self, request: Plan) -> dict[str, Any]:
        with self.lock, self.connection:
            state = self.state()
            vehicles = {v["id"]: v for v in state["vehicles"]}
            orders = {o["id"]: o for o in state["orders"]}
            if any(vid not in vehicles for vid in request.routes):
                raise DispatchError("Unknown vehicle identifier")
            proposed: dict[str, list[str]] = {
                vid: request.routes[vid] if vid in request.routes else list(v["route"])
                for vid, v in vehicles.items()
            }
            assigned: set[str] = set()
            for vid, route in proposed.items():
                v = vehicles[vid]
                if len(route) > 36 or len(set(route)) != len(route):
                    raise DispatchError("Route contains duplicate or too many stops")
                if not set(v["cargo"]).issubset(route):
                    raise DispatchError("Route must retain every onboard parcel")
                for oid in route:
                    if oid not in orders or orders[oid]["status"] == "delivered":
                        raise DispatchError("Unknown or completed order identifier")
                    if oid in assigned:
                        raise DispatchError("Order appears in more than one vehicle route")
                    if orders[oid]["status"] == "onboard" and oid not in v["cargo"]:
                        raise DispatchError("An onboard parcel cannot change vehicles")
                    assigned.add(oid)
                if sum(orders[oid]["size"] for oid in set(route) | set(v["cargo"])) > v["capacity"]:
                    raise DispatchError("Route exceeds vehicle parcel capacity")
            for order in state["orders"]:
                if order["status"] not in ("onboard", "delivered"):
                    order["status"] = "unassigned"
                    order["assigned_vehicle"] = None
            for vid, route in proposed.items():
                vehicles[vid]["route"] = list(route)
                for oid in route:
                    orders[oid]["assigned_vehicle"] = vid
                    if orders[oid]["status"] != "onboard":
                        orders[oid]["status"] = "assigned"
            return self._write(
                state, {"type": "plan", "minute": state["minute"], "routes": request.routes}
            )

    def simulator_event(self, event: SimulatorEvent) -> dict[str, Any]:
        with self.lock, self.connection:
            state = self.state()
            if event.minute < state["minute"]:
                raise DispatchError("Simulator clock cannot move backwards")
            vehicles = {v["id"]: v for v in state["vehicles"]}
            orders = {o["id"]: o for o in state["orders"]}
            if event.type != "clock":
                if event.vehicle_id not in vehicles:
                    raise DispatchError("Unknown vehicle identifier")
                vehicle = vehicles[event.vehicle_id]
                if event.type == "move":
                    if not event.node:
                        raise DispatchError("Movement requires a node")
                    vehicle["node"] = event.node
                    vehicle["edge"] = event.edge
                    vehicle["distance"] += event.distance_delta
                    vehicle["distance_km"] = vehicle["distance"]
                elif event.type == "pickup":
                    if vehicle["node"] != state["depot"] or vehicle["edge"] is not None:
                        raise DispatchError("Pickup requires a stationary vehicle at the depot")
                    if len(set(event.order_ids)) != len(event.order_ids):
                        raise DispatchError("Duplicate pickup identifier")
                    for oid in event.order_ids:
                        if oid not in orders or oid not in vehicle["route"]:
                            raise DispatchError("Pickup requires an assigned order")
                        if orders[oid]["status"] != "assigned":
                            raise DispatchError("Parcel has already been collected or delivered")
                    loaded = set(vehicle["cargo"]) | set(event.order_ids)
                    if sum(orders[oid]["size"] for oid in loaded) > vehicle["capacity"]:
                        raise DispatchError("Pickup exceeds vehicle parcel capacity")
                    for oid in event.order_ids:
                        vehicle["cargo"].append(oid)
                        orders[oid]["status"] = "onboard"
                elif event.type == "deliver":
                    oid = event.order_id
                    if oid not in orders or oid not in vehicle["cargo"]:
                        raise DispatchError("Delivery requires an onboard parcel")
                    if vehicle["node"] != orders[oid]["destination"] or vehicle["edge"] is not None:
                        raise DispatchError("Delivery requires reaching the customer")
                    if not vehicle["route"] or vehicle["route"][0] != oid:
                        raise DispatchError("Delivery must follow the committed route")
                    vehicle["cargo"].remove(oid)
                    vehicle["route"].remove(oid)
                    orders[oid]["status"] = "delivered"
                    orders[oid]["delivered_at"] = event.minute
            state["minute"] = event.minute
            return self._write(state, event.model_dump())
