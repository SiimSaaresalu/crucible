"""Typed views of `incident@1` tool results, for policies.

Only what a tool result shows: no import from `nightshift.tasks`. Every parser raises
`ValueError` if the result is not `ok` or its content has the wrong shape.
"""

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from nightshift.core.tools import ToolCall, ToolResult


class AlertView(BaseModel):
    """One alert as shown by `get_alerts` or `wait`."""

    model_config = ConfigDict(frozen=True)

    name: str
    service: str
    started_at: int


class DeployView(BaseModel):
    """One deploy as shown by `list_deploys`."""

    model_config = ConfigDict(frozen=True)

    id: str
    service: str
    minute: int


class LogLineView(BaseModel):
    """One log line as shown by `read_logs`."""

    model_config = ConfigDict(frozen=True)

    minute: int
    level: str
    msg: str


_ALERTS = TypeAdapter(list[AlertView])
_DEPLOYS = TypeAdapter(list[DeployView])
_LOGS = TypeAdapter(list[LogLineView])


def _content(result: ToolResult) -> dict[str, JsonValue]:
    if not result.ok:
        raise ValueError(f"Tool result is not ok: {result.error}")
    if not isinstance(result.content, dict):
        raise ValueError(f"Tool result content is not an object: {result.content!r}")
    return result.content


def _rows(result: ToolResult, keys: tuple[str, ...]) -> JsonValue:
    content = _content(result)
    for key in keys:
        if key in content:
            return content[key]
    raise ValueError(f"Tool result has none of the fields {', '.join(keys)}")


def _parse[T](adapter: TypeAdapter[list[T]], value: JsonValue, what: str) -> list[T]:
    try:
        return adapter.validate_python(value)
    except ValidationError as exc:
        raise ValueError(f"Malformed {what} in tool result: {exc}") from exc


def parse_alerts(result: ToolResult) -> list[AlertView]:
    """Alerts from a `get_alerts` result (`alerts`) or a `wait` result (`new_alerts`)."""
    return _parse(_ALERTS, _rows(result, ("alerts", "new_alerts")), "alerts")


def parse_deploys(result: ToolResult) -> list[DeployView]:
    """Deploys from a `list_deploys` result."""
    return _parse(_DEPLOYS, _rows(result, ("deploys",)), "deploys")


def parse_logs(result: ToolResult) -> list[LogLineView]:
    """Log lines from a `read_logs` result."""
    return _parse(_LOGS, _rows(result, ("lines",)), "log lines")


def result_minute(result: ToolResult) -> int:
    """The minute at the start of the call that made this result."""
    minute = _content(result).get("minute")
    if isinstance(minute, bool) or not isinstance(minute, int):
        raise ValueError(f"Tool result has no integer minute: {minute!r}")
    return minute


def call(name: str, **args: JsonValue) -> ToolCall:
    """Shorthand for `ToolCall(name=name, args=args)`."""
    return ToolCall(name=name, args=dict(args))


__all__ = [
    "AlertView",
    "DeployView",
    "LogLineView",
    "call",
    "parse_alerts",
    "parse_deploys",
    "parse_logs",
    "result_minute",
]
