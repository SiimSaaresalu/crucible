"""Private seeded arrivals and deterministic physical dispatch simulation.

Only ``state`` and ``city_map`` are public observations. Future orders and traffic
are generated before play, independently of actions, and never included in them.
"""

from __future__ import annotations

import heapq
from dataclasses import asdict, dataclass
from typing import Any

from crucible.core.rng import RngStreams

HORIZON = 360
DECISION_INTERVAL = 30
DEPOT = "n12"
DRIVING_COST_PER_KM = 0.35


@dataclass(frozen=True)
class Order:
    id: str
    destination: str
    size: int
    deadline: int
    value: float
    grace: int
    released_at: int


@dataclass(frozen=True)
class Road:
    a: str
    b: str
    distance: float
    minutes: int


@dataclass(frozen=True)
class Disruption:
    start: int
    end: int
    a: str
    b: str
    multiplier: int
    closed: bool


@dataclass(frozen=True)
class World:
    orders: tuple[Order, ...]
    roads: tuple[Road, ...]
    disruptions: tuple[Disruption, ...]
    capacities: tuple[int, ...]

    @property
    def total_value(self) -> float:
        return sum(o.value for o in self.orders)


def make_world(seed: int, salt: str | None = None) -> World:
    """Draw the entire exogenous schedule once from the salted stream."""
    rng = RngStreams(seed, salt).exogenous
    roads: list[Road] = []
    for y in range(5):
        for x in range(5):
            for dx, dy in ((1, 0), (0, 1)):
                if x + dx < 5 and y + dy < 5:
                    roads.append(
                        Road(f"n{y * 5 + x}", f"n{(y + dy) * 5 + x + dx}", 2.0, rng.randint(5, 9))
                    )
    # A few diagonals vary topology while retaining a connected public grid.
    for y in range(4):
        for x in range(4):
            if rng.random() < 0.18:
                roads.append(
                    Road(f"n{y * 5 + x}", f"n{(y + 1) * 5 + x + 1}", 2.828427, rng.randint(8, 12))
                )
    count = rng.randint(28, 36)
    initial = rng.randint(8, 12)
    releases = [0] * initial + sorted(rng.randrange(1, 10) * 30 for _ in range(count - initial))
    orders: list[Order] = []
    # Demand clusters change by episode, with occasional cross-city jobs.
    clusters = rng.sample([0, 4, 20, 24, 6, 8, 16, 18], 3)
    for i, release in enumerate(releases):
        destination = rng.choice(clusters) if rng.random() < 0.55 else rng.randrange(25)
        if destination == 12:
            destination = 11
        urgent = rng.random() < 0.4
        deadline = release + (rng.randint(28, 60) if urgent else rng.randint(65, 125))
        orders.append(
            Order(
                f"o{i:03d}",
                f"n{destination}",
                rng.randint(1, 3),
                min(deadline, HORIZON - 5),
                float(rng.randint(30, 100)),
                rng.randint(20, 45),
                release,
            )
        )
    disruptions: list[Disruption] = []
    for start in (30, 90, 150, 210, 270):
        for road in rng.sample(roads, rng.randint(2, 4)):
            disruptions.append(
                Disruption(
                    start,
                    min(start + rng.choice((30, 60)), HORIZON),
                    road.a,
                    road.b,
                    rng.choice((2, 3)),
                    rng.random() < 0.2,
                )
            )
    return World(
        tuple(orders), tuple(roads), tuple(disruptions), tuple(rng.randint(6, 10) for _ in range(4))
    )


@dataclass
class _Motion:
    node: str = DEPOT
    edge: dict[str, Any] | None = None
    uncommitted_distance: float = 0.0


