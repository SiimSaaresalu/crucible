"""Deterministic route estimates using only the current public observation.

A route with an uncollected parcel first returns to the depot. All current cargo
must stay on its van. The edge currently being driven is included before any new
route suffix. Estimates assume today's published traffic persists; no future
arrivals or disruption schedules are available to this module.
"""

from dataclasses import dataclass
from math import inf
from typing import Literal

from crucible.envs.dispatch.policies.views import OrderView, StateView, VehicleView

Mode = Literal["regret", "greedy", "nearest", "deadline"]


@dataclass(frozen=True)
class Estimate:
    utility: float
    distance: float
    finish: float


@dataclass(frozen=True)
class Insertion:
    vehicle_id: str
    route: tuple[str, ...]
    gain: float
    extra_distance: float
    finish: float


class Planner:
    """Economic insertion planner with explicit physical feasibility checks."""

    def __init__(self, state: StateView) -> None:
        self.state = state
        self.orders = {o.id: o for o in state.orders}
        self.vehicles = {v.id: v for v in state.vehicles}

    def estimate(self, vehicle: VehicleView, route: list[str]) -> Estimate:
        if not set(vehicle.cargo).issubset(route):
            return Estimate(-inf, inf, inf)
        if sum(self.orders[oid].size for oid in route) > vehicle.capacity:
            return Estimate(-inf, inf, inf)
        now = self.state.minute
        node = vehicle.node
        distance = 0.0
        if vehicle.edge is not None:
            now += vehicle.edge.remaining_minutes
            distance += vehicle.edge.remaining_distance_km
            node = vehicle.edge.to_node
        stops = [(self.orders[oid].destination, oid) for oid in route]
        if any(oid not in vehicle.cargo for oid in route):
            stops.insert(0, (self.state.city_map.depot, ""))
        value = 0.0
        for destination, oid in stops:
            minutes = self.state.city_map.travel_times.get(node, {}).get(destination, inf)
            length = self.state.city_map.distances.get(node, {}).get(destination, inf)
            if minutes == inf or length == inf:
                return Estimate(-inf, inf, inf)
            now += minutes
            distance += length
            node = destination
            if oid and now <= self.state.horizon:
                order = self.orders[oid]
                punctuality = max(0.0, 1.0 - max(0.0, now - order.deadline) / order.grace)
                value += order.value * punctuality
        # Routes that extend beyond the shift pay only distance actually drivable
        # in the simulator, but charging the full planned distance is conservative.
        return Estimate(value - distance * self.state.driving_cost_per_km, distance, now)

    def insertions(
        self, order: OrderView, routes: dict[str, list[str]], *, append_only: bool = False
    ) -> list[Insertion]:
        candidates: list[Insertion] = []
        for vid, vehicle in self.vehicles.items():
            route = routes[vid]
            if sum(self.orders[oid].size for oid in route) + order.size > vehicle.capacity:
                continue
            before = self.estimate(vehicle, route)
            positions = [len(route)] if append_only else range(len(route) + 1)
            for index in positions:
                proposed = [*route[:index], order.id, *route[index:]]
                after = self.estimate(vehicle, proposed)
                if after.utility == -inf or after.finish > self.state.horizon:
                    continue
                candidates.append(
                    Insertion(
                        vid,
                        tuple(proposed),
                        after.utility - before.utility,
                        after.distance - before.distance,
                        after.finish,
                    )
                )
        return sorted(
            candidates, key=lambda c: (-c.gain, c.extra_distance, c.finish, c.vehicle_id, c.route)
        )

    def improve(self, routes: dict[str, list[str]]) -> None:
        """Accept only improving stop swaps or contiguous 2-opt reversals."""
        for vid, vehicle in self.vehicles.items():
            route = routes[vid]
            while len(route) > 1:
                score = self.estimate(vehicle, route).utility
                best = route
                for left in range(len(route) - 1):
                    for right in range(left + 1, len(route)):
                        swapped = list(route)
                        swapped[left], swapped[right] = swapped[right], swapped[left]
                        reversed_route = [
                            *route[:left],
                            *reversed(route[left : right + 1]),
                            *route[right + 1 :],
                        ]
                        for candidate in (swapped, reversed_route):
                            utility = self.estimate(vehicle, candidate).utility
                            if utility > score + 1e-9:
                                score, best = utility, candidate
                if best is route:
                    break
                route = best
            routes[vid] = route

    def build(self, mode: Mode = "regret") -> dict[str, list[str]]:
        # Reconsider every uncollected order at each round. Already loaded parcels
        # retain ownership; their delivery sequence may be improved.
        routes = {
            vid: [oid for oid in v.route if oid in v.cargo] for vid, v in self.vehicles.items()
        }
        pending = {
            oid: o for oid, o in self.orders.items() if o.status not in ("onboard", "delivered")
        }
        while pending:
            choices: list[tuple[tuple[float | str, ...], str, Insertion]] = []
            for oid, order in pending.items():
                candidates = self.insertions(
                    order, routes, append_only=mode in ("nearest", "deadline")
                )
                if not candidates:
                    continue
                if mode in ("nearest", "deadline"):
                    best = min(
                        candidates,
                        key=lambda c: (c.extra_distance, c.finish, c.vehicle_id, c.route),
                    )
                    priority: tuple[float | str, ...] = (
                        (best.extra_distance if mode == "nearest" else order.deadline),
                        order.deadline,
                        -order.value,
                        oid,
                    )
                else:
                    best = candidates[0]
                    if best.gain <= 1e-9:
                        continue
                    # A missing second opportunity is equivalent to leaving this
                    # order unassigned, which contributes no incremental value.
                    second = candidates[1].gain if len(candidates) > 1 else 0.0
                    regret = best.gain - max(0.0, second)
                    priority = (
                        -(best.gain + (regret if mode == "regret" else 0.0)),
                        -best.gain,
                        order.deadline,
                        oid,
                    )
                choices.append((priority, oid, best))
            if not choices:
                break
            _, oid, chosen = min(choices, key=lambda entry: entry[0])
            routes[chosen.vehicle_id] = list(chosen.route)
            del pending[oid]
        if mode in ("regret", "greedy"):
            self.improve(routes)
        return routes
