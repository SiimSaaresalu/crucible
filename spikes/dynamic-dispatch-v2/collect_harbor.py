#!/usr/bin/env python3
"""Collect public Harbor metrics; pair only trials from the same preserved export."""

import argparse
import csv
import hashlib
import json
import random
import re
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text()) if path.is_file() else default


def collect(jobs_root: Path, prefix: str, export: Path) -> list[dict[str, Any]]:
    manifest = load_json(export / "manifest.json", {})
    if not manifest:
        raise ValueError("Export manifest is missing")
    folders = {name: export / name for name in manifest["tasks"]}
    rows: list[dict[str, Any]] = []
    for job in sorted(jobs_root.glob(f"{prefix}-*")):
        suffix = job.name[len(prefix) + 1 :]
        if suffix == "oracle":
            agent = "oracle"
        elif suffix == "nop":
            agent = "nop"
        elif suffix in ("stop-early", "stop_early", "stopearly"):
            agent = "stop_early"
        elif suffix.startswith("opencode"):
            agent = "opencode"
        else:
            continue
        for trial in sorted(p for p in job.iterdir() if p.is_dir()):
            task_name = trial.name.split("__", 1)[0]
            if task_name not in folders:
                continue
            result: dict[str, Any] = load_json(trial / "result.json", {})
            grade: dict[str, Any] = load_json(trial / "verifier/grade.json", {})
            records: list[dict[str, Any]] = load_json(
                trial / "verifier/transcript.json", []
            )
            steps = [r for r in records if r.get("type") == "step"]
            task_id: dict[str, Any] = result.get("task_id") or {}
            task_path = task_id.get("path")
            path_matches = (
                bool(task_path)
                and (ROOT / task_path).resolve() == folders[task_name].resolve()
            )
            match = re.search(r"-seed-(\d+)$", task_name)
            if match is None:
                raise ValueError(f"Unrecognized task folder: {task_name}")
            errors: list[str] = []
            exit_path = trial / "agent/exit-code.txt"
            exit_code = exit_path.read_text().strip() if exit_path.is_file() else None
            if exit_code is not None and exit_code != "0":
                errors.append(f"agent exited with code {exit_code}")
            if not result:
                errors.append("missing trial result")
            if not grade:
                errors.append("missing verifier grade")
            if result.get("exception_info") is not None:
                errors.append("Harbor trial exception")
            if not path_matches:
                errors.append("trial export path does not match preserved export")
            if grade.get("stop_reason") == "error" or grade.get("flags", {}).get(
                "env_error"
            ):
                errors.append("environment error")
            usage: dict[str, Any] = result.get("agent_result") or {}
            compose = folders[task_name] / "environment/docker-compose.yaml"
            # Hash private identity; never publish capabilities or salts themselves.
            world_hash = digest(compose)
            rows.append(
                {
                    "agent": agent,
                    "job": job.name,
                    "trial": trial.name,
                    "seed": int(match[1]),
                    "task": manifest["task_ref"],
                    "world_sha256": world_hash,
                    "wheel_sha256": manifest["wheel_sha256"],
                    "reward": grade.get("reward"),
                    "steps": grade.get("steps"),
                    "stop_reason": grade.get("stop_reason"),
                    "agent_exit_code": exit_code,
                    "error": "; ".join(errors) or None,
                    "invalid_actions": sum(not s["result"]["ok"] for s in steps),
                    "tool_response_characters": sum(
                        len(json.dumps(s["result"], separators=(",", ":")))
                        for s in steps
                    ),
                    "plan_calls": sum(
                        s["call"]["name"] == "submit_dispatch_plan" for s in steps
                    ),
                    "map_reads": sum(
                        s["call"]["name"] == "get_city_map" for s in steps
                    ),
                    "state_reads": sum(
                        s["call"]["name"] == "get_dispatch_state" for s in steps
                    ),
                    "input_tokens": usage.get("n_input_tokens"),
                    "output_tokens": usage.get("n_output_tokens"),
                    "cost_usd": usage.get("cost_usd"),
                    **grade.get("breakdown", {}),
                }
            )
    return rows


