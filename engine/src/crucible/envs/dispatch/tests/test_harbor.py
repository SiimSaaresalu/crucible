"""Harbor layout and real MCP sidecar checks for dispatch."""

import json
import tomllib
from pathlib import Path

import httpx
import yaml

from crucible.agents.remote import McpEpisode, drive, fetch_grade
from crucible.core.executor import LocalExecutor
from crucible.core.jobs import Job
from crucible.core.registry import get_policy, get_task
from crucible.envs.dispatch.tests.live import TOKEN, live_sidecar
from crucible.export.harbor import EngineArtifacts, export_harbor


def test_dispatch_export_keeps_backend_and_secrets_out_of_agent(tmp_path: Path) -> None:
    wheel = tmp_path / "crucible-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"test artifact")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("fastapi==0.162.7\n")
    folders = export_harbor(
        "dispatch@1",
        [1000, 1001],
        tmp_path / "export",
        EngineArtifacts(wheel=wheel, requirements=requirements),
        token_factory=lambda: "private-grading-capability",
        salt_factory=lambda: "private-world-salt",
    )
    for folder in folders:
        config = tomllib.loads((folder / "task.toml").read_text())
        assert config["verifier"]["env"]["GRADE_TOKEN"] == "private-grading-capability"
        compose = yaml.safe_load((folder / "environment/docker-compose.yaml").read_text())
        services = compose["services"]
        assert set(services) == {"main", "mcp-server"}
        assert services["mcp-server"]["environment"]["SIDECAR_TASK"] == "dispatch@1"
        assert services["mcp-server"]["environment"]["SIDECAR_SALT"] == "private-world-salt"
        assert services["mcp-server"]["expose"] == ["8000"]
        assert "ports" not in services["mcp-server"]
        for rel in ("instruction.md", "environment/Dockerfile", "solution/solve.sh"):
            contents = (folder / rel).read_text()
            assert "private-grading-capability" not in contents
            assert "private-world-salt" not in contents
        assert (folder / "environment/mcp-server" / wheel.name).is_file()
        assert not (folder / "environment" / wheel.name).exists()


async def test_dispatch_reference_runs_through_real_mcp_and_grade_stays_private(
    tmp_path: Path,
) -> None:
    task = get_task("dispatch@1")
    local = (
        await LocalExecutor().run(
            [Job(task="dispatch@1", policy="reference", seed=0)], tmp_path / "local"
        )
    )[0]
    assert not local.score.flags.get("env_error", False)
    async with live_sidecar(tmp_path / "sidecar", 0, task="dispatch@1") as url:
        async with httpx.AsyncClient() as http:
            assert (await http.get(f"{url}/grade")).status_code == 403
            for path in ("/state", "/history", "/events", "/reset", "/orders", "/plans"):
                assert (await http.get(f"{url}{path}")).status_code == 404
        episode, instruction = await McpEpisode.connect(
            f"{url}/mcp", grade=lambda: fetch_grade(url, TOKEN)
        )
        outcome = await drive(
            get_policy("reference", "dispatch@1"), episode, task.spec, task.tools(), instruction
        )
        async with httpx.AsyncClient() as http:
            body = (await http.get(f"{url}/grade", headers={"x-grade-token": TOKEN})).json()
    assert outcome.score == local.score
    assert outcome.steps == local.steps <= 40
    assert body["reward"] == local.score.total
    assert "simulator_token" not in json.dumps(body["transcript"])
