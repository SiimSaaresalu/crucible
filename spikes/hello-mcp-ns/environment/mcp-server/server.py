"""Night Shift-like MCP server: typed tools + token-gated /grade route."""
import os
from typing import Literal

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

mcp = FastMCP("night-shift-spike")

GRADE_TOKEN = os.environ["GRADE_TOKEN"]
STATE = {"calls": [], "resolved": None}

METRICS = {
    "api": {"p99_ms": 2400, "error_rate": 0.31},
    "cache": {"hit_rate": 0.12, "evictions_per_s": 9000},
    "db": {"connections": 500, "max_connections": 500},
}


@mcp.tool()
def get_metrics(service: Literal["api", "cache", "db"]) -> dict:
    """Return current metrics for a service."""
    STATE["calls"].append(["get_metrics", service])
    return METRICS[service]


@mcp.tool()
def resolve_incident(
    root_cause: Literal["db_connection_exhaustion", "cache_stampede", "bad_deploy"],
) -> str:
    """Declare the root cause and resolve the incident."""
    STATE["calls"].append(["resolve_incident", root_cause])
    STATE["resolved"] = root_cause
    return f"incident resolved: {root_cause}"


@mcp.custom_route("/grade", methods=["GET"])
async def grade(request: Request) -> JSONResponse:
    if request.headers.get("x-grade-token") != GRADE_TOKEN:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    ok = STATE["resolved"] == "db_connection_exhaustion"
    return JSONResponse({"reward": 1 if ok else 0, "calls": STATE["calls"]})


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
