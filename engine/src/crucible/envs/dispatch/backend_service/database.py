"""SQLite dispatch ledger. Every accepted operation commits atomically with its history."""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, cast

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
        if request.version == 2:
            state["version"] = 2
            for vehicle in state["vehicles"]:
                vehicle.update(tours=[], tour_active=False, returning=False, locked_route=[])
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM events")
            payload = request.model_dump()
            if request.version == 1:
                payload.pop("version")
            return self._write(state, {"type": "reset", **payload})

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
            if state.get("version", 1) == 2:
                return self._submit_plan_v2(state, request)
            vehicles = {v["id"]: v for v in state["vehicles"]}
            orders = {o["id"]: o for o in state["orders"]}
            if any(vid not in vehicles for vid in request.routes):
                raise DispatchError("Unknown vehicle identifier")
            if any(not isinstance(oid, str) for route in request.routes.values() for oid in route):
                raise DispatchError("Version 1 routes require flat delivery sequences")
            flat_routes = cast(dict[str, list[str]], request.routes)
            proposed: dict[str, list[str]] = {
                vid: flat_routes[vid] if vid in flat_routes else list(v["route"])
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

    def _submit_plan_v2(self, state: dict[str, Any], request: Plan) -> dict[str, Any]:
        """Validate the complete replacement before mutating any persisted reservation."""
        vehicles = {v["id"]: v for v in state["vehicles"]}
        orders = {o["id"]: o for o in state["orders"]}
        if any(vid not in vehicles for vid in request.routes):
            raise DispatchError("Unknown vehicle identifier")
        if any(not isinstance(tour, list) for tours in request.routes.values() for tour in tours):
            raise DispatchError("Version 2 routes require nested delivery tours")
        routes = cast(dict[str, list[list[str]]], request.routes)
        proposed = {
            vid: routes[vid] if vid in routes else v["tours"] for vid, v in vehicles.items()
        }
        assigned: set[str] = set()
        for vid, tours in proposed.items():
            vehicle = vehicles[vid]
            if len(tours) > 64 or sum(len(tour) for tour in tours) > 128:
                raise DispatchError("Plan contains too many tours or stops")
            if vehicle["tour_active"] and (not tours or tours[0] != vehicle["locked_route"]):
                raise DispatchError("Plan must preserve the complete loaded delivery sequence")
            if any(not tour for tour in tours):
                raise DispatchError("An empty delivery tour is invalid")
            for tour in tours:
                for oid in tour:
                    if oid not in orders or orders[oid]["status"] == "delivered":
                        raise DispatchError("Unknown or completed order identifier")
                    if orders[oid]["released_at"] > state["minute"]:
                        raise DispatchError("Order is not released yet")
                    if oid in assigned:
                        raise DispatchError("Order appears more than once across fleet tours")
                    if orders[oid]["status"] == "onboard" and oid not in vehicle["cargo"]:
                        raise DispatchError("An onboard parcel cannot change vehicles")
                    assigned.add(oid)
                if sum(orders[oid]["size"] for oid in tour) > vehicle["capacity"]:
                    raise DispatchError("Tour exceeds vehicle parcel capacity")
            if not set(vehicle["cargo"]).issubset(assigned):
                raise DispatchError("Plan must retain every onboard parcel")
        for order in state["orders"]:
            if order["status"] not in ("onboard", "delivered"):
                order["status"] = "unassigned"
                order["assigned_vehicle"] = None
        for vid, tours in proposed.items():
            vehicle = vehicles[vid]
            vehicle["tours"] = [list(tour) for tour in tours]
            vehicle["route"] = [oid for tour in tours for oid in tour]
            for oid in vehicle["route"]:
                orders[oid]["assigned_vehicle"] = vid
                if orders[oid]["status"] != "onboard":
                    orders[oid]["status"] = "assigned"
        return self._write(
            state, {"type": "plan", "minute": state["minute"], "routes": request.routes}
        )

    def simulator_event(self, event: SimulatorEvent) -> dict[str, Any]:
        with self.lock, self.connection:
            state = self.state()
            if state.get("version", 1) == 2:
                return self._simulator_event_v2(state, event)
            if event.type == "depot_return":
                raise DispatchError("Depot return events require version 2")
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

    def _simulator_event_v2(self, state: dict[str, Any], event: SimulatorEvent) -> dict[str, Any]:
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
                if vehicle["cargo"] or vehicle["tour_active"] or vehicle["returning"]:
                    raise DispatchError("Pickup requires completion of the previous depot tour")
                if not vehicle["tours"] or event.order_ids != vehicle["tours"][0]:
                    raise DispatchError("Pickup must load exactly the first committed tour")
                if any(
                    orders[oid]["status"] != "assigned"
                    or orders[oid]["assigned_vehicle"] != event.vehicle_id
                    or orders[oid]["released_at"] > event.minute
                    for oid in event.order_ids
                ):
                    raise DispatchError("Pickup requires available reserved parcels")
                if sum(orders[oid]["size"] for oid in event.order_ids) > vehicle["capacity"]:
                    raise DispatchError("Pickup exceeds vehicle parcel capacity")
                vehicle["cargo"] = list(event.order_ids)
                vehicle["locked_route"] = list(event.order_ids)
                vehicle["tour_active"] = True
                for oid in event.order_ids:
                    orders[oid]["status"] = "onboard"
            elif event.type == "deliver":
                oid = event.order_id
                if oid not in orders or oid not in vehicle["cargo"]:
                    raise DispatchError("Delivery requires an onboard parcel")
                if vehicle["node"] != orders[oid]["destination"] or vehicle["edge"] is not None:
                    raise DispatchError("Delivery requires reaching the customer")
                if not vehicle["locked_route"] or vehicle["locked_route"][0] != oid:
                    raise DispatchError("Delivery must follow the committed loaded sequence")
                vehicle["cargo"].remove(oid)
                vehicle["locked_route"].pop(0)
                vehicle["tours"][0].pop(0)
                vehicle["route"].remove(oid)
                orders[oid]["status"] = "delivered"
                orders[oid]["delivered_at"] = event.minute
                if not vehicle["tours"][0]:
                    vehicle["tours"].pop(0)
                    vehicle["tour_active"] = False
                    vehicle["returning"] = True
            elif event.type == "depot_return":
                if (
                    vehicle["node"] != state["depot"]
                    or vehicle["edge"] is not None
                    or vehicle["cargo"]
                    or vehicle["tour_active"]
                    or not vehicle["returning"]
                ):
                    raise DispatchError("Depot return requires physical completion at the depot")
                vehicle["returning"] = False
        state["minute"] = event.minute
        return self._write(state, event.model_dump())
