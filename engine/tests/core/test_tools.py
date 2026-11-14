from pydantic import BaseModel, Field

from nightshift.core.tools import (
    ToolCall,
    ToolResult,
    ToolSpec,
    make_tool,
    tool_map,
    validate_call,
)


class GuessArgs(BaseModel):
    n: int = Field(ge=1, le=100)


SPECS = tool_map(
    [ToolSpec(name="guess", description="Guess.", kind="action", args_model=GuessArgs)]
)


def test_json_schema_has_constraints() -> None:
    schema = SPECS["guess"].json_schema()
    assert schema["properties"]["n"]["minimum"] == 1
    assert schema["properties"]["n"]["maximum"] == 100


def test_spec_dump_excludes_args_model() -> None:
    assert "args_model" not in SPECS["guess"].model_dump()


def test_valid_call_returns_parsed_args() -> None:
    out = validate_call(SPECS, ToolCall(name="guess", args={"n": 5}))
    assert isinstance(out, GuessArgs)
    assert out.n == 5


def test_unknown_tool() -> None:
    out = validate_call(SPECS, ToolCall(name="nope"))
    assert isinstance(out, ToolResult)
    assert not out.ok
    assert out.error is not None
    assert "nope" in out.error
    assert "guess" in out.error


def test_bad_args_message_is_readable() -> None:
    out = validate_call(SPECS, ToolCall(name="guess", args={"n": 101}))
    assert isinstance(out, ToolResult)
    assert not out.ok
    assert out.error == "guess: n: Input should be less than or equal to 100"


def test_missing_arg() -> None:
    out = validate_call(SPECS, ToolCall(name="guess"))
    assert isinstance(out, ToolResult)
    assert out.error == "guess: n: Field required"


def test_make_tool() -> None:
    spec = make_tool("wait", "Wait.", "clock", minutes=(int, Field(ge=1, le=60)))
    specs = tool_map([spec])
    assert spec.kind == "clock"
    assert spec.json_schema()["properties"]["minutes"]["maximum"] == 60
    assert isinstance(validate_call(specs, ToolCall(name="wait", args={"minutes": 5})), BaseModel)
    bad = validate_call(specs, ToolCall(name="wait", args={"minutes": 0}))
    assert isinstance(bad, ToolResult)
    assert not bad.ok
