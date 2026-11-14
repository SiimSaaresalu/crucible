#!/usr/bin/env python3
"""Privately replay exported salted worlds and compare public Harbor outcomes.

Reads sidecar salts internally. Output contains hashes and comparison outcomes,
never compose contents, credentials, or salts. Run against the frozen installed
engine matching the export's wheel; source changes should cause parity failure.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import yaml
from crucible.core.jobs import Job
from crucible.core.registry import get_policy, get_task
from crucible.core.session import EpisodeSession
from crucible.core.tools import Observation, ToolCall, result_text
from crucible.core.transcript import TranscriptWriter

ROOT = Path(__file__).resolve().parents[2]


def hash_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def matches_numbers(actual: Any, expected: Any) -> bool:
    if isinstance(actual, dict) and isinstance(expected, dict):
        left, right = cast(dict[str, Any], actual), cast(dict[str, Any], expected)
        return left.keys() == right.keys() and all(
            matches_numbers(left[k], right[k]) for k in left
        )
    if isinstance(actual, (float, int)) and isinstance(expected, (float, int)):
        return abs(actual - expected) <= 1e-9
    return actual == expected


async def replay(
    task_ref: str,
    seed: int,
    salt: str,
    expected_steps: list[dict[str, Any]],
    expected_grade: dict[str, Any],
    private_out: Path,
    *,
    regenerate: bool,
) -> dict[str, Any]:
    task = get_task(task_ref)
    policy_name = "reference" if regenerate else "action_replay"
    job = Job(task=task_ref, policy=policy_name, seed=seed)
    session = EpisodeSession(
        task, seed, TranscriptWriter(private_out / f"{job.job_id}.jsonl"), salt=salt
    )
    error: str | None = None
    actual_calls: list[dict[str, Any]] = []
    observations_match = True
    try:
        observation = await session.start(policy_name=policy_name, job=job)
        policy = get_policy("reference", task_ref) if regenerate else None
        if policy is not None:
            await policy.reset(task.spec, task.tools(), task.instruction())
        for index in range(session.max_steps):
            if task.done():
                break
            if policy is not None:
                call = await policy.act(observation)
            else:
                call = (
                    ToolCall.model_validate(expected_steps[index]["call"])
                    if index < len(expected_steps)
                    else None
                )
            if call is None:
                break
            result = await session.step(call)
            actual_calls.append(call.model_dump(mode="json"))
            if (
                index >= len(expected_steps)
                or result.model_dump(mode="json") != expected_steps[index]["result"]
            ):
                observations_match = False
            observation = Observation(text=result_text(result), last_result=result)
        reason, score = await session.close()
        simulation = getattr(task, "_simulation", None)
        if simulation is not None:
            await simulation.finish()
            history_hash = hash_json(await simulation.backend.history())
        else:
            history_hash = None
        expected_calls = [s["call"] for s in expected_steps]
        return {
            "mode": "regenerated_reference" if regenerate else "action_replay",
            "seed": seed,
            "reward": score.total,
            "raw_utility": score.breakdown.get("total_utility"),
            "steps": session.steps_used,
            "reward_matches": matches_numbers(score.total, expected_grade["reward"]),
            "economic_breakdown_matches": matches_numbers(
                score.breakdown, expected_grade["breakdown"]
            ),
            "flags_match": score.flags == expected_grade.get("flags", {}),
            "calls_match": session.steps_used == expected_grade["steps"],
            "stop_reason_matches": reason == expected_grade["stop_reason"],
            "action_sequence_matches": actual_calls == expected_calls,
            "observations_match": observations_match,
            "history_sha256": history_hash,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - retain failed replay evidence without secret text
        error = type(exc).__name__
        await session.close(error)
        return {"mode": policy_name, "seed": seed, "error": error}
    finally:
        simulation = getattr(task, "_simulation", None)
        if simulation is not None:
            await simulation.close()


async def run(args: argparse.Namespace) -> int:
    export = args.export_dir.resolve()
    manifest = json.loads((export / "manifest.json").read_text())
    private_out = (
        ROOT
        / "engine/runs/dispatch-v2/harbor-replay"
        / (args.replay_name or args.job_prefix)
    )
    if private_out.exists():
        raise ValueError(
            "Replay runtime output already exists; choose a new --replay-name"
        )
    private_out.mkdir(parents=True, mode=0o700)
    rows: list[dict[str, Any]] = []
    for folder_name in manifest["tasks"]:
        folder = export / folder_name
        compose_path = folder / "environment/docker-compose.yaml"
        env = yaml.safe_load(compose_path.read_text())["services"]["mcp-server"][
            "environment"
        ]
        task_ref, seed, salt = (
            env["SIDECAR_TASK"],
            int(env["SIDECAR_SEED"]),
            env["SIDECAR_SALT"],
        )
        trials = sorted(
            (args.jobs_root / f"{args.job_prefix}-oracle").glob(f"{folder_name}__*")
        )
        if len(trials) != 1:
            rows.append({"seed": seed, "error": "expected exactly one oracle trial"})
            continue
        trial = trials[0]
        grade_path, transcript_path = (
            trial / "verifier/grade.json",
            trial / "verifier/transcript.json",
        )
        if not grade_path.is_file() or not transcript_path.is_file():
            rows.append({"seed": seed, "error": "missing oracle verification artifact"})
            continue
        result: dict[str, Any] = json.loads((trial / "result.json").read_text())
        task_id: dict[str, Any] = result.get("task_id") or {}
        task_path = task_id.get("path")
        if not task_path or (ROOT / task_path).resolve() != folder:
            rows.append({"seed": seed, "error": "oracle export path mismatch"})
            continue
        grade = json.loads(grade_path.read_text())
        steps = [
            r for r in json.loads(transcript_path.read_text()) if r["type"] == "step"
        ]
        results = [
            await replay(
                task_ref, seed, salt, steps, grade, private_out, regenerate=mode
            )
            for mode in (False, True)
        ]
        hashes = [r.get("history_sha256") for r in results]
        for row in results:
            row["world_sha256"] = hashlib.sha256(compose_path.read_bytes()).hexdigest()
            row["independent_histories_match"] = (
                len(set(hashes)) == 1 and hashes[0] is not None
            )
            rows.append(row)
    checks = (
        "reward_matches",
        "economic_breakdown_matches",
        "flags_match",
        "calls_match",
        "stop_reason_matches",
        "action_sequence_matches",
        "observations_match",
        "independent_histories_match",
    )
    accepted = bool(rows) and all(
        not r["error"] and all(r.get(k) for k in checks) for r in rows
    )
    report = {
        "accepted": accepted,
        "wheel_sha256": manifest["wheel_sha256"],
        "rows": rows,
        "privacy": "Only salted-world digests are public. Private salts read in memory.",
    }
    if args.out.exists():
        raise ValueError("Report exists; choose a fresh --out")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(not accepted)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-root", type=Path, default=ROOT / "jobs")
    parser.add_argument("--job-prefix", required=True)
    parser.add_argument("--export", dest="export_dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--replay-name", help="Fresh private runtime folder name for repeated replays"
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
