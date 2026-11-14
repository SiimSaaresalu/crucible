#!/usr/bin/env python3
"""Evaluate paired dispatch policies through normal public tools.

Run with engine/.venv/bin/python. Completed outputs are immutable by default.
Raw transcripts and private salted manifests live only in gitignored engine/runs.
No held-out or salted results should be used to tune a candidate.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import platform
import re
import secrets
import shutil
import statistics
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from crucible.core.jobs import Job
from crucible.core.registry import get_policy, get_task
from crucible.core.session import EpisodeSession
from crucible.core.tools import Observation, result_text
from crucible.core.transcript import TranscriptWriter, git_commit

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SPLITS = {
    "development": list(range(3000, 3020)),
    "calibration": list(range(3100, 3120)),
    "heldout": list(range(4000, 4050)),
    "salted": list(range(5000, 5020)),
}
SPLIT_ALIASES = {"dev": "development", "cal": "calibration"}
POLICIES = (
    "rolling_horizon_solver",
    "nearest_first",
    "deadline_first",
    "greedy_insertion",
    "stop_early",
    "do_nothing",
)
SALT_NAMESPACE = "dispatch-spike-v2-salted-validation-v1"
METRICS = (
    "reward",
    "total_utility",
    "realized_delivery_value",
    "driving_cost",
    "driving_distance_km",
    "deliveries",
    "on_time_percentage",
    "on_time_generated_percentage",
    "late_deliveries",
    "undelivered_orders",
    "unserved_face_value",
    "runtime_seconds",
    "policy_runtime_seconds",
    "solver_runtime_seconds",
    "solver_fallback_rounds",
    "invalid_actions",
    "tool_response_characters",
    "steps",
)


def source_manifest() -> dict[str, str]:
    """Hash execution source, dependency pins, and the evaluator itself."""
    paths = [
        p
        for p in (ROOT / "engine/src/crucible").rglob("*")
        if p.is_file()
        and p.suffix in (".py", ".yaml", ".toml", ".sh")
        and "tests" not in p.relative_to(ROOT / "engine/src/crucible").parts
    ]
    paths += [ROOT / "engine/pyproject.toml", ROOT / "engine/uv.lock", Path(__file__)]
    return {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)
    }


def fingerprint(manifest: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def interval(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "mean": None, "stddev": None, "ci95_low": None, "ci95_high": None}
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    margin = 1.96 * deviation / math.sqrt(len(values))
    return {
        "n": len(values),
        "mean": mean,
        "stddev": deviation,
        "ci95_low": mean - margin,
        "ci95_high": mean + margin,
    }


def _decision_traces(policy: Any) -> list[dict[str, Any]]:
    delegate = getattr(policy, "_version_policy", None)
    return list(getattr(delegate or policy, "decision_traces", []))


def _trace_number(traces: list[dict[str, Any]], names: tuple[str, ...]) -> float:
    return sum(
        float(next((trace[name] for name in names if isinstance(trace.get(name), (int, float))), 0))
        for trace in traces
    )


async def episode(
    policy_name: str, seed: int, args: argparse.Namespace, traces: Path
) -> dict[str, Any]:
    if args.task == "dispatch@2":
        from crucible.envs.dispatch.v2.task import DispatchTask

        task = DispatchTask(regime=args.regime)
    else:
        task = get_task(args.task)
    policy = get_policy(policy_name, args.task)
    job = Job(task=args.task, policy=policy_name, seed=seed)
    salt = args.world_salts.get(str(seed))
    transcript = traces / f"{job.job_id}.jsonl"
    session = EpisodeSession(task, seed, TranscriptWriter(transcript), salt=salt)
    error: str | None = None
    invalid, characters, policy_runtime = 0, 0, 0.0
    started = time.perf_counter()
    traces_data: list[dict[str, Any]] = []
    final_orders: list[dict[str, Any]] = []
    try:
        observation = await session.start(policy_name=policy_name, job=job)
        await policy.reset(task.spec, task.tools(), task.instruction())
        for _ in range(session.max_steps):
            if task.done():
                break
            action_start = time.perf_counter()
            call = await policy.act(observation)
            policy_runtime += time.perf_counter() - action_start
            if call is None:
                break
            result = await session.step(call)
            invalid += int(not result.ok)
            characters += len(result.model_dump_json())
            observation = Observation(text=result_text(result), last_result=result)
    except Exception as exc:  # noqa: BLE001, RUF100 - retain failed-world evidence
        error = f"{type(exc).__name__}: {exc}"
    try:
        reason, score = await session.close(error)
        traces_data = _decision_traces(policy)
        # Evaluator-only finalization retrieves physical final outcomes for metrics;
        # this observation is never sent back to the policy. Grade already replayed
        # the same commitments for stop-early/call-exhaustion behavior.
        simulation = getattr(task, "_simulation", None)
        if error is None and simulation is not None:
            final = await simulation.finish()
            final_orders = final["orders"]
    except Exception as exc:  # noqa: BLE001, RUF100 - retain finalization errors
        error = error or f"finalization {type(exc).__name__}: {exc}"
        reason, score = "error", None
    finally:
        simulation = getattr(task, "_simulation", None)
        if simulation is not None:
            await simulation.close()
    if traces_data:
        (traces / f"{job.job_id}.solver.json").write_text(
            json.dumps(traces_data, indent=2, sort_keys=True) + "\n"
        )
    breakdown = score.breakdown if score is not None else {}
    generated = float(breakdown.get("generated_orders", 0))
    delivered_face_value = sum(o["value"] for o in final_orders if o["status"] == "delivered")
    row = {
        "task": args.task,
        "candidate": args.candidate,
        "regime": args.regime,
        "split": args.split,
        "seed": seed,
        "policy": policy_name,
        "reward": score.total if score is not None else 0.0,
        "steps": session.steps_used,
        "stop_reason": reason,
        "error": error,
        "invalid_actions": invalid,
        "tool_response_characters": characters,
        "runtime_seconds": time.perf_counter() - started,
        "policy_runtime_seconds": policy_runtime,
        "solver_runtime_seconds": (
            sum(
                getattr(
                    getattr(policy, "_version_policy", None) or policy, "solve_runtime_seconds", []
                )
            )
            if traces_data
            else None
        ),
        "solver_fallback_rounds": sum(
            bool(t.get("fallback", t.get("fallback_used", False))) for t in traces_data
        )
        if traces_data
        else None,
        "solver_rounds": len(traces_data),
        "solver_deterministic_time": _trace_number(
            traces_data, ("deterministic_time", "deterministic_work", "solver_deterministic_time")
        )
        if traces_data
        else None,
        "on_time_generated_percentage": (
            100 * float(breakdown.get("on_time_deliveries", 0)) / generated if generated else None
        ),
        "unserved_face_value": (
            float(breakdown.get("total_potential_value", 0)) - delivered_face_value
            if error is None
            else None
        ),
        "transcript": str(transcript.relative_to(ROOT)),
        "solver_trace": str((traces / f"{job.job_id}.solver.json").relative_to(ROOT))
        if traces_data
        else None,
        **breakdown,
    }
    return row


def summarize(rows: list[dict[str, Any]], policies: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"policies": {}, "paired_reference_minus_baseline": {}}
    for policy in policies:
        cases = [r for r in rows if r["policy"] == policy]
        result["policies"][policy] = {
            "episodes": len(cases),
            "errors": sum(r["error"] is not None for r in cases),
            "invalid_actions_total": sum(r["invalid_actions"] for r in cases),
            **{
                metric: interval([float(r[metric]) for r in cases if r.get(metric) is not None])
                for metric in METRICS
            },
        }
    reference_name = next(
        (p for p in ("rolling_horizon_solver", "reference") if p in policies), None
    )
    result["reference_policy"] = reference_name
    if reference_name is not None:
        reference = {r["seed"]: r for r in rows if r["policy"] == reference_name}
        for policy in policies:
            if policy == reference_name:
                continue
            cases = [r for r in rows if r["policy"] == policy]
            deltas = [reference[r["seed"]]["reward"] - r["reward"] for r in cases]
            raw = [
                reference[r["seed"]]["total_utility"] - r["total_utility"]
                for r in cases
                if r.get("total_utility") is not None
                and reference[r["seed"]].get("total_utility") is not None
            ]
            result["paired_reference_minus_baseline"][policy] = {
                "worlds": len(cases),
                "worlds_with_errors": sum(
                    r["error"] is not None or reference[r["seed"]]["error"] is not None
                    for r in cases
                ),
                "reward_difference": interval(deltas),
                "utility_difference": interval(raw),
                "wins": sum(d > 1e-9 for d in deltas),
                "ties": sum(abs(d) <= 1e-9 for d in deltas),
                "losses": sum(d < -1e-9 for d in deltas),
            }
    return result


def _prepare_out(out: Path, args: argparse.Namespace) -> None:
    if not out.exists() or not any(out.iterdir()):
        out.mkdir(parents=True, exist_ok=True)
        return
    existing = out / "summary.json"
    prior: dict[str, Any] = json.loads(existing.read_text()) if existing.is_file() else {}
    if not args.overwrite or prior.get("candidate") == args.candidate:
        raise ValueError(
            f"{out} already has artifacts. Choose a new candidate/output path; replacing an "
            "existing output requires --overwrite and a different --candidate."
        )
    archived = out.with_name(
        out.name + ".archive-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    shutil.move(str(out), str(archived))
    out.mkdir(parents=True)


async def run(args: argparse.Namespace) -> int:
    original_split: str = str(args.split)
    split = SPLIT_ALIASES.get(original_split, original_split)
    args.split = split
    policies = [p.strip() for p in args.policies.split(",")]
    seeds = SPLITS[split][: args.limit] if args.limit is not None else SPLITS[split]
    if not policies or any(not p for p in policies) or len(set(policies)) != len(policies):
        raise ValueError("Need distinct nonempty policy names")
    for name in policies:
        get_policy(name, args.task)
    out = (args.out or HERE / "experiments" / args.candidate / args.regime / args.split).resolve()
    _prepare_out(out, args)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    traces = ROOT / "engine/runs/dispatch-v2" / args.candidate / args.regime / args.split / stamp
    traces.mkdir(parents=True)
    sources = source_manifest()
    source_hash = fingerprint(sources)
    world_salts: dict[str, str] = {}
    salt_manifest_hash: str | None = None
    if split == "salted":
        salt_path = args.salt_manifest or (
            ROOT / "engine/runs/dispatch-v2" / args.candidate / f"private-salts-{args.regime}.json"
        )
        if not salt_path.exists():
            if args.salt_manifest is not None:
                raise ValueError("Explicit --salt-manifest does not exist")
            salt_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "namespace": SALT_NAMESPACE,
                "task": args.task,
                "regime": args.regime,
                "salts": {str(seed): secrets.token_hex(24) for seed in SPLITS[split]},
            }
            salt_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            salt_path.chmod(0o600)
        private_payload: dict[str, Any] = json.loads(salt_path.read_text())
        if private_payload.get("task") != args.task or private_payload.get("regime") != args.regime:
            raise ValueError("Salt manifest task/regime mismatch")
        raw_salts: dict[str, str] = private_payload["salts"]
        world_salts = {str(seed): raw_salts[str(seed)] for seed in seeds}
        if any(not salt for salt in world_salts.values()):
            raise ValueError("Salt manifest contains an empty salt")
        salt_manifest_hash = hashlib.sha256(salt_path.read_bytes()).hexdigest()
    elif args.salt_manifest is not None:
        raise ValueError("--salt-manifest is only valid for the salted split")
    args.world_salts = world_salts
    private_path = traces / "private-worlds.json"
    private_path.write_text(
        json.dumps(
            {
                "task": args.task,
                "regime": args.regime,
                "salts": {str(seed): world_salts.get(str(seed)) for seed in seeds},
            },
            indent=2,
        )
        + "\n"
    )
    private_path.chmod(0o600)
    semaphore = asyncio.Semaphore(args.concurrency)
    completed = 0

    async def one(name: str, seed: int) -> dict[str, Any]:
        nonlocal completed
        async with semaphore:
            row = await episode(name, seed, args, traces)
            completed += 1
            if completed % 10 == 0 or row["error"]:
                print(
                    f"{args.regime}/{args.split}: {completed}/{len(seeds) * len(policies)} "
                    f"error={row['error']}",
                    flush=True,
                )
            return row

    rows = await asyncio.gather(*(one(name, seed) for seed in seeds for name in policies))
    changed = sources != source_manifest()
    fields = sorted({key for row in rows for key in row})
    with (out / "results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    errors = sum(row["error"] is not None for row in rows)
    report: dict[str, Any] = {
        "task": args.task,
        "candidate": args.candidate,
        "regime": args.regime,
        "split": args.split,
        "seeds": seeds,
        "full_split": len(seeds) == len(SPLITS[split]),
        "policies_evaluated": policies,
        "source_sha256": source_hash,
        "source_files": sources,
        "source_changed_during_run": changed,
        "accepted": not changed and errors == 0 and not any(r["invalid_actions"] for r in rows),
        "base_git_commit": git_commit(),
        "python": platform.python_version(),
        "ortools": version("ortools"),
        "salt_namespace": SALT_NAMESPACE if args.split == "salted" else None,
        "private_artifacts": str(traces.relative_to(ROOT)),
        "salt_manifest_sha256": salt_manifest_hash,
        "confidence_interval": "normal approximation: mean +/- 1.96 * sample_stddev / sqrt(n)",
        "error_handling": "All worlds retained. Failed rewards are zero; missing economic "
        "metrics have explicit n. Any episode error invalidates acceptance.",
        "runtime_note": "Wall time is observational only; it does not control solver search.",
        "errors": errors,
        **summarize(rows, policies),
    }
    (out / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for name in policies:
        stats = report["policies"][name]
        print(
            f"{name:24s} reward={stats['reward']['mean']:.4f} "
            f"utility={stats['total_utility']['mean']} errors={stats['errors']}",
            flush=True,
        )
    if changed:
        print(
            "REJECTED: source changed during measurement; preserve this diagnostic and rerun.",
            flush=True,
        )
    return int(changed or errors > 0 or any(r["invalid_actions"] for r in rows))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=("dispatch@1", "dispatch@2"), default="dispatch@2")
    parser.add_argument("--regime", choices=tuple("ABCD"), default="A")
    parser.add_argument("--split", choices=(*SPLITS, *SPLIT_ALIASES), required=True)
    parser.add_argument("--candidate", default="development-v2")
    parser.add_argument("--policies", default=",".join(POLICIES))
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--limit", type=int, help="Diagnostic prefix only; omit for a full split")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--salt-manifest",
        type=Path,
        help="Private existing task/regime salt manifest for exact salted replay",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Archive old output; requires new candidate ID"
    )
    args = parser.parse_args()
    if args.concurrency < 1 or (args.limit is not None and args.limit < 1):
        parser.error("concurrency and limit must be positive")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", args.candidate):
        parser.error("candidate must be a simple nonempty name")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
