"""Harbor-only oracle adapter; changes policy in memory, never exported worlds.

Import this module with Harbor's interpreter, which supplies its own API.
"""

from pathlib import Path
from typing import Any

from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.oracle import OracleAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import TrialPaths


class StopEarlyOracle(OracleAgent):
    capabilities = AgentCapabilities(windows=True, mcp_servers=True)

    def __init__(
        self,
        logs_dir: Path,
        dispatch_export_dir: str,
        model_name: str | None = None,
        **kwargs: Any,
    ) -> None:
        # Harbor supplies these fields specially only for its built-in oracle.
        # Resolve the same preserved task for this custom adapter explicitly.
        task_name = logs_dir.parent.name.split("__", 1)[0]
        task_dir = Path(dispatch_export_dir).resolve() / task_name
        if not task_dir.is_dir():
            raise ValueError(f"Paired export task is missing: {task_dir}")
        super().__init__(
            logs_dir=logs_dir,
            task_dir=task_dir,
            trial_paths=TrialPaths(trial_dir=logs_dir.parent),
            model_name=model_name,
            agent_timeout_sec=900,
            **kwargs,
        )

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        self._task.config.solution.env["CRUCIBLE_POLICY"] = "stop_early"
        await super().run(instruction, environment, context)
