"""Policies must not import the hidden parts of a task."""

import ast
from pathlib import Path

import nightshift.policies

FORBIDDEN = (
    "nightshift.tasks.incident.world",
    "nightshift.tasks.incident.sim",
    "nightshift.tasks.incident.state",
    "nightshift.tasks.incident.grader",
)


def _imported(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.append(node.module)
            names += [f"{node.module}.{a.name}" for a in node.names]
    return names


def test_policies_do_not_import_hidden_task_modules() -> None:
    root = Path(nightshift.policies.__file__).parent
    files = sorted(root.rglob("*.py"))
    assert files
    for path in files:
        for name in _imported(ast.parse(path.read_text())):
            assert name != "nightshift.tasks", (path, name)
            assert not name.startswith(FORBIDDEN), (path, name)
