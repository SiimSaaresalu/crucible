"""Physical movement, public information and exogenous scheduling guarantees."""

import json

import pytest

from crucible.envs.dispatch.world import (
    DispatchSimulation,
    Disruption,
    Order,
    Road,
    World,
    make_world,
)


async def small_simulation() -> DispatchSimulation:
    world = World(
        (Order("a", "n14", 2, 80, 80.0, 20, 0), Order("b", "n18", 2, 80, 60.0, 20, 0)),
        (Road("n12", "n13", 2.0, 45), Road("n13", "n14", 2.0, 5), Road("n13", "n18", 2.0, 5)),
        (Disruption(30, 90, "n12", "n13", 1, True),),
        (8, 8, 8, 8),
    )

    def generate(seed: int, salt: str | None) -> World:
        return world

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("crucible.envs.dispatch.world.make_world", generate)
        sim = await DispatchSimulation.create(0)
    return sim


def test_seeded_world_distribution_and_salt() -> None:
    for seed in range(20):
        world = make_world(seed)
        assert 28 <= len(world.orders) <= 36
        assert 8 <= sum(o.released_at == 0 for o in world.orders) <= 12
        assert len(world.capacities) == 4
        assert all(6 <= cap <= 10 for cap in world.capacities)
        assert world == make_world(seed)
        assert world != make_world(seed, "secret")
        assert world.total_value == sum(o.value for o in world.orders)
    assert make_world(0).roads != make_world(1).roads


async def test_noop_releases_all_orders_without_driving() -> None:
    sim = await DispatchSimulation.create(0, salt="test-salt")
    try:
        initial = await sim.state()
        assert len(initial["orders"]) < len(sim.world.orders)
        final = await sim.finish()
        assert final["minute"] == 360
        assert len(final["orders"]) == len(sim.world.orders)
        assert all(v["distance"] == 0 for v in final["vehicles"])
        assert all(o["status"] == "unassigned" for o in final["orders"])
    finally:
        await sim.close()


async def test_public_views_hide_unreleased_orders_and_future_traffic() -> None:
    sim = await DispatchSimulation.create(5)
    try:
        state = await sim.state()
        text = json.dumps(state, allow_nan=False)
        for order in sim.world.orders:
            if order.released_at > 0:
                assert f'"{order.id}"' not in text
        assert state["city_map"]["traffic"] == []
        assert "disruptions" not in text
        assert "total_value" not in text
        assert "salt" not in text
    finally:
        await sim.close()


async def test_pickup_locked_edge_suffix_reroute_and_partial_distance() -> None:
    sim = await small_simulation()
    try:
        first = await sim.submit_plan({"v0": ["a", "b"]})
        vehicle = first["vehicles"][0]
        assert vehicle["node"] == "n12"
        assert set(vehicle["cargo"]) == {"a", "b"}
        assert vehicle["distance"] == pytest.approx(2 * 30 / 45)
        assert vehicle["edge"]["to_node"] == "n13"
        assert vehicle["edge"]["remaining_minutes"] == 15
        # Current closure cannot undo an already entered edge. Reordering customer
        # stops changes the suffix after that edge; it cannot teleport the van.
        final = await sim.submit_plan({"v0": ["b", "a"]})
        orders = {o["id"]: o for o in final["orders"]}
        assert orders["b"]["delivered_at"] == 50
        assert orders["a"]["delivered_at"] == 60
        assert final["vehicles"][0]["distance"] == pytest.approx(8.0)
        ledger = await sim.backend.history()
        assert next(e for e in ledger if e["type"] == "pickup")["minute"] == 0
        assert [e["order_id"] for e in ledger if e["type"] == "deliver"] == ["b", "a"]
    finally:
        await sim.close()


async def test_invalid_plan_is_atomic_and_does_not_advance_clock() -> None:
    sim = await small_simulation()
    try:
        before = await sim.state()
        history = await sim.backend.history()
        with pytest.raises(ValueError):
            await sim.submit_plan({"v0": ["a", "a"]})
        assert await sim.state() == before
        assert await sim.backend.history() == history
        assert sim.minute == 0
    finally:
        await sim.close()


async def test_future_order_cannot_be_assigned() -> None:
    sim = await DispatchSimulation.create(8)
    try:
        future = next(o for o in sim.world.orders if o.released_at > 0)
        with pytest.raises(ValueError):
            await sim.submit_plan({"v0": [future.id]})
        assert sim.minute == 0
    finally:
        await sim.close()
