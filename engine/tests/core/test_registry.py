from collections.abc import Generator

import pytest

from nightshift.core import registry
from nightshift.core.policy import Policy
from nightshift.core.registry import (
    UnknownPolicyError,
    UnknownTaskError,
    get_policy,
    get_task,
    list_tasks,
    register_policy,
    register_task,
)
from nightshift.core.scoring import Score
from nightshift.core.task import Task, TaskSpec
from nightshift.core.tools import Observation, ToolCall, ToolResult, ToolSpec


class _Base(Task):
    async def reset(self, seed: int, difficulty: int = 0) -> Observation:
        return Observation()

    def tools(self) -> list[ToolSpec]:
        return []

    async def step(self, call: ToolCall) -> ToolResult:
        return ToolResult(ok=True)

    def done(self) -> bool:
        return True

    async def grade(self) -> Score:
        return Score(total=0.0)

    def instruction(self) -> str:
        return ""


def _make_task(task_id: str, version: int) -> type[Task]:
    class T(_Base):
        spec = TaskSpec(id=task_id, version=version, max_steps=1)

    return T


class _BasePolicy:
    name = "p"

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None:
        return None

    async def act(self, obs: Observation) -> ToolCall | None:
        return None


class _Generic(_BasePolicy):
    pass


class _Specific(_BasePolicy):
    pass


@pytest.fixture(autouse=True)
def isolated() -> Generator[None]:
    with registry._reset_for_tests():  # pyright: ignore[reportPrivateUsage]
        yield


def test_get_task_by_ref_returns_fresh_instances() -> None:
    cls = register_task(_make_task("dummy", 1))
    a, b = get_task("dummy@1"), get_task("dummy@1")
    assert isinstance(a, cls)
    assert a is not b


def test_bare_id_returns_newest_version() -> None:
    register_task(_make_task("dummy", 1))
    register_task(_make_task("dummy", 10))
    v2 = register_task(_make_task("dummy", 2))
    register_task(_make_task("other", 99))
    assert get_task("dummy").spec.version == 10
    assert isinstance(get_task("dummy@2"), v2)


def test_register_returns_class() -> None:
    cls = _make_task("dummy", 1)
    assert register_task(cls) is cls


def test_duplicate_ref_raises() -> None:
    register_task(_make_task("dummy", 1))
    with pytest.raises(ValueError, match=r"dummy@1.*already registered"):
        register_task(_make_task("dummy", 1))


def test_unknown_task_lists_known_refs() -> None:
    register_task(_make_task("dummy", 1))
    register_task(_make_task("dummy", 2))
    with pytest.raises(UnknownTaskError, match=r"nope.*dummy@1, dummy@2"):
        get_task("nope")
    with pytest.raises(KeyError, match=r"dummy@3"):
        get_task("dummy@3")


@pytest.mark.parametrize("ref", ["x@", "x@abc", "@1", "", "x@-1", "x@1.5"])
def test_malformed_refs(ref: str) -> None:
    with pytest.raises(ValueError, match="Malformed task ref"):
        get_task(ref)


def test_list_tasks_sorted_numerically() -> None:
    register_task(_make_task("b", 1))
    register_task(_make_task("a", 10))
    register_task(_make_task("a", 2))
    assert list_tasks() == ["a@2", "a@10", "b@1"]


def test_policy_specific_beats_generic() -> None:
    register_policy("p")(_Generic)
    register_policy("p", "dummy")(_Specific)
    assert isinstance(get_policy("p", "dummy@1"), _Specific)
    assert isinstance(get_policy("p", "dummy"), _Specific)


def test_policy_falls_back_to_generic() -> None:
    register_policy("p")(_Generic)
    register_policy("p", "dummy")(_Specific)
    policy: Policy = get_policy("p", "other@1")
    assert isinstance(policy, _Generic)


def test_policy_factory_function() -> None:
    @register_policy("fn")
    def make() -> Policy:
        return _Generic()

    assert isinstance(get_policy("fn", "any@1"), _Generic)


def test_duplicate_policy_raises() -> None:
    register_policy("p")(_Generic)
    with pytest.raises(ValueError, match="already registered"):
        register_policy("p")(_Specific)


def test_unknown_policy_lists_options() -> None:
    register_policy("generic")(_Generic)
    register_policy("mine", "dummy")(_Specific)
    register_policy("theirs", "other")(_Specific)
    with pytest.raises(UnknownPolicyError) as info:
        get_policy("nope", "dummy@1")
    msg = str(info.value)
    assert "generic (any task)" in msg
    assert "mine (task: dummy)" in msg
    assert "theirs" not in msg


def test_builtins_loaded_once(monkeypatch: pytest.MonkeyPatch) -> None:
    imported: list[str] = []
    monkeypatch.setattr(registry.importlib, "import_module", imported.append)
    monkeypatch.setattr(registry, "_loaded", False)
    list_tasks()
    list_tasks()
    assert imported == ["nightshift.tasks", "nightshift.policies"]


def test_state_restored_after_reset_context() -> None:
    register_task(_make_task("outer", 1))
    with registry._reset_for_tests():  # pyright: ignore[reportPrivateUsage]
        assert list_tasks() == []
    assert list_tasks() == ["outer@1"]
