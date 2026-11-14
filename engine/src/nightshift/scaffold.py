"""`nightshift new-env`: start a new environment as a copy of `guess@1`.

The copy is a working task from the first minute: a reference policy, a test module, and
registration by discovery (`nightshift.tasks` and `nightshift.policies.reference` import every
module they hold). The generic checks in `tests/generic/` pick it up with no edits.
"""

import keyword
import re
from dataclasses import dataclass
from pathlib import Path

from nightshift.core.registry import list_tasks

__all__ = ["ScaffoldError", "engine_root", "new_env"]

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,23}$")  # short enough that copied lines fit 100
_SOURCE = "guess"


class ScaffoldError(Exception):
    """The environment cannot be created (bad name, name taken, no source checkout)."""


@dataclass(frozen=True)
class _Replace:
    old: str
    new: str


def engine_root() -> Path:
    """The engine folder of a source checkout (holds `pyproject.toml`, `src/` and `tests/`)."""
    root = Path(__file__).resolve().parents[2]
    if not (root / "pyproject.toml").is_file() or not (root / "tests").is_dir():
        raise ScaffoldError(
            "new-env needs a source checkout of the engine; pass --root <engine folder>"
        )
    return root


def _class_name(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part) + "Task"


def _title(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split("_") if part)


def _copy(text: str, edits: list[_Replace], source: Path) -> str:
    """Apply each edit exactly once. Fail loudly if the source no longer has the anchor."""
    for edit in edits:
        if text.count(edit.old) != 1:
            raise ScaffoldError(f"{source} changed: cannot find {edit.old!r} exactly once")
        text = text.replace(edit.old, edit.new)
    return text


def _check_name(name: str, root: Path) -> None:
    if not _NAME.match(name) or keyword.iskeyword(name):
        raise ScaffoldError(
            f"bad name {name!r}: use lowercase letters, digits and '_', starting with a letter"
        )
    if name in {ref.split("@")[0] for ref in list_tasks()}:
        raise ScaffoldError(f"a task named {name!r} is already registered")
    taken = [
        root / "src" / "nightshift" / "tasks" / name,
        root / "src" / "nightshift" / "policies" / "reference" / f"{name}.py",
        root / "tests" / name,
    ]
    for path in taken:
        if path.exists():
            raise ScaffoldError(f"{path} already exists")


def _test_module(name: str, cls: str) -> str:
    return f'''"""Task-specific tests for `{name}@1`.

The generic checks in `tests/generic/` run on it too.
"""

import asyncio
from pathlib import Path

from nightshift.core.registry import get_policy, get_task, list_tasks
from nightshift.tasks.{name}.task import {cls}
from tests.core.helpers import play


def test_registered() -> None:
    assert "{name}@1" in list_tasks()
    assert isinstance(get_task("{name}@1"), {cls})


def test_reference_scores_above_zero(tmp_path: Path) -> None:
    policy = get_policy("reference", "{name}@1")
    result = asyncio.run(play(policy, 0, tmp_path / "t.jsonl", "{name}@1"))
    assert result.score.total > 0
'''


def new_env(name: str, root: Path) -> list[Path]:
    """Copy `guess@1` into a new task `name@1` under `root` and return the files written."""
    _check_name(name, root)
    src = root / "src" / "nightshift"
    cls = _class_name(name)
    guess_task = src / "tasks" / _SOURCE / "task.py"
    guess_policy = src / "policies" / "reference" / f"{_SOURCE}.py"
    for path in (guess_task, guess_policy):
        if not path.is_file():
            raise ScaffoldError(f"{path} not found; is {root} the engine folder?")

    task_text = _copy(
        guess_task.read_text(),
        [
            _Replace(
                '"""`guess@1`: find a hidden number in 1..100 with higher/lower feedback."""',
                f'"""`{name}@1`, copied from `guess@1` by `nightshift new-env`.\n\n'
                "Replace the game and keep the `Task` interface.\n"
                '"""',
            ),
            _Replace("class GuessNumber(Task):", f"class {cls}(Task):"),
            _Replace('id="guess",', f'id="{name}",'),
            _Replace('title="Guess the number",', f'title="{_title(name)}",'),
        ],
        guess_task,
    )
    policy_text = _copy(
        guess_policy.read_text(),
        [
            _Replace(
                '"""Binary-search reference for `guess`. '
                'Uses only tool results, never task internals."""',
                f'"""Reference for `{name}` (copied from `guess`).\n\n'
                "Uses only tool results, never task internals.\n"
                '"""',
            ),
            _Replace(
                '@register_policy("reference", "guess")', f'@register_policy("reference", "{name}")'
            ),
        ],
        guess_policy,
    )
    files = {
        src / "tasks" / name / "__init__.py": (
            f'"""`{name}@1`. Importing this package registers the task."""\n\n'
            f"from nightshift.tasks.{name}.task import {cls}\n\n"
            f'__all__ = ["{cls}"]\n'
        ),
        src / "tasks" / name / "task.py": task_text,
        src / "policies" / "reference" / f"{name}.py": policy_text,
        root / "tests" / name / "__init__.py": "",
        root / "tests" / name / "test_task.py": _test_module(name, cls),
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return list(files)
