#!/usr/bin/env python3
"""Run the documented frontier harness without writing credentials to reports."""

import argparse
import os
import subprocess
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def credential_environment(env_file: Path | None) -> dict[str, str]:
    env = dict(os.environ)
    if env_file is None and not env.get("GOOGLE_GENERATIVE_AI_API_KEY"):
        local = ROOT / ".env"
        if local.is_file():
            env_file = local
        else:
            common = subprocess.check_output(
                ["git", "rev-parse", "--git-common-dir"], cwd=ROOT, text=True
            ).strip()
            candidate = (ROOT / common).resolve().parent / ".env"
            if candidate.is_file():
                env_file = candidate
    if env_file is not None:
        for key, value in dotenv_values(env_file).items():
            if value is not None and key not in env:
                env[key] = value
    if not env.get("GOOGLE_GENERATIVE_AI_API_KEY"):
        raise ValueError(
            "GOOGLE_GENERATIVE_AI_API_KEY is missing; set it or pass --env-file. "
            "Do not put the secret in a command argument."
        )
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--job-prefix", required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1 or args.concurrency < 1:
        parser.error("repeats and concurrency must be positive")
    env = credential_environment(args.env_file)
    for repeat in range(args.repeats):
        job = f"{args.job_prefix}-opencode-gemini-r{repeat}"
        if (ROOT / "jobs" / job).exists():
            raise ValueError(f"Job already exists: {job}; choose a fresh prefix")
        command = [
            "harbor",
            "run",
            "-p",
            str(args.export),
            "-a",
            "opencode",
            "-m",
            "google/gemini-3.8-flash",
            "--ak",
            "version=1.19.5",
            "--ak",
            "disable_web_search=true",
            "--api-key-name",
            "GOOGLE_GENERATIVE_AI_API_KEY",
            "--ae",
            "GOOGLE_GENERATIVE_AI_API_KEY=" + env["GOOGLE_GENERATIVE_AI_API_KEY"],
            "-o",
            "jobs",
            "--job-name",
            job,
            "--n-concurrent",
            str(args.concurrency),
        ]
        print(f"Running frontier repeat {repeat + 1}/{args.repeats}: {job}", flush=True)
        result = subprocess.run(command, cwd=ROOT, env=env, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
