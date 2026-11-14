"""The independent backend process persists verified operations across TCP restarts."""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from crucible.envs.dispatch.backend import DispatchBackend
from crucible.envs.dispatch.tests.live import free_port


@asynccontextmanager
async def backend_process(database: Path, token: str) -> AsyncGenerator[str]:
    port = free_port()
    environment = {
        **os.environ,
        "DISPATCH_DATABASE": str(database),
        "DISPATCH_SIMULATOR_TOKEN": token,
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "crucible.envs.dispatch.backend_service.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        async with asyncio.timeout(15), httpx.AsyncClient() as client:
            while True:
                if process.returncode is not None:
                    raise AssertionError("Independent dispatch HTTP backend failed to start")
                try:
                    response = await client.get(f"{url}/openapi.json")
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.02)
        yield url
    finally:
        process.terminate()
        async with asyncio.timeout(10):
            await process.wait()


async def test_independent_http_service_persists_deliveries_across_restart(tmp_path: Path) -> None:
    database = tmp_path / "dispatch.sqlite3"
    token = "independent-service-test-capability"
    async with backend_process(database, token) as url:
        adapter = DispatchBackend(base_url=url, simulator_token=token)
        try:
            await adapter.reset([{"id": "v0", "capacity": 6, "node": "depot"}], "depot")
            await adapter.create_order(
                {
                    "id": "a",
                    "destination": "customer",
                    "size": 2,
                    "deadline": 30,
                    "value": 80,
                    "grace": 20,
                    "released_at": 0,
                }
            )
            await adapter.submit_plan({"v0": ["a"]})
            async with httpx.AsyncClient() as client:
                rejected = await client.post(
                    f"{url}/events",
                    json={"type": "deliver", "minute": 0, "vehicle_id": "v0", "order_id": "a"},
                )
                assert rejected.status_code == 403
            await adapter.simulator_event(
                {
                    "type": "pickup",
                    "minute": 0,
                    "vehicle_id": "v0",
                    "order_ids": ["a"],
                }
            )
            await adapter.simulator_event(
                {
                    "type": "move",
                    "minute": 10,
                    "vehicle_id": "v0",
                    "node": "customer",
                    "distance_delta": 2,
                }
            )
            await adapter.simulator_event(
                {
                    "type": "deliver",
                    "minute": 10,
                    "vehicle_id": "v0",
                    "order_id": "a",
                }
            )
            expected_state = await adapter.state()
            expected_history = await adapter.history()
            assert expected_state["orders"][0]["status"] == "delivered"
            assert expected_state["orders"][0]["delivered_at"] == 10
            assert database.is_file()
        finally:
            await adapter.close()
    async with backend_process(database, token) as url:
        adapter = DispatchBackend(base_url=url, simulator_token=token)
        try:
            assert await adapter.state() == expected_state
            assert await adapter.history() == expected_history
        finally:
            await adapter.close()