class DispatchSimulation:
    """World authority for movement; every lifecycle update crosses backend HTTP."""

    def __init__(self, world: World, backend: Any) -> None:
        self.world = world
        self.backend = backend
        self.minute = 0
        self.horizon = HORIZON
        self._snapshot: dict[str, Any] = {}
        self._motion = {f"v{i}": _Motion() for i in range(4)}
        self._released: set[str] = set()
        self._closed = False
        self._map_cache: tuple[int, dict[str, Any]] | None = None

    @classmethod
    async def create(
        cls, seed: int, salt: str | None = None, backend: Any = None
    ) -> DispatchSimulation:
        if backend is None:
            from crucible.envs.dispatch.backend import DispatchBackend

            backend = DispatchBackend()
        sim = cls(make_world(seed, salt), backend)
        await backend.reset(
            [
                {"id": f"v{i}", "capacity": capacity, "node": DEPOT}
                for i, capacity in enumerate(sim.world.capacities)
            ],
            DEPOT,
        )
        await sim._release_orders()
        sim._snapshot = await backend.state()
        return sim

    async def _release_orders(self) -> None:
        if any(
            o.released_at <= self.minute and o.id not in self._released for o in self.world.orders
        ):
            await self.backend.simulator_event({"type": "clock", "minute": self.minute})
        for order in self.world.orders:
            if order.released_at <= self.minute and order.id not in self._released:
                await self.backend.create_order(asdict(order))
                self._released.add(order.id)

    def _active_traffic(self) -> list[Disruption]:
        return [d for d in self.world.disruptions if d.start <= self.minute < d.end]

    def _graph(self) -> dict[str, list[tuple[str, float, float]]]:
        graph: dict[str, list[tuple[str, float, float]]] = {f"n{i}": [] for i in range(25)}
        active = {(d.a, d.b): d for d in self._active_traffic()}
        for road in self.world.roads:
            disruption = active.get((road.a, road.b))
            if disruption is not None and disruption.closed:
                continue
            minutes = road.minutes * (disruption.multiplier if disruption else 1)
            graph[road.a].append((road.b, float(minutes), road.distance))
            graph[road.b].append((road.a, float(minutes), road.distance))
        return graph

    @staticmethod
    def _paths(
        graph: dict[str, list[tuple[str, float, float]]], source: str
    ) -> tuple[dict[str, float], dict[str, float], dict[str, str]]:
        times = {source: 0.0}
        distances = {source: 0.0}
        previous: dict[str, str] = {}
        queue = [(0.0, source)]
        while queue:
            cost, node = heapq.heappop(queue)
            if cost != times[node]:
                continue
            for other, minutes, distance in graph[node]:
                candidate = cost + minutes
                if candidate < times.get(other, float("inf")):
                    times[other] = candidate
                    distances[other] = distances[node] + distance
                    previous[other] = node
                    heapq.heappush(queue, (candidate, other))
        return times, distances, previous

    def city_map(self) -> dict[str, Any]:
        """Public topology and CURRENT estimates, never the disruption schedule."""
        if self._map_cache is not None and self._map_cache[0] == self.minute:
            return self._map_cache[1]
        graph = self._graph()
        travel_times: dict[str, dict[str, float]] = {}
        distances: dict[str, dict[str, float]] = {}
        for source in graph:
            times, lengths, _ = self._paths(graph, source)
            # A closure can isolate a node; expose null rather than JSON Infinity.
            travel_times[source] = times
            distances[source] = lengths
        result = {
            "depot": DEPOT,
            "nodes": [{"id": f"n{i}", "x": (i % 5) * 2, "y": (i // 5) * 2} for i in range(25)],
            "roads": [asdict(r) for r in self.world.roads],
            "travel_times": travel_times,
            "distances": distances,
            "traffic": [asdict(d) for d in self._active_traffic()],
        }
        self._map_cache = (self.minute, result)
        return result

    async def state(self) -> dict[str, Any]:
        self._snapshot = await self.backend.state()
        return {
            **self._snapshot,
            "minute": self.minute,
            "horizon": HORIZON,
            "decision_interval": DECISION_INTERVAL,
            "driving_cost_per_km": DRIVING_COST_PER_KM,
            "city_map": self.city_map(),
        }

    async def submit_plan(self, routes: dict[str, list[str]]) -> dict[str, Any]:
        if self.minute >= HORIZON:
            raise ValueError("Shift is over")
        self._snapshot = await self.backend.submit_plan(routes)
        return await self.advance(DECISION_INTERVAL)

    async def _event(self, event: dict[str, Any]) -> None:
        self._snapshot = await self.backend.simulator_event({**event, "minute": self.minute})

    def _vehicle(self, vehicle_id: str) -> dict[str, Any]:
        return next(v for v in self._snapshot["vehicles"] if v["id"] == vehicle_id)

    async def _sync_motion(self, vehicle_id: str) -> None:
        motion = self._motion[vehicle_id]
        await self._event(
            {
                "type": "move",
                "vehicle_id": vehicle_id,
                "node": motion.node,
                "edge": motion.edge,
                "distance_delta": round(motion.uncommitted_distance, 9),
            }
        )
        motion.uncommitted_distance = 0.0

    async def _at_node(self, vehicle_id: str) -> None:
        motion = self._motion[vehicle_id]
        vehicle = self._vehicle(vehicle_id)
        if motion.node == DEPOT:
            pickup = [oid for oid in vehicle["route"] if oid not in vehicle["cargo"]]
            if pickup:
                await self._sync_motion(vehicle_id)
                await self._event({"type": "pickup", "vehicle_id": vehicle_id, "order_ids": pickup})
                vehicle = self._vehicle(vehicle_id)
        orders = {o["id"]: o for o in self._snapshot["orders"]}
        while (
            vehicle["route"]
            and vehicle["route"][0] in vehicle["cargo"]
            and orders[vehicle["route"][0]]["destination"] == motion.node
        ):
            await self._sync_motion(vehicle_id)
            await self._event(
                {"type": "deliver", "vehicle_id": vehicle_id, "order_id": vehicle["route"][0]}
            )
            vehicle = self._vehicle(vehicle_id)

    def _start_edge(self, vehicle_id: str) -> None:
        motion = self._motion[vehicle_id]
        vehicle = self._vehicle(vehicle_id)
        if not vehicle["route"]:
            return
        orders = {o["id"]: o for o in self._snapshot["orders"]}
        # Missing parcels require a physical depot visit before continuing.
        target = (
            DEPOT
            if any(oid not in vehicle["cargo"] for oid in vehicle["route"])
            else orders[vehicle["route"][0]]["destination"]
        )
        if target == motion.node:
            return
        graph = self._graph()
        times, _, previous = self._paths(graph, motion.node)
        if target not in times:
            return  # Wait for the temporary closure to clear.
        first = target
        while previous[first] != motion.node:
            first = previous[first]
        _, duration, distance = next(edge for edge in graph[motion.node] if edge[0] == first)
        motion.edge = {
            "from_node": motion.node,
            "to_node": first,
            "remaining_minutes": duration,
            "total_minutes": duration,
            "distance_km": distance,
            "remaining_distance_km": distance,
        }

    async def advance(self, minutes: int) -> dict[str, Any]:
        if minutes < 0:
            raise ValueError("Cannot reverse virtual time")
        target = min(self.minute + minutes, HORIZON)
        # Pickup at the current clock time, then move in exact integer minute ticks.
        for vehicle_id in self._motion:
            if self._motion[vehicle_id].edge is None:
                await self._at_node(vehicle_id)
                self._start_edge(vehicle_id)
        while self.minute < target:
            self.minute += 1
            for vehicle_id, motion in self._motion.items():
                edge = motion.edge
                if edge is not None:
                    distance = edge["distance_km"] / edge["total_minutes"]
                    motion.uncommitted_distance += distance
                    edge["remaining_minutes"] -= 1
                    edge["remaining_distance_km"] = max(
                        0.0, edge["distance_km"] * edge["remaining_minutes"] / edge["total_minutes"]
                    )
                    if edge["remaining_minutes"] <= 0:
                        motion.node = edge["to_node"]
                        motion.edge = None
                        await self._sync_motion(vehicle_id)
                        await self._at_node(vehicle_id)
                if motion.edge is None:
                    self._start_edge(vehicle_id)
            await self._release_orders()
        for vehicle_id in self._motion:
            await self._sync_motion(vehicle_id)
        await self._event({"type": "clock"})
        return await self.state()

    async def finish(self) -> dict[str, Any]:
        """Continue existing commitments to the shift boundary, with no new assignments."""
        return await self.advance(HORIZON - self.minute)

    async def close(self) -> None:
        if not self._closed:
            await self.backend.close()
            self._closed = True
