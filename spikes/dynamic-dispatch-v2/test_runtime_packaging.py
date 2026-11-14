"""Fresh pinned export contract and capability preservation."""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from crucible.export.harbor import EngineArtifacts, export_harbor

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "dispatch_v2_export_tested", HERE / "export_v2.py"
)
assert SPEC is not None and SPEC.loader is not None
EXPORTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EXPORTER
SPEC.loader.exec_module(EXPORTER)


@pytest.fixture
def artifacts(tmp_path: Path) -> EngineArtifacts:
    wheel = tmp_path / "crucible-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"synthetic engine wheel; no install is attempted")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("ortools==9.15.6755\n")
    return EngineArtifacts(wheel=wheel, requirements=requirements)


def test_fresh_v2_export_pins_runtime_preserves_world_and_capabilities(
    artifacts: EngineArtifacts, tmp_path: Path
) -> None:
    out = tmp_path / "pinned-v2"
    folders = EXPORTER.export_pinned(artifacts, [6000, 6001], out)
    runtime = json.loads((HERE / "runtime-linux.json").read_text())
    manifest_text = (out / "manifest.json").read_text()
    manifest = json.loads(manifest_text)
    assert manifest["task_ref"] == "dispatch@2"
    assert manifest["seeds"] == [6000, 6001]
    assert manifest["inference_runtime"]["platform"] == "linux/arm64"
    assert manifest["inference_runtime"]["python"] == "3.14.10"
    assert manifest["inference_runtime"]["oracle_python"] == "3.14"
    assert len(folders) == 2
    salts: list[str] = []
    for folder, seed in zip(folders, [6000, 6001], strict=True):
        assert (
            f"FROM {runtime['base_image']}"
            in (folder / "environment/mcp-server/Dockerfile").read_text()
        )
        assert "--python 3.14 --quiet" in (folder / "solution/solve.sh").read_text()
        config = cast(
            dict[str, Any],
            yaml.safe_load((folder / "environment/docker-compose.yaml").read_text()),
        )
        assert all(s["platform"] == "linux/arm64" for s in config["services"].values())
        env = config["services"]["mcp-server"]["environment"]
        assert env["SIDECAR_TASK"] == "dispatch@2"
        assert env["SIDECAR_SEED"] == str(seed)
        assert env["SIDECAR_SALT"] and env["GRADE_TOKEN"]
        assert env["SIDECAR_SALT"] not in manifest_text
        assert env["GRADE_TOKEN"] not in manifest_text
        assert env["GRADE_TOKEN"] in (folder / "task.toml").read_text()
        salts.append(env["SIDECAR_SALT"])
        assert (
            folder / "solution" / artifacts.wheel.name
        ).read_bytes() == artifacts.wheel.read_bytes()
        # The frontier image still contains no engine installation.
        main = (folder / "environment/Dockerfile").read_text()
        assert "crucible" not in main.lower()
    assert len(set(salts)) == 2


def test_existing_export_is_never_modified(
    artifacts: EngineArtifacts, tmp_path: Path
) -> None:
    out = tmp_path / "prior"
    out.mkdir()
    sentinel = out / "manifest.json"
    sentinel.write_text('{"preserved":true}\n')
    with pytest.raises(ValueError, match="existing export"):
        EXPORTER.export_pinned(artifacts, [6000], out)
    assert sentinel.read_text() == '{"preserved":true}\n'
    assert list(out.iterdir()) == [sentinel]


def test_standard_v1_export_remains_unchanged(
    artifacts: EngineArtifacts, tmp_path: Path
) -> None:
    out = tmp_path / "v1"
    folder = export_harbor("dispatch@1", [6000], out, artifacts)[0]
    assert (
        "FROM python:3.14-slim-bookworm"
        in (folder / "environment/mcp-server/Dockerfile").read_text()
    )
    assert "--python 3.14 --quiet" in (folder / "solution/solve.sh").read_text()
    config = cast(
        dict[str, Any],
        yaml.safe_load((folder / "environment/docker-compose.yaml").read_text()),
    )
    assert all("platform" not in service for service in config["services"].values())
    assert "inference_runtime" not in json.loads((out / "manifest.json").read_text())
