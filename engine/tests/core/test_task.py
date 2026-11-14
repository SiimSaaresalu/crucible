import pytest

from nightshift.core.policy import Policy
from nightshift.core.registry import get_task, list_tasks
from nightshift.core.scoring import Score
from nightshift.core.task import Task, TaskSpec
from nightshift.core.tools import Observation, ToolCall, ToolResult, ToolSpec


class MinimalTask(Task):
    spec = TaskSpec(id="min", version=3, max_steps=5, title="t", description="d")

    async def reset(self, seed: int, difficulty: int = 0) -> Observation:
        return Observation(text=f"{seed}/{difficulty}")

    def tools(self) -> list[ToolSpec]:
        return []

    async def step(self, call: ToolCall) -> ToolResult:
        return ToolResult(ok=True)

    def done(self) -> bool:
        return True

    async def grade(self) -> Score:
        return Score(total=1.0)

    def instruction(self) -> str:
        return "do it"


class StopPolicy:
    name = "stop"

    async def reset(self, task_spec: TaskSpec, tools: list[ToolSpec], instruction: str) -> None:
        self.ref = task_spec.ref

    async def act(self, obs: Observation) -> ToolCall | None:
        return None


def test_ref() -> None:
    assert (
        TaskSpec(id="guess", version=2, max_steps=10, title="t", description="d").ref == "guess@2"
    )


def test_spec_is_frozen() -> None:
    spec = TaskSpec(id="a", version=1, max_steps=1, title="t", description="d")
    with pytest.raises(ValueError):
        spec.version = 2


def test_task_is_abstract() -> None:
    with pytest.raises(TypeError):
        Task()  # type: ignore[abstract]  # pyright: ignore[reportAbstractUsage]


def test_scoring_hash_default() -> None:
    assert MinimalTask.scoring_hash == "none"


async def test_minimal_task_and_policy() -> None:
    task = MinimalTask()
    obs = await task.reset(7, difficulty=2)
    assert obs.text == "7/2"
    policy: Policy = StopPolicy()
    await policy.reset(task.spec, task.tools(), task.instruction())
    assert await policy.act(obs) is None


@pytest.mark.parametrize(
    "bad",
    [{"title": ""}, {"title": "two\nlines"}, {"description": ""}, {"keywords": ("ok", " ")}],
)
def test_spec_metadata_is_validated(bad: dict[str, object]) -> None:
    fields: dict[str, object] = {"id": "a", "version": 1, "max_steps": 1, "title": "t"}
    fields["description"] = "d"
    with pytest.raises(ValueError):
        TaskSpec.model_validate({**fields, **bad})


def test_every_registered_task_has_metadata() -> None:
    for ref in list_tasks():
        spec = get_task(ref).spec
        assert spec.title.strip() and spec.description.strip(), ref
