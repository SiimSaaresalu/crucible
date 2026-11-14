"""V2 physical tours, compact public information and deterministic replay."""

import json
from collections.abc import Callable
from typing import Any

import pytest

from crucible.envs.dispatch.v2.scenarios import REGIMES, make_world
from crucible.envs.dispatch.v2.world import DispatchSimulation
from crucible.envs.dispatch.world import Disruption, Order, Road, World


def fixed_world(world: World) -> Callable[..., World]:
    def factory(*_args: object, **_kwargs: object) -> World:
        return world

    return factory


async def small_simulation(*, disrupted: bool = False) -> DispatchSimulation:
    world = World(
        (Order("a", "n14", 2, 200, 80.0, 20, 0), Order("b", "n18", 2, 250, 60.0, 20, 0)),
        (Road("n12", "n13", 2.0, 45), Road("n13", "n14", 2.0, 5), Road("n13", "n18", 2.0, 5)),
        (Disruption(30, 90, "n12", "n13", 1, True),) if disrupted else (),
        (2,),
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("crucible.envs.dispatch.v2.world.make_world", fixed_world(world))
        return await DispatchSimulation.create(0)


def test_regimes_are_deterministic_salted_and_action_independent() -> None:
    for name in REGIMES:
        for seed in range(5):
            world = make_world(seed, regime=name)
            assert world == make_world(seed, regime=name)
            assert world != make_world(seed, "private-salt", regime=name)
            assert 40 <= len(world.orders) <= 55
            assert len(world.capacities) == 3
            assert all(6 <= capacity <= 8 for capacity in world.capacities)
            assert all(20 <= order.value <= 300 for order in world.orders)
            assert all(order.released_at < order.deadline <= 350 for order in world.orders)
    assert make_world(0, regime="A").roads == make_world(0, regime="D").roads


async def test_two_tours_physically_return_and_reload_with_capacity_per_load() -> None:
    sim = await small_simulation()
    try:
        first = await sim.submit_plan({"v0": [["a"], ["b"]]})
        vehicle = first["vehicles"][0]
        assert vehicle["cargo"] == ["a"]
        assert vehicle["tour_active"] is True
        assert vehicle["edge"]["remaining_minutes"] == 15
        assert vehicle["node"] == "n12"
        assert vehicle["distance"] == pytest.approx(2 * 30 / 45)
        final = await sim.finish()
        vehicle = final["vehicles"][0]
        assert vehicle["node"] == "n12"
        assert vehicle["tours"] == vehicle["cargo"] == []
        assert vehicle["returning"] is False
        assert vehicle["distance"] == pytest.approx(16.0)
        history = await sim.backend.history()
        assert [(e["order_ids"], e["minute"]) for e in history if e["type"] == "pickup"] == [
            (["a"], 0),
            (["b"], 100),
        ]
        assert [(e["order_id"], e["minute"]) for e in history if e["type"] == "deliver"] == [
            ("a", 50),
            ("b", 150),
        ]
        assert [e["minute"] for e in history if e["type"] == "depot_return"] == [100, 200]
    finally:
        await sim.close()


async def test_loaded_sequence_and_road_edge_are_irreversible() -> None:
    sim = await small_simulation(disrupted=True)
    try:
        first = await sim.submit_plan({"v0": [["a"], ["b"]]})
        before_history = await sim.backend.history()
        with pytest.raises(ValueError):
            await sim.submit_plan({"v0": [["b"], ["a"]]})
        assert await sim.state() == first
        assert await sim.backend.history() == before_history
        # Cancel only the not-yet-picked second tour. The old edge completes
        # despite its current closure; the final physical return remains owed.
        second = await sim.submit_plan({"v0": [["a"]]})
        assert second["vehicles"][0]["returning"] is True
        assert next(o for o in second["orders"] if o["id"] == "a")["delivered_at"] == 50
        final = await sim.finish()
        assert final["vehicles"][0]["node"] == "n12"
        assert final["vehicles"][0]["distance"] == pytest.approx(8.0)
        assert next(o for o in final["orders"] if o["id"] == "b")["status"] == "unassigned"
    finally:
        await sim.close()


async def test_shift_boundary_charges_partial_edge_and_never_starts_another() -> None:
    sim = await small_simulation()
    try:
        await sim.advance(330)
        await sim.submit_plan({"v0": [["a"]]})
        final = await sim.state()
        vehicle = final["vehicles"][0]
        assert vehicle["edge"]["remaining_minutes"] == 15
        assert vehicle["distance"] == pytest.approx(2 * 30 / 45)
        assert vehicle["cargo"] == ["a"]
        assert all(o["status"] != "delivered" for o in final["orders"])
        assert max(e["minute"] for e in await sim.backend.history() if "minute" in e) == 360
        with pytest.raises(ValueError, match="over"):
            await sim.submit_plan({})
    finally:
        await sim.close()


async def test_compact_public_state_and_static_map_do_not_leak_future() -> None:
    sim = await DispatchSimulation.create(5, salt="private-secret")
    try:
        static = sim.city_map()
        initial = await sim.state()
        text = json.dumps(initial, allow_nan=False)
        assert "city_map" not in initial
        assert "roads" not in initial
        assert "travel_times" not in initial
        assert "distances" not in initial
        assert "private-secret" not in text
        assert "total_value" not in text
        assert "disruption" not in text
        for order in sim.world.orders:
            if order.released_at > 0:
                assert f'"{order.id}"' not in text
        later = await sim.submit_plan({})
        assert later["traffic"]
        assert set(later["traffic"][0]) == {"a", "b", "multiplier", "closed"}
        assert static == sim.city_map()
    finally:
        await sim.close()


async def replay(seed: int, salt: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sim = await DispatchSimulation.create(seed, salt=salt, regime="D")
    try:
        states = [await sim.state()]
        for _ in range(12):
            available = [o for o in states[-1]["orders"] if o["status"] == "unassigned"]
            routes: dict[str, list[list[str]]] = {}
            for vehicle in states[-1]["vehicles"]:
                if not vehicle["tours"] and available:
                    routes[vehicle["id"]] = [[available.pop(0)["id"]]]
            states.append(await sim.submit_plan(routes))
        return states, await sim.backend.history()
    finally:
        await sim.close()


async def test_salted_full_episode_replays_identical_states_and_transactions() -> None:
    first = await replay(7, "held-fixed")
    second = await replay(7, "held-fixed")
    assert first == second
    assert any(e["type"] == "deliver" for e in first[1])
    assert first != await replay(7, "different")


async def test_noop_and_episode_databases_are_independent() -> None:
    first = await DispatchSimulation.create(0)
    second = await DispatchSimulation.create(0)
    try:
        assert first.backend.db_path != second.backend.db_path
        initial = await second.state()
        final = await first.finish()
        assert len(final["orders"]) == len(first.world.orders)
        assert all(v["distance"] == 0 for v in final["vehicles"])
        assert await second.state() == initial
    finally:
        await first.close()
        await second.close()


async def test_depot_customers_settle_without_zero_time_loop() -> None:
    world = World(
        (Order("a", "n12", 2, 60, 80.0, 20, 0), Order("b", "n12", 2, 60, 60.0, 20, 0)),
        (Road("n12", "n13", 2.0, 5),),
        (),
        (2,),
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("crucible.envs.dispatch.v2.world.make_world", fixed_world(world))
        sim = await DispatchSimulation.create(0)
    try:
        state = await sim.submit_plan({"v0": [["a"], ["b"]]})
        assert all(o["delivered_at"] == 0 for o in state["orders"])
        assert state["vehicles"][0]["distance"] == 0
        assert state["vehicles"][0]["returning"] is False
    finally:
        await sim.close()


async def test_depot_arrival_at_shift_end_does_not_collect_another_load() -> None:
    world = World(
        (Order("a", "n13", 2, 350, 80.0, 20, 0), Order("b", "n13", 2, 350, 60.0, 20, 0)),
        (Road("n12", "n13", 2.0, 15),),
        (),
        (2,),
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("crucible.envs.dispatch.v2.world.make_world", fixed_world(world))
        sim = await DispatchSimulation.create(0)
    try:
        await sim.advance(330)
        final = await sim.submit_plan({"v0": [["a"], ["b"]]})
        assert final["vehicles"][0]["node"] == "n12"
        assert final["vehicles"][0]["cargo"] == []
        assert final["vehicles"][0]["returning"] is False
        assert [e["order_ids"] for e in await sim.backend.history() if e["type"] == "pickup"] == [
            ["a"]
        ]
    finally:
        await sim.close()
