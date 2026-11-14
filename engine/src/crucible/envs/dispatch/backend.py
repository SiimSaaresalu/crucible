"""HTTP adapter used by the private simulator, with an isolated SQLite application per task."""

import secrets
import tempfile
from pathlib import Path
from typing import Any

import httpx

from crucible.envs.dispatch.backend_service.app import create_app


class DispatchBackend:
    """Runs the same FastAPI routes locally or connects to an independently running service.

    ASGITransport avoids ephemeral listening sockets for local evaluation; every operation is
    still a serialized HTTP request through FastAPI, backed by a real on-disk SQLite database.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        simulator_token: str | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        self._directory = (
            tempfile.TemporaryDirectory(prefix="crucible-dispatch-") if db_path is None else None
        )
        if db_path is not None:
            self.db_path = Path(db_path)
        else:
            assert self._directory is not None
            self.db_path = Path(self._directory.name) / "dispatch.sqlite3"
        self._token = simulator_token or secrets.token_urlsafe(32)
        self._app = create_app(self.db_path, self._token) if base_url is None else None
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._app) if self._app else None,
            base_url=base_url or "http://dispatch.local",
            headers={"X-Simulator-Token": self._token},
        )

    async def _request(self, method: str, path: str, data: Any = None) -> Any:
        response = await self._client.request(method, path, json=data)
        if response.is_error:
            detail = response.json().get("detail", "Dispatch request failed")
            raise ValueError(detail if isinstance(detail, str) else "Invalid dispatch request")
        return response.json()

    async def reset(
        self, vehicles: list[dict[str, Any]], depot: str, *, version: int = 1
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"vehicles": vehicles, "depot": depot}
        if version != 1:
            payload["version"] = version
        return await self._request("POST", "/reset", payload)

    async def create_order(self, order: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/orders", order)

    async def state(self) -> dict[str, Any]:
        return await self._request("GET", "/state")

    async def submit_plan(
        self, routes: dict[str, list[str]] | dict[str, list[list[str]]]
    ) -> dict[str, Any]:
        return await self._request("POST", "/plans", {"routes": routes})

    async def simulator_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/events", event)

    async def history(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/history")

    async def close(self) -> None:
        await self._client.aclose()
        if self._app is not None:
            self._app.state.store.close()
        if self._directory is not None:
            self._directory.cleanup()
