"""Persistent HTTP backend and parcel integrity checks, independent of the world."""

from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue

from crucible.envs.dispatch.backend import DispatchBackend
from crucible.envs.dispatch.backend_service.app import create_app


async def initialized(tmp_path: Path) -> DispatchBackend:
    backend = DispatchBackend(tmp_path / "dispatch.sqlite3", "private-test-token")
    await backend.reset(
        [
            {"id": "v1", "capacity": 3, "node": "depot"},
            {"id": "v2", "capacity": 3, "node": "depot"},
        ],
        "depot",
    )
    for oid in ("o1", "o2", "o3"):
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
    return backend


async def test_real_http_persistence_and_delivery(tmp_path: Path) -> None:
    backend = await initialized(tmp_path)
    await backend.submit_plan({"v1": ["o1"]})
    await backend.simulator_event(
        {"type": "pickup", "vehicle_id": "v1", "order_ids": ["o1"], "minute": 0}
    )
    await backend.simulator_event(
        {"type": "move", "vehicle_id": "v1", "node": "customer", "distance_delta": 2, "minute": 8}
    )
    await backend.simulator_event(
        {"type": "deliver", "vehicle_id": "v1", "order_id": "o1", "minute": 8}
    )
    before = await backend.state()
    assert before["orders"][0]["delivered_at"] == 8
    assert before["vehicles"][0]["distance"] == 2
    await backend.close()
    reopened = DispatchBackend(tmp_path / "dispatch.sqlite3", "private-test-token")
    assert await reopened.state() == before
    assert (await reopened.history())[-1]["type"] == "deliver"
    await reopened.close()


@pytest.mark.parametrize(
    "routes",
    [
        {"bad": ["o1"]},
        {"v1": ["future"]},
        {"v1": ["o1", "o1"]},
        {"v1": ["o1"], "v2": ["o1"]},
        {"v1": ["o1", "o2"]},
    ],
)
async def test_invalid_plan_is_atomic(tmp_path: Path, routes: dict[str, list[str]]) -> None:
    backend = await initialized(tmp_path)
    before, events = await backend.state(), await backend.history()
    with pytest.raises(ValueError):
        await backend.submit_plan(routes)
    assert await backend.state() == before
    assert await backend.history() == events
    await backend.close()


async def test_loaded_parcel_cannot_teleport_or_be_delivered_twice(tmp_path: Path) -> None:
    backend = await initialized(tmp_path)
    await backend.submit_plan({"v1": ["o1"]})
    with pytest.raises(ValueError):
        await backend.simulator_event(
            {"type": "deliver", "vehicle_id": "v1", "order_id": "o1", "minute": 0}
        )
    await backend.simulator_event(
        {"type": "pickup", "vehicle_id": "v1", "order_ids": ["o1"], "minute": 0}
    )
    with pytest.raises(ValueError):
        await backend.submit_plan({"v1": [], "v2": ["o1"]})
    with pytest.raises(ValueError):
        await backend.simulator_event(
            {"type": "deliver", "vehicle_id": "v1", "order_id": "o1", "minute": 0}
        )
    with pytest.raises(ValueError):
        await backend.simulator_event(
            {"type": "pickup", "vehicle_id": "v1", "order_ids": ["o1"], "minute": 0}
        )
    await backend.simulator_event(
        {"type": "move", "vehicle_id": "v1", "node": "customer", "distance_delta": 2, "minute": 8}
    )
    await backend.simulator_event(
        {"type": "deliver", "vehicle_id": "v1", "order_id": "o1", "minute": 8}
    )
    with pytest.raises(ValueError):
        await backend.simulator_event(
            {"type": "deliver", "vehicle_id": "v1", "order_id": "o1", "minute": 8}
        )
    await backend.close()


async def test_reassignment_and_locked_edge_preservation(tmp_path: Path) -> None:
    backend = await initialized(tmp_path)
    await backend.submit_plan({"v1": ["o1"], "v2": ["o2"]})
    await backend.simulator_event(
        {
            "type": "move",
            "vehicle_id": "v1",
            "node": "depot",
            "edge": {"from_node": "depot", "to_node": "customer", "remaining_minutes": 4},
            "distance_delta": 1,
            "minute": 4,
        }
    )
    edge = (await backend.state())["vehicles"][0]["edge"]
    await backend.submit_plan({"v1": ["o2"], "v2": ["o1"]})
    assert (await backend.state())["vehicles"][0]["edge"] == edge
    with pytest.raises(ValueError):
        await backend.simulator_event(
            {"type": "pickup", "vehicle_id": "v1", "order_ids": ["o2"], "minute": 4}
        )
    await backend.close()


async def test_capability_and_immutable_orders(tmp_path: Path) -> None:
    app = create_app(tmp_path / "service.sqlite3", "private-capability")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        requests: list[tuple[str, JsonValue]] = [
            ("/events", {"type": "clock", "minute": 1}),
            ("/plans", {"routes": {}}),
            ("/orders", {"id": "fake"}),
            ("/reset", {"vehicles": [{"id": "v", "capacity": 3, "node": "d"}], "depot": "d"}),
        ]
        for path, payload in requests:
            response = await client.post(path, json=payload)
            assert response.status_code == 403
        assert (await client.get("/history")).status_code == 403
    app.state.store.close()
    backend = await initialized(tmp_path)
    before = await backend.state()
    with pytest.raises(ValueError):
        await backend.create_order(
            {
                "id": "o1",
                "destination": "customer",
                "size": 1,
                "deadline": 999,
                "value": 9999,
                "grace": 30,
                "released_at": 0,
            }
        )
    with pytest.raises(ValueError):
        await backend.create_order(
            {
                "id": "future",
                "destination": "customer",
                "size": 1,
                "deadline": 100,
                "value": 100,
                "grace": 30,
                "released_at": 30,
            }
        )
    assert await backend.state() == before
    await backend.close()
