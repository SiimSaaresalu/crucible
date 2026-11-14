"""Identical seed, salt and actions reproduce observations and committed events."""

from typing import Any

from crucible.envs.dispatch.world import DispatchSimulation


async def replay(seed: int, salt: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sim = await DispatchSimulation.create(seed, salt=salt)
    try:
        states = [await sim.state()]
        # A simple observation-only route: one available parcel per idle van.
        for _ in range(12):
            state = states[-1]
            available = [o for o in state["orders"] if o["status"] == "unassigned"]
            routes: dict[str, list[str]] = {}
            for vehicle in state["vehicles"]:
                if not vehicle["route"] and available:
                    routes[vehicle["id"]] = [available.pop(0)["id"]]
            states.append(await sim.submit_plan(routes))
        return states, await sim.backend.history()
    finally:
        await sim.close()


async def test_salted_replay_includes_trajectories_deliveries_and_ledger() -> None:
    one = await replay(7, "held-fixed")
    two = await replay(7, "held-fixed")
    assert one == two
    assert any(e["type"] == "deliver" for e in one[1])
    assert one != await replay(7, "different-salt")
