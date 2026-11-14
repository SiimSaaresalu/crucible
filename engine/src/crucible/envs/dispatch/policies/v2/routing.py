"""Public road estimates and deterministic capacity-feasible tour insertion.

Every estimate assumes currently observed traffic persists. Estimated delivery
value and distance refer to the same fastest road paths. No future schedule is
used. Existing loaded sequences and physical edge/return commitments are fixed.
"""

import heapq
from dataclasses import dataclass, field
from math import inf
from typing import Literal

from crucible.envs.dispatch.policies.v2.views import MapView, OrderView, StateView, VehicleView

Mode = Literal["greedy", "regret", "nearest", "deadline"]
type Routes = dict[str, list[list[str]]]


@dataclass(frozen=True)
class Estimate:
    utility: float
    distance: float
    finish: float
    value: float = 0
    arrivals: dict[str, float] = field(default_factory=lambda: dict[str, float]())


@dataclass(frozen=True)
class Insertion:
    vehicle_id: str
    tours: tuple[tuple[str, ...], ...]
    gain: float
    extra_distance: float
    finish: float


class PublicRouting:
    """All-pairs shortest paths reconstructed from static roads and current traffic."""

    def __init__(self, city_map: MapView, state: StateView) -> None:
        self.depot = city_map.depot
        graph: dict[str, list[tuple[str, float, float]]] = {n.id: [] for n in city_map.nodes}
        traffic = {(t.a, t.b): t for t in state.traffic}
        for road in city_map.roads:
            change = traffic.get((road.a, road.b), traffic.get((road.b, road.a)))
            if change is not None and change.closed:
                continue
            minutes = road.minutes * (change.multiplier if change else 1)
            graph[road.a].append((road.b, minutes, road.distance))
            graph[road.b].append((road.a, minutes, road.distance))
        self.times: dict[str, dict[str, float]] = {}
        self.distances: dict[str, dict[str, float]] = {}
        for source in graph:
            times, distances = {source: 0.0}, {source: 0.0}
            queue = [(0.0, source)]
            while queue:
                cost, node = heapq.heappop(queue)
                if cost != times[node]:
                    continue
                for other, minutes, distance in graph[node]:
                    candidate = cost + minutes
                    if candidate < times.get(other, inf):
                        times[other] = candidate
                        distances[other] = distances[node] + distance
                        heapq.heappush(queue, (candidate, other))
            self.times[source], self.distances[source] = times, distances

    def leg(self, source: str, destination: str) -> tuple[float, float]:
        return (
            self.times.get(source, {}).get(destination, inf),
            self.distances.get(source, {}).get(destination, inf),
        )


class Planner:
    """Estimate multi-tour plans and build explainable simple baselines."""

    def __init__(self, state: StateView, city_map: MapView) -> None:
        self.state, self.city_map = state, city_map
        self.orders = {o.id: o for o in state.orders}
        self.vehicles = {v.id: v for v in state.vehicles}
        self.routing = PublicRouting(city_map, state)

    def initial_routes(self) -> Routes:
        """Keep only irreversible cargo; reconsider every unpicked reservation."""
        return {vid: [v.locked_route] if v.tour_active else [] for vid, v in self.vehicles.items()}

    def estimate(self, vehicle: VehicleView, tours: list[list[str]]) -> Estimate:
        locked = vehicle.locked_route
        if locked and (not tours or tours[0] != locked):
            return Estimate(-inf, inf, inf)
        flat = [oid for tour in tours for oid in tour]
        if len(set(flat)) != len(flat) or not set(vehicle.cargo).issubset(flat):
            return Estimate(-inf, inf, inf)
        if any(
            not tour
            or any(oid not in self.orders for oid in tour)
            or sum(self.orders[oid].size for oid in tour) > vehicle.capacity
            for tour in tours
        ):
            return Estimate(-inf, inf, inf)
        now, node, distance = self.state.minute, vehicle.node, 0.0
        if vehicle.edge is not None:
            now += vehicle.edge.remaining_minutes
            distance += vehicle.edge.remaining_distance_km
            node = vehicle.edge.to_node
        value = 0.0
        arrivals: dict[str, float] = {}

        def travel(destination: str) -> None:
            nonlocal now, node, distance
            minutes, length = self.routing.leg(node, destination)
            now += minutes
            distance += length
            node = destination

        # A committed return completes before another load, even with no new tours.
        if vehicle.returning or (
            not vehicle.tour_active and (tours or node != self.city_map.depot)
        ):
            travel(self.city_map.depot)
        for index, tour in enumerate(tours):
            if not (index == 0 and vehicle.tour_active):
                travel(self.city_map.depot)
                now += self.state.load_service_minutes
            for oid in tour:
                order = self.orders[oid]
                travel(order.destination)
                now += self.state.service_minutes
                arrivals[oid] = now
                if now <= self.state.horizon:
                    value += order.value * max(
                        0.0, 1.0 - max(0.0, now - order.deadline) / order.grace
                    )
            travel(self.city_map.depot)
        if distance == inf or now == inf:
            return Estimate(-inf, inf, inf)
        return Estimate(
            value - distance * self.state.driving_cost_per_km, distance, now, value, arrivals
        )

    def insertions(
        self, order: OrderView, routes: Routes, *, append_only: bool = False
    ) -> list[Insertion]:
        candidates: list[Insertion] = []
        for vid, vehicle in self.vehicles.items():
            tours = routes[vid]
            before = self.estimate(vehicle, tours)
            first = int(vehicle.tour_active)
            proposed: list[list[list[str]]] = []
            for tour_index in range(first, len(tours)):
                tour = tours[tour_index]
                if sum(self.orders[oid].size for oid in tour) + order.size > vehicle.capacity:
                    continue
                for position in [len(tour)] if append_only else range(len(tour) + 1):
                    new = [list(t) for t in tours]
                    new[tour_index].insert(position, order.id)
                    proposed.append(new)
            if order.size <= vehicle.capacity:
                positions = [len(tours)] if append_only else range(first, len(tours) + 1)
                for index in positions:
                    proposed.append([*tours[:index], [order.id], *tours[index:]])
            for new in proposed:
                after = self.estimate(vehicle, new)
                if after.utility == -inf or after.finish > self.state.horizon:
                    continue
                candidates.append(
                    Insertion(
                        vid,
                        tuple(tuple(t) for t in new),
                        after.utility - before.utility,
                        after.distance - before.distance,
                        after.finish,
                    )
                )
        return sorted(
            candidates, key=lambda c: (-c.gain, c.extra_distance, c.finish, c.vehicle_id, c.tours)
        )

    def build(self, mode: Mode = "greedy") -> Routes:
        routes = self.initial_routes()
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
                best = candidates[0]
                if mode in ("nearest", "deadline"):
                    best = min(
                        candidates,
                        key=lambda c: (c.extra_distance, c.finish, c.vehicle_id, c.tours),
                    )
                    priority: tuple[float | str, ...] = (
                        best.extra_distance if mode == "nearest" else order.deadline,
                        order.deadline,
                        -order.value,
                        oid,
                    )
                else:
                    second = candidates[1].gain if len(candidates) > 1 else 0
                    regret = best.gain - max(0, second) if mode == "regret" else 0
                    priority = (-(best.gain + regret), -best.gain, order.deadline, oid)
                if best.gain > 1e-9:
                    choices.append((priority, oid, best))
            if not choices:
                break
            _, oid, chosen = min(choices, key=lambda item: item[0])
            routes[chosen.vehicle_id] = [list(t) for t in chosen.tours]
            del pending[oid]
        return routes
