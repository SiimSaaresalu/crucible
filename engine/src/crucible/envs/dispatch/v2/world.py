"""Capacity-limited online tours with immutable loads and physical depot returns."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from crucible.envs.dispatch.v2.scenarios import make_world
from crucible.envs.dispatch.world import (
    DECISION_INTERVAL,
    DEPOT,
    DRIVING_COST_PER_KM,
    HORIZON,
    World,
    _Motion,  # pyright: ignore[reportPrivateUsage] -- shared internal movement record
)
from crucible.envs.dispatch.world import (
    DispatchSimulation as V1Simulation,
)


class DispatchSimulation(V1Simulation):
    """V2 state machine; shared pathfinding, HTTP ledger and economic units."""

    def __init__(self, world: World, backend: Any) -> None:
        super().__init__(world, backend)
        self.minute: int = 0
        self._motion = {f"v{i}": _Motion() for i in range(len(world.capacities))}

    @classmethod
    async def create(
        cls, seed: int, salt: str | None = None, backend: Any = None, *, regime: str = "A"
    ) -> DispatchSimulation:
        if backend is None:
            from crucible.envs.dispatch.backend import DispatchBackend

            backend = DispatchBackend()
        sim = cls(make_world(seed, salt, regime=regime), backend)
        await backend.reset(
            [
                {"id": f"v{i}", "capacity": cap, "node": DEPOT}
                for i, cap in enumerate(sim.world.capacities)
            ],
            DEPOT,
            version=2,
        )
        await sim._release_orders()
        sim._snapshot = await backend.state()
        return sim

    def city_map(self) -> dict[str, Any]:
        """Static roads only; current observed traffic is available through state."""
        return {
            "map_version": "dispatch@2-grid-v1",
            "depot": DEPOT,
            "nodes": [{"id": f"n{i}", "x": (i % 5) * 2, "y": (i // 5) * 2} for i in range(25)],
            "roads": [asdict(road) for road in self.world.roads],
        }

    async def state(self) -> dict[str, Any]:
        self._snapshot = await self.backend.state()
        traffic = [
            {"a": d.a, "b": d.b, "multiplier": d.multiplier, "closed": d.closed}
            for d in self._active_traffic()
        ]
        revision = hashlib.sha256(json.dumps(traffic, sort_keys=True).encode()).hexdigest()[:16]
        return {
            **self._snapshot,
            "minute": self.minute,
            "horizon": HORIZON,
            "decision_interval": DECISION_INTERVAL,
            "driving_cost_per_km": DRIVING_COST_PER_KM,
            "load_service_minutes": 0,
            "service_minutes": 0,
            "traffic": traffic,
            "traffic_revision": revision,
        }

    async def submit_plan(self, routes: dict[str, Any]) -> dict[str, Any]:
        if self.minute >= HORIZON:
            raise ValueError("Shift is over")
        self._snapshot = await self.backend.submit_plan(routes)
        return await self.advance(DECISION_INTERVAL)

    async def _at_node(self, vehicle_id: str) -> None:
        motion = self._motion[vehicle_id]
        vehicle = self._vehicle(vehicle_id)
        if motion.edge is not None:
            return
        # Settle zero-duration depot/customer transitions until the next movement.
        # Each iteration consumes a return or at least one order; finite plans
        # therefore cannot create a zero-time loop, including depot customers.
        while True:
            changed = False
            if vehicle["returning"] and motion.node == DEPOT:
                await self._sync_motion(vehicle_id)
                await self._event({"type": "depot_return", "vehicle_id": vehicle_id})
                vehicle = self._vehicle(vehicle_id)
                changed = True
            if (
                self.minute < HORIZON
                and motion.node == DEPOT
                and not vehicle["tour_active"]
                and not vehicle["returning"]
                and vehicle["tours"]
            ):
                await self._sync_motion(vehicle_id)
                await self._event(
                    {
                        "type": "pickup",
                        "vehicle_id": vehicle_id,
                        "order_ids": list(vehicle["tours"][0]),
                    }
                )
                vehicle = self._vehicle(vehicle_id)
                changed = True
            orders = {o["id"]: o for o in self._snapshot["orders"]}
            while (
                vehicle["tour_active"]
                and vehicle["tours"]
                and orders[vehicle["tours"][0][0]]["destination"] == motion.node
            ):
                await self._sync_motion(vehicle_id)
                await self._event(
                    {
                        "type": "deliver",
                        "vehicle_id": vehicle_id,
                        "order_id": vehicle["tours"][0][0],
                    }
                )
                vehicle = self._vehicle(vehicle_id)
                changed = True
            if not changed:
                return

    async def _begin_edge(self, vehicle_id: str) -> None:
        if self.minute >= HORIZON:
            return
        motion = self._motion[vehicle_id]
        if motion.edge is not None:
            return
        vehicle = self._vehicle(vehicle_id)
        if vehicle["returning"]:
            target = DEPOT
        elif vehicle["tour_active"]:
            orders = {o["id"]: o for o in self._snapshot["orders"]}
            target = orders[vehicle["tours"][0][0]]["destination"]
        elif vehicle["tours"]:
            target = DEPOT
        else:
            return
        if target == motion.node:
            return
        graph = self._graph()
        times, _, previous = self._paths(graph, motion.node)
        if target not in times:
            return  # No future closure schedule is consulted.
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
        # Persist edge entry, not merely edge completion, so commitments are public.
        await self._sync_motion(vehicle_id)

    async def advance(self, minutes: int) -> dict[str, Any]:
        if minutes < 0:
            raise ValueError("Cannot reverse virtual time")
        target = min(self.minute + minutes, HORIZON)
        if self.minute < HORIZON:
            for vehicle_id in self._motion:
                if self._motion[vehicle_id].edge is None:
                    await self._at_node(vehicle_id)
                    await self._begin_edge(vehicle_id)
        while self.minute < target:
            self.minute += 1
            for vehicle_id, motion in self._motion.items():
                edge = motion.edge
                if edge is not None:
                    motion.uncommitted_distance += edge["distance_km"] / edge["total_minutes"]
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
                    await self._begin_edge(vehicle_id)
            await self._release_orders()
        for vehicle_id in self._motion:
            await self._sync_motion(vehicle_id)
        await self._event({"type": "clock"})
        return await self.state()