def bootstrap(values: list[float], samples: int = 10000) -> dict[str, Any]:
    if not values:
        return {"n_worlds": 0, "mean": None, "ci95_low": None, "ci95_high": None}
    rng = random.Random(0)
    means = sorted(
        statistics.fmean(rng.choices(values, k=len(values))) for _ in range(samples)
    )
    return {
        "n_worlds": len(values),
        "mean": statistics.fmean(values),
        "ci95_low": means[int(0.025 * samples)],
        "ci95_high": means[min(samples - 1, int(0.975 * samples))],
    }


def summarize(
    rows: list[dict[str, Any]], expected_worlds: int, expected_repeats: int = 3
) -> dict[str, Any]:
    agents: dict[str, Any] = {}
    for agent in sorted({r["agent"] for r in rows}):
        cases = [r for r in rows if r["agent"] == agent]
        valid = [r for r in cases if r["error"] is None and r["reward"] is not None]
        agents[agent] = {
            "trials": len(cases),
            "completed_valid_trials": len(valid),
            "errors": len(cases) - len(valid),
            "invalid_actions": sum(r["invalid_actions"] for r in cases),
            "observed_mean_reward": statistics.fmean(r["reward"] for r in valid)
            if valid
            else None,
            "observed_mean_utility": statistics.fmean(r["total_utility"] for r in valid)
            if valid
            else None,
        }
    world_keys = sorted({r["world_sha256"] for r in rows})
    paired: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for key in world_keys:
        cases = [r for r in rows if r["world_sha256"] == key]
        oracle = [r for r in cases if r["agent"] == "oracle" and not r["error"]]
        models = [r for r in cases if r["agent"] == "opencode" and not r["error"]]
        if len(oracle) != 1 or not models:
            missing.append(
                {
                    "seed": cases[0]["seed"],
                    "valid_oracles": len(oracle),
                    "valid_model_trials": len(models),
                }
            )
            continue
        rewards = [float(r["reward"]) for r in models]
        utilities = [float(r["total_utility"]) for r in models]
        paired.append(
            {
                "seed": cases[0]["seed"],
                "world_sha256": key,
                "model_trials": len(models),
                "model_mean_reward": statistics.fmean(rewards),
                "model_stddev_reward": statistics.stdev(rewards)
                if len(rewards) > 1
                else 0.0,
                "reference_reward": oracle[0]["reward"],
                "reward_difference": oracle[0]["reward"] - statistics.fmean(rewards),
                "utility_difference": oracle[0]["total_utility"]
                - statistics.fmean(utilities),
            }
        )
    deltas = [p["reward_difference"] for p in paired]
    return {
        "agents": agents,
        "expected_export_worlds": expected_worlds,
        "observed_export_worlds": len(world_keys),
        "paired_worlds": paired,
        "unpaired_worlds": missing,
        "paired_reference_minus_model": {
            "reward": bootstrap(deltas),
            "raw_utility": bootstrap([p["utility_difference"] for p in paired]),
            "wins": sum(d > 1e-9 for d in deltas),
            "ties": sum(abs(d) <= 1e-9 for d in deltas),
            "losses": sum(d < -1e-9 for d in deltas),
        },
        "accepted_complete_comparison": bool(paired)
        and len(paired) == expected_worlds
        and all(p["model_trials"] >= expected_repeats for p in paired)
        and all(not r["error"] for r in rows),
        "expected_model_repeats_per_world": expected_repeats,
        "uncertainty_method": "Average repeats within world, then 10000 percentile-bootstrap "
        "resamples of paired worlds; fixed random seed 0. Few-world intervals are descriptive.",
        "missing_data_rule": "Errors remain in trial counts. Means use explicitly counted "
        "valid trials; missing worlds invalidate complete-comparison acceptance.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-root", type=Path, default=Path("jobs"))
    parser.add_argument("--job-prefix", required=True)
    parser.add_argument("--export", dest="export_dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expected-repeats", type=int, default=3)
    args = parser.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError("Output is not empty; choose a fresh directory")
    rows = collect(args.jobs_root, args.job_prefix, args.export_dir)
    if not rows:
        raise ValueError("No matching Harbor trials found")
    manifest = load_json(args.export_dir / "manifest.json", {})
    if args.expected_repeats < 1:
        parser.error("expected-repeats must be positive")
    report = summarize(rows, len(manifest["tasks"]), args.expected_repeats)
    report["export_manifest_sha256"] = digest(args.export_dir / "manifest.json")
    report["wheel_sha256"] = manifest["wheel_sha256"]
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=sorted({k for r in rows for k in r}), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    (args.out / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
