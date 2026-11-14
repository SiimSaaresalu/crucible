#!/usr/bin/env python3
"""Extract only public scores and tool counts from local Harbor artifacts.

Never copy Harbor config.json or exported docker-compose files: they contain
provider or verifier capabilities. This collector reads only grade, transcript,
and selected usage fields from trial result files.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from pathlib import Path
from typing import Any

JOBS = {
    "oracle": "dispatch-1-oracle",
    "nop": "dispatch-1-nop",
    "opencode": "dispatch-1-opencode-gemini",
}


def collect(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for agent, name in JOBS.items():
        for trial in sorted((root / name).glob("dispatch-1-seed-*__*")):
            match = re.match(r"dispatch-1-seed-(\d+)__", trial.name)
            if match is None:
                continue
            grade_path = trial / "verifier/grade.json"
            if not grade_path.is_file():
                raise ValueError(f"Trial has no grade yet: {trial}")
            grade = json.loads(grade_path.read_text())
            result = json.loads((trial / "result.json").read_text())
            transcript = json.loads((trial / "verifier/transcript.json").read_text())
            steps = [record for record in transcript if record["type"] == "step"]
            usage: dict[str, Any] = result.get("agent_result") or {}
            states = [
                s["result"]["content"]
                for s in steps
                if s["result"]["ok"]
                and isinstance(s["result"]["content"], dict)
                and "orders" in s["result"]["content"]
            ]
            last_orders: list[dict[str, Any]] = states[-1]["orders"] if states else []
            unassigned = sum(o["status"] == "unassigned" for o in last_orders) + max(
                int(grade["breakdown"]["generated_orders"]) - len(last_orders), 0
            )
            agent_log = trial / "agent/opencode.txt"
            events = (
                [
                    json.loads(line)
                    for line in agent_log.read_text().splitlines()
                    if line.startswith("{")
                ]
                if agent_log.is_file()
                else []
            )
            mcp_events = [
                r["part"]
                for r in events
                if r.get("type") == "tool_use" and r["part"]["tool"].startswith("mcp-server_")
            ]
            tool_errors = [r for r in mcp_events if r.get("state", {}).get("status") == "error"]
            rows.append(
                {
                    "agent": agent,
                    "model": "google/gemini-3.8-flash" if agent == "opencode" else "",
                    "seed": int(match[1]),
                    "reward": grade["reward"],
                    "steps": grade["steps"],
                    "stop_reason": grade["stop_reason"],
                    "rejected_episode_calls": sum(not s["result"]["ok"] for s in steps),
                    "observed_mcp_calls": len(mcp_events) if mcp_events else len(steps),
                    "agent_tool_errors": len(tool_errors),
                    "post_completion_refusals": sum(
                        "Episode is over" in r["state"].get("error", "") for r in tool_errors
                    ),
                    "orders_left_unassigned": unassigned,
                    "missed_deadlines": grade["breakdown"]["late_deliveries"]
                    + grade["breakdown"]["undelivered_orders"],
                    "plan_calls": sum(s["call"]["name"] == "submit_dispatch_plan" for s in steps),
                    "state_reads": sum(s["call"]["name"] == "get_dispatch_state" for s in steps),
                    "map_reads": sum(s["call"]["name"] == "get_city_map" for s in steps),
                    "finish_calls": sum(s["call"]["name"] == "finish" for s in steps),
                    "tool_response_characters": sum(len(json.dumps(s["result"])) for s in steps),
                    "input_tokens": usage.get("n_input_tokens"),
                    "output_tokens": usage.get("n_output_tokens"),
                    "cost_usd": usage.get("cost_usd"),
                    "exception": result.get("exception_info") is not None,
                    "raw_trial_path": str(trial),
                    **grade["breakdown"],
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-root", type=Path, default=Path("jobs"))
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "harbor")
    args = parser.parse_args()
    rows = collect(args.jobs_root)
    if not rows:
        raise ValueError("No measured trials found")
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=sorted({key for row in rows for key in row}), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    summary: dict[str, Any] = {"agents": {}, "paired_reference_minus_model": {}}
    for agent in JOBS:
        cases = [r for r in rows if r["agent"] == agent]
        if cases:
            summary["agents"][agent] = {
                "trials": len(cases),
                "seeds": [r["seed"] for r in cases],
                "mean_reward": statistics.fmean(r["reward"] for r in cases),
                "sample_stddev_reward": statistics.stdev(r["reward"] for r in cases)
                if len(cases) > 1
                else 0.0,
                "mean_utility": statistics.fmean(r["total_utility"] for r in cases),
                "exceptions": sum(r["exception"] for r in cases),
                "rejected_episode_calls": sum(r["rejected_episode_calls"] for r in cases),
                "agent_tool_errors": sum(r["agent_tool_errors"] for r in cases),
                "post_completion_refusals": sum(r["post_completion_refusals"] for r in cases),
            }
    oracle = {r["seed"]: r for r in rows if r["agent"] == "oracle"}
    models = [r for r in rows if r["agent"] == "opencode"]
    if models:
        deltas = [oracle[r["seed"]]["reward"] - r["reward"] for r in models]
        summary["paired_reference_minus_model"] = {
            "mean_reward_difference": statistics.fmean(deltas),
            "wins": sum(d > 1e-9 for d in deltas),
            "ties": sum(abs(d) <= 1e-9 for d in deltas),
            "losses": sum(d < -1e-9 for d in deltas),
            "seeds": [r["seed"] for r in models],
        }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
