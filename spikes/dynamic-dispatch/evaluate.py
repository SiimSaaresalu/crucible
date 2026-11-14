#!/usr/bin/env python3
"""Paired, observation-only dispatch evaluation with fixed disjoint seed splits.

Run from the repository root with engine/.venv/bin/python. Transcripts stay in
engine/runs (gitignored); compact measurements and source fingerprints are saved
under the requested output directory. No scenario or policy tuning happens here.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

from crucible.core.jobs import Job
from crucible.core.registry import get_policy, get_task
from crucible.core.session import EpisodeSession
from crucible.core.tools import Observation, result_text
from crucible.core.transcript import TranscriptWriter, git_commit

ROOT = Path(__file__).resolve().parents[2]
SPLITS = {
    "development": list(range(20)),
    "calibration": list(range(100, 120)),
    "heldout": list(range(1000, 1050)),
    "salted": list(range(2000, 2010)),
}
POLICIES = (
    "reference",
    "nearest_first",
    "deadline_first",
    "greedy_insertion",
    "stop_early",
    "do_nothing",
)
SALT_NAMESPACE = "dispatch-spike-v1-salted-validation"


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    source = ROOT / "engine/src/crucible/envs/dispatch"
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix in (".py", ".yaml") and "tests" not in path.parts:
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def interval(values: list[float]) -> dict[str, float]:
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    margin = 1.96 * deviation / math.sqrt(len(values))
    return {
        "mean": mean,
        "stddev": deviation,
        "ci95_low": mean - margin,
        "ci95_high": mean + margin,
    }


async def episode(policy_name: str, seed: int, split: str, traces: Path) -> dict[str, Any]:
    task = get_task("dispatch@1")
    policy = get_policy(policy_name, "dispatch@1")
    job = Job(task="dispatch@1", policy=policy_name, seed=seed)
    salt = (
        hashlib.sha256(f"{SALT_NAMESPACE}:{seed}".encode()).hexdigest()
        if split == "salted"
        else None
    )
    transcript = traces / f"{job.job_id}.jsonl"
    session = EpisodeSession(task, seed, TranscriptWriter(transcript), salt=salt)
    error: str | None = None
    try:
        observation = await session.start(policy_name=policy_name, job=job)
        await policy.reset(task.spec, task.tools(), task.instruction())
        for _ in range(session.max_steps):
            if task.done():
                break
            call = await policy.act(observation)
            if call is None:
                break
            result = await session.step(call)
            observation = Observation(text=result_text(result), last_result=result)
    except Exception as exc:  # noqa: BLE001, RUF100 - record environment failures in the result set
        error = f"{type(exc).__name__}: {exc}"
    reason, score = await session.close(error)
    # This is evaluator cleanup, outside the observation-only policy boundary.
    simulation = getattr(task, "_simulation", None)
    if simulation is not None:
        await simulation.close()
    return {
        "split": split,
        "seed": seed,
        "policy": policy_name,
        "reward": score.total,
        "steps": session.steps_used,
        "stop_reason": reason,
        "error": error,
        "transcript": str(transcript.relative_to(ROOT)),
        **score.breakdown,
    }


def summarize(rows: list[dict[str, Any]], policies: list[str]) -> dict[str, Any]:
    metrics = (
        "reward",
        "total_utility",
        "deliveries",
        "on_time_percentage",
        "late_deliveries",
        "driving_distance_km",
        "undelivered_orders",
        "steps",
    )
    result: dict[str, Any] = {"policies": {}, "paired_reference_minus_baseline": {}}
    for policy in policies:
        cases = [r for r in rows if r["policy"] == policy]
        result["policies"][policy] = {
            metric: interval([float(r.get(metric, 0)) for r in cases]) for metric in metrics
        }
    if "reference" in policies:
        reference = {r["seed"]: r for r in rows if r["policy"] == "reference"}
        for policy in policies:
            if policy == "reference":
                continue
            cases = [r for r in rows if r["policy"] == policy]
            deltas = [float(reference[r["seed"]]["reward"]) - float(r["reward"]) for r in cases]
            utilities = [
                float(reference[r["seed"]].get("total_utility", 0))
                - float(r.get("total_utility", 0))
                for r in cases
            ]
            result["paired_reference_minus_baseline"][policy] = {
                "reward_difference": interval(deltas),
                "utility_difference": interval(utilities),
                "wins": sum(d > 1e-9 for d in deltas),
                "ties": sum(abs(d) <= 1e-9 for d in deltas),
                "losses": sum(d < -1e-9 for d in deltas),
            }
    return result


async def run(args: argparse.Namespace) -> int:
    split: str = args.split
    policies: list[str] = args.policies.split(",")
    seeds = SPLITS[split]
    if args.limit is not None:
        seeds = seeds[: args.limit]
    if not seeds or not policies or len(set(policies)) != len(policies):
        raise ValueError("Need nonempty seeds and distinct policies")
    for name in policies:
        get_policy(name, "dispatch@1")
    out: Path = args.out or ROOT / "spikes/dynamic-dispatch/experiments" / split
    traces = ROOT / "engine/runs/dispatch-spike" / split
    if (out / "results.csv").exists() and not args.overwrite:
        raise ValueError(f"{out}/results.csv exists; use --overwrite for an intentional rerun")
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(traces.mkdir, parents=True, exist_ok=True)
    fingerprint = source_fingerprint()
    semaphore = asyncio.Semaphore(args.concurrency)
    completed = 0

    async def one(name: str, seed: int) -> dict[str, Any]:
        nonlocal completed
        async with semaphore:
            row = await episode(name, seed, split, traces)
            completed += 1
            if completed % 10 == 0:
                print(
                    f"{split}: {completed}/{len(seeds) * len(policies)} episodes",
                    flush=True,
                )
            return row

    rows = await asyncio.gather(*(one(name, seed) for seed in seeds for name in policies))
    if fingerprint != source_fingerprint():
        raise RuntimeError(
            "Dispatch implementation changed during measurement; results not accepted"
        )
    fields = sorted({key for row in rows for key in row})
    with (out / "results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    report: dict[str, Any] = {
        "task": "dispatch@1",
        "split": split,
        "seeds": seeds,
        "policies_evaluated": policies,
        "source_sha256": fingerprint,
        "base_git_commit": git_commit(),
        "salt_namespace": SALT_NAMESPACE if split == "salted" else None,
        "confidence_interval": "normal approximation: mean +/- 1.96 * sample_stddev / sqrt(n)",
        "errors": sum(row["error"] is not None for row in rows),
        **summarize(rows, policies),
    }
    (out / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for name in policies:
        stats = report["policies"][name]
        print(
            f"{name:18s} reward={stats['reward']['mean']:.4f} "
            f"utility={stats['total_utility']['mean']:.2f} "
            f"deliveries={stats['deliveries']['mean']:.2f}",
            flush=True,
        )
    return int(report["errors"] > 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=SPLITS, required=True)
    parser.add_argument("--policies", default=",".join(POLICIES))
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int, help="Smoke run only; omit for complete split")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("concurrency must be positive")
    if args.limit is not None and args.limit < 1:
        parser.error("limit must be positive")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
