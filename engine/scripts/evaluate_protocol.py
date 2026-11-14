"""One frozen protocol comparison on the complete dev and held-out seed banks.

Run from engine with `uv run python scripts/evaluate_protocol.py`.
Costs include purchase, holding, stockout penalties, expedite and switching.
Profit additionally includes sales revenue and terminal stock credit.
"""

import asyncio
import csv
import json
from pathlib import Path
from statistics import fmean

from crucible.core.executor import LocalExecutor
from crucible.core.jobs import Job
from crucible.envs.supplygym.play import SPLITS

COST_KEYS = ("purchase", "holding", "stockout", "expedite", "switching")


async def main() -> None:
    out = Path("runs/protocol")
    for split in ("dev", "heldout"):
        lo, hi = SPLITS[split]
        jobs = [
            Job(task="supplygym@1", policy=p, seed=s)
            for s in range(lo, hi)
            for p in ("protocol", "ss")
        ]
        results = await LocalExecutor().run(jobs, out / split)
        rows = []
        for r in results:
            assert r.stop_reason == "task_done", (r.job, r.stop_reason)
            assert r.steps <= 120 and not r.score.flags.get("budget_exhausted", False)
            b = r.score.breakdown
            rows.append(
                {
                    "task": r.task,
                    "agent": r.policy,
                    "seed": r.seed,
                    "repeat": r.job.repeat,
                    "profit": b["profit"],
                    "cost": sum(b[k] for k in COST_KEYS),
                    "steps": r.steps,
                    "reward": r.score.total,
                    **{k: b[k] for k in COST_KEYS},
                }
            )
        with (out / f"{split}.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        for metric in ("profit", "cost"):
            with (out / f"{split}-{metric}.csv").open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["task", "agent", "seed", "repeat", "score"])
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {
                            **{k: row[k] for k in ("task", "agent", "seed", "repeat")},
                            "score": row[metric] * (-1 if metric == "cost" else 1),
                        }
                    )
        paired = {p: [r for r in rows if r["agent"] == p] for p in ("protocol", "ss")}
        differences = [
            a["profit"] - b["profit"] for a, b in zip(paired["protocol"], paired["ss"], strict=True)
        ]
        print(
            json.dumps(
                {
                    "split": split,
                    "seeds": hi - lo,
                    "mean_cost": {p: fmean(r["cost"] for r in rs) for p, rs in paired.items()},
                    "mean_profit": {p: fmean(r["profit"] for r in rs) for p, rs in paired.items()},
                    "profit_difference": fmean(differences),
                    "profit_wins": sum(d > 0 for d in differences),
                },
                indent=2,
            ),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
