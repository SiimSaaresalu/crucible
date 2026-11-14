"""Tool definitions, calls, results and observations."""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, create_model

ToolKind = Literal["read", "action", "clock", "end"]


class ToolSpec(BaseModel):
    """One tool. `args_model` validates calls and makes the JSON Schema."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    description: str
    kind: ToolKind
    args_model: type[BaseModel] = Field(exclude=True)

    def json_schema(self) -> dict[str, Any]:
        """JSON Schema of the arguments."""
        return self.args_model.model_json_schema()


class ToolCall(BaseModel):
    """A request from a policy to run one tool."""

    model_config = ConfigDict(frozen=True)

    name: str
    args: dict[str, JsonValue] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """Explicit result of a tool call. Never holds hidden state."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    content: JsonValue = None
    error: str | None = None


class Observation(BaseModel):
    """What a policy sees before each action."""

    model_config = ConfigDict(frozen=True)

    text: str = ""
    data: dict[str, JsonValue] = Field(default_factory=dict)
    last_result: ToolResult | None = None


def _error_text(name: str, exc: ValidationError) -> str:
    """Readable text from argument names, messages and the caller's own values."""
    parts = [
        f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}"
        for err in exc.errors(include_url=False, include_input=False, include_context=False)
    ]
    return f"{name}: " + "; ".join(parts)


def validate_call(specs: Mapping[str, ToolSpec], call: ToolCall) -> BaseModel | ToolResult:
    """Return the parsed args, or an `ok=False` result. Never changes state."""
    spec = specs.get(call.name)
    if spec is None:
        known = ", ".join(sorted(specs))
        return ToolResult(ok=False, error=f"Unknown tool {call.name!r}. Known tools: {known}")
    try:
        return spec.args_model.model_validate(call.args)
    except ValidationError as exc:
        return ToolResult(ok=False, error=_error_text(call.name, exc))


def make_tool(name: str, description: str, kind: ToolKind, **fields: Any) -> ToolSpec:
    """Build a `ToolSpec` from pydantic field definitions, e.g. `n=(int, Field(ge=1))`."""
    model = create_model(f"{name}_args", **fields)
    return ToolSpec(name=name, description=description, kind=kind, args_model=model)


def tool_map(specs: Sequence[ToolSpec]) -> dict[str, ToolSpec]:
    """Index tool specs by name."""
    return {s.name: s for s in specs}


__all__ = [
    "Observation",
    "ToolCall",
    "ToolKind",
    "ToolResult",
    "ToolSpec",
    "make_tool",
    "tool_map",
    "validate_call",
]
