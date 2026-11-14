#!/usr/bin/env python3
"""Run stop-early through Harbor against the unmodified paired export."""

import argparse
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--job-prefix", required=True)
    args = parser.parse_args()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(HERE) + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        "harbor",
        "run",
        "-p",
        str(args.export),
        "-a",
        "stop_early_agent:StopEarlyOracle",
        "--ak",
        f"dispatch_export_dir={args.export.resolve()}",
        "-o",
        "jobs",
        "--job-name",
        f"{args.job_prefix}-stop-early",
        "--n-concurrent",
        "3",
    ]
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
