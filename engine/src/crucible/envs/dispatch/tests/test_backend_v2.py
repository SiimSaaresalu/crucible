"""Versioned multi-load ledger, atomic replacements, and authenticated HTTP tests."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from crucible.envs.dispatch.backend import DispatchBackend
from crucible.envs.dispatch.backend_service.app import create_app
from crucible.envs.dispatch.tests.test_backend_http import backend_process


async def initialize(backend: DispatchBackend) -> None:
    await backend.reset(
        [
            {"id": "v0", "capacity": 4, "node": "depot"},
            {"id": "v1", "capacity": 4, "node": "depot"},
        ],
        "depot",
        version=2,
    )
    for oid in ("o1", "o2", "o3", "o4", "o5"):
        await backend.create_order(
            {
                "id": oid,
                "destination": "customer",
                "size": 2,
                "deadline": 60,
                "value": 100,
                "grace": 30,
                "released_at": 0,
            }
        )


@pytest.fixture
async def backend(tmp_path: Path) -> AsyncIterator[DispatchBackend]:
    service = DispatchBackend(tmp_path / "dispatch-v2.sqlite3", "test-private-capability")
    await initialize(service)
    try:
        yield service
    finally:
        await service.close()


async def event(backend: DispatchBackend, kind: str, minute: int, **fields: Any) -> None:
    await backend.simulator_event({"type": kind, "minute": minute, "vehicle_id": "v0", **fields})


async def finish_tour(backend: DispatchBackend, orders: list[str], minute: int) -> None:
    await event(backend, "pickup", minute, order_ids=orders)
    await event(backend, "move", minute + 8, node="customer", distance_delta=2)
    for oid in orders:
        await event(backend, "deliver", minute + 8, order_id=oid)
    returning = (await backend.state())["vehicles"][0]
    assert not returning["cargo"] and returning["returning"]
    await event(backend, "move", minute + 16, node="depot", distance_delta=2)
    await event(backend, "depot_return", minute + 16)


async def test_two_loads_have_separate_capacity_and_durable_history(
    backend: DispatchBackend,
) -> None:
    # Total assigned size 8 exceeds capacity 4; each load remains valid.
    await backend.submit_plan({"v0": [["o1", "o2"], ["o3", "o4"]]})
    await finish_tour(backend, ["o1", "o2"], 0)
    await finish_tour(backend, ["o3", "o4"], 16)
    before, history = await backend.state(), await backend.history()
    vehicle = before["vehicles"][0]
    assert vehicle["node"] == "depot"
    assert vehicle["tours"] == vehicle["locked_route"] == vehicle["cargo"] == []
    assert not vehicle["tour_active"] and not vehicle["returning"]
    assert vehicle["distance"] == 8
    assert [o["delivered_at"] for o in before["orders"][:4]] == [8, 8, 24, 24]
    reopened = DispatchBackend(backend.db_path, "test-private-capability")
    try:
        assert await reopened.state() == before
        assert await reopened.history() == history
    finally:
        await reopened.close()
    assert [e["order_ids"] for e in history if e["type"] == "pickup"] == [
        ["o1", "o2"],
        ["o3", "o4"],
    ]


@pytest.mark.parametrize(
    "routes",
    cast(
        list[dict[str, Any]],
        [
            {"v0": ["o1"]},
            {"v0": [[]]},
            {"v0": [["o1"], ["o2", "o3", "o4"]]},
            {"v0": [["o1"], ["o1"]]},
            {"v0": [["o1", "o1"]]},
            {"v0": [["o1"]], "v1": [["o1"]]},
            {"v1": [["o2"]]},  # v0 omitted, retains o2 reservation.
            {"unknown": [["o1"]]},
            {"v0": [["future"]]},
        ],
    ),
)
async def test_invalid_replacement_preserves_state_time_and_history(
    backend: DispatchBackend, routes: dict[str, Any]
) -> None:
    await backend.submit_plan({"v0": [["o1"], ["o2"]]})
    before, history = await backend.state(), await backend.history()
    with pytest.raises(ValueError):
        await backend.submit_plan(routes)
    assert await backend.state() == before
    assert await backend.history() == history


@pytest.mark.parametrize(
    "routes",
    [
        {"v0": []},
        {"v0": [["o2", "o1"]]},
        {"v0": [["o1"], ["o2"]]},
        {"v0": [["o1", "o2", "o3"]]},
        {"v0": [["o3"]], "v1": [["o1", "o2"]]},
    ],
)
async def test_loaded_sequence_is_immutable(
    backend: DispatchBackend, routes: dict[str, Any]
) -> None:
    await backend.submit_plan({"v0": [["o1", "o2"]]})
    await event(backend, "pickup", 0, order_ids=["o1", "o2"])
    before, history = await backend.state(), await backend.history()
    with pytest.raises(ValueError):
        await backend.submit_plan(routes)
    assert await backend.state() == before
    assert await backend.history() == history


async def test_future_reservations_can_move_atomically_while_cargo_stays(
    backend: DispatchBackend,
) -> None:
    await backend.submit_plan({"v0": [["o1"], ["o2"]], "v1": [["o3"]]})
    await event(backend, "pickup", 0, order_ids=["o1"])
    await backend.submit_plan({"v0": [["o1"], ["o3"]], "v1": [["o2"]]})
    state = await backend.state()
    assert state["vehicles"][0]["cargo"] == ["o1"]
    assert state["vehicles"][0]["tours"] == [["o1"], ["o3"]]
    assert {o["id"]: o["assigned_vehicle"] for o in state["orders"]} == {
        "o1": "v0",
        "o2": "v1",
        "o3": "v0",
        "o4": None,
        "o5": None,
    }


async def test_cancellation_during_return_keeps_physical_commitments(
    backend: DispatchBackend,
) -> None:
    await backend.submit_plan({"v0": [["o1"], ["o2"]]})
    await event(backend, "pickup", 0, order_ids=["o1"])
    await event(backend, "move", 8, node="customer", distance_delta=2)
    await event(backend, "deliver", 8, order_id="o1")
    edge = {"from_node": "customer", "to_node": "depot", "remaining_minutes": 8}
    await event(backend, "move", 8, node="customer", edge=edge)
    await backend.submit_plan({"v0": []})
    state = await backend.state()
    assert state["vehicles"][0]["edge"] == edge
    assert state["vehicles"][0]["returning"]
    assert state["orders"][1]["status"] == "unassigned"
    with pytest.raises(ValueError):
        await event(backend, "depot_return", 8)
    await event(backend, "move", 16, node="depot", distance_delta=2)
    await event(backend, "depot_return", 16)
    assert not (await backend.state())["vehicles"][0]["returning"]


async def test_pickup_and_completion_cannot_skip_tour_transitions(
    backend: DispatchBackend,
) -> None:
    await backend.submit_plan({"v0": [["o1", "o2"], ["o3"]]})
    for ids in (["o1"], ["o3"], ["o1", "o2", "o3"], ["o1", "o1"]):
        with pytest.raises(ValueError):
            await event(backend, "pickup", 0, order_ids=ids)
    await event(backend, "pickup", 0, order_ids=["o1", "o2"])
    with pytest.raises(ValueError):
        await event(backend, "depot_return", 0)
    with pytest.raises(ValueError):
        await event(backend, "deliver", 0, order_id="o1")
    await event(backend, "move", 8, node="customer", distance_delta=2)
    with pytest.raises(ValueError):
        await event(backend, "deliver", 8, order_id="o2")
    await event(backend, "deliver", 8, order_id="o1")
    await event(backend, "deliver", 8, order_id="o2")
    with pytest.raises(ValueError):
        await event(backend, "deliver", 8, order_id="o1")
    with pytest.raises(ValueError):
        await backend.submit_plan({"v0": [["o1"]]})
    await event(backend, "move", 16, node="depot", distance_delta=2)
    with pytest.raises(ValueError):
        await event(backend, "pickup", 16, order_ids=["o3"])
    await event(backend, "depot_return", 16)
    await event(backend, "pickup", 16, order_ids=["o3"])


async def test_v2_events_remain_capability_protected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "auth.sqlite3", "private-capability")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for kind in ("pickup", "move", "deliver", "depot_return"):
                response = await client.post(
                    "/events", json={"type": kind, "minute": 0, "vehicle_id": "v0"}
                )
                assert response.status_code == 403
    finally:
        app.state.store.close()


async def test_v2_two_tours_survive_standalone_http_restart(tmp_path: Path) -> None:
    database = tmp_path / "standalone-v2.sqlite3"
    token = "standalone-private-token"
    async with backend_process(database, token) as url:
        backend = DispatchBackend(database, token, base_url=url)
        try:
            await initialize(backend)
            await backend.submit_plan({"v0": [["o1", "o2"], ["o3", "o4"]]})
            await finish_tour(backend, ["o1", "o2"], 0)
            await finish_tour(backend, ["o3", "o4"], 16)
            before, history = await backend.state(), await backend.history()
        finally:
            await backend.close()
    async with backend_process(database, token) as url:
        backend = DispatchBackend(database, token, base_url=url)
        try:
            assert await backend.state() == before
            assert await backend.history() == history
        finally:
            await backend.close()
