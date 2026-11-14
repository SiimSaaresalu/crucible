#!/usr/bin/env python3
"""Measure or verify dispatch@2 goldens in the canonical Linux runtime."""

import argparse
import asyncio
import json
from pathlib import Path

from crucible.core.executor import LocalExecutor
from crucible.core.jobs import Job

ROOT = Path(__file__).resolve().parents[2]


async def run(out: Path, check: bool) -> int:
    jobs = [
        Job(task="dispatch@2", policy=policy, seed=seed)
        for policy in ("reference", "stop_early", "do_nothing")
        for seed in range(10)
    ]
    if out.exists():
        raise ValueError(f"Refusing to overwrite {out}")
    results = await LocalExecutor(concurrency=3).run(
        jobs, ROOT / "engine/runs/dispatch-v2/canonical-golden" / out.stem
    )
    scores: dict[str, dict[str, float]] = {}
    for result in results:
        if result.score.flags.get("env_error"):
            raise RuntimeError(f"Failed golden episode: {result.job}")
        scores.setdefault(result.job.policy, {})[str(result.seed)] = result.score.total
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"dispatch@2": scores}, indent=2, sort_keys=True) + "\n")
    if check:
        expected = json.loads(
            (ROOT / "engine/tests/policies/golden_scores.json").read_text()
        )["dispatch@2"]
        for policy, seeds in scores.items():
            for seed, score in seeds.items():
                if abs(score - expected[policy][seed]) > 1e-9:
                    raise AssertionError(f"Golden differs: {policy}, seed {seed}")
    print(json.dumps({"episodes": len(results), "checked": check, "output": str(out)}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args.out, args.check))


if __name__ == "__main__":
    raise SystemExit(main())
