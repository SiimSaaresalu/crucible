"""Independent HTTP service: uvicorn crucible.envs.dispatch.backend_service.app:app."""

import os
import secrets
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException

from crucible.envs.dispatch.backend_service.database import DispatchError, DispatchStore
from crucible.envs.dispatch.backend_service.models import Order, Plan, Reset, SimulatorEvent


def create_app(db_path: str | Path = ":memory:", simulator_token: str | None = None) -> FastAPI:
    service = FastAPI(title="Dispatch backend", docs_url=None, redoc_url=None)
    store = DispatchStore(db_path)
    token = simulator_token or secrets.token_urlsafe(32)
    service.state.store = store

    def authorize(x_simulator_token: Annotated[str | None, Header()] = None) -> None:
        if x_simulator_token is None or not secrets.compare_digest(x_simulator_token, token):
            raise HTTPException(status_code=403, detail="Simulator authentication required")

    def invoke(method: Any, request: Any = None) -> Any:
        try:
            return method() if request is None else method(request)
        except DispatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @service.get("/state")
    def state() -> Any:
        return invoke(store.state)

    @service.post("/plans", dependencies=[Depends(authorize)])
    def plan(request: Plan) -> Any:
        return invoke(store.submit_plan, request)

    @service.post("/reset", dependencies=[Depends(authorize)])
    def reset(request: Reset) -> Any:
        return invoke(store.reset, request)

    @service.post("/orders", dependencies=[Depends(authorize)])
    def order(request: Order) -> Any:
        return invoke(store.create_order, request)

    @service.post("/events", dependencies=[Depends(authorize)])
    def event(request: SimulatorEvent) -> Any:
        return invoke(store.simulator_event, request)

    @service.get("/history", dependencies=[Depends(authorize)])
    def history() -> Any:
        return invoke(store.history)

    return service


# The standalone service uses an explicit persistent DB and a capability supplied by its owner.
app = create_app(
    os.environ.get("DISPATCH_DATABASE", ":memory:"), os.environ.get("DISPATCH_SIMULATOR_TOKEN")
)
