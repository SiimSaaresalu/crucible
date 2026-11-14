#!/usr/bin/env python3
"""Export a fresh dispatch@2 batch with its canonical Linux runtime pinned."""

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, cast

import yaml
from crucible.core.jobs import parse_seeds
from crucible.export.harbor import (
    EngineArtifacts,
    build_engine_artifacts,
    export_harbor,
)

HERE = Path(__file__).resolve().parent


def export_pinned(
    artifacts: EngineArtifacts, seeds: list[int], out: Path
) -> list[Path]:
    """Use the existing task contract, then pin only the newly created batch."""
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError(f"Refusing to change an existing export: {out}")
    runtime: dict[str, Any] = json.loads((HERE / "runtime-linux.json").read_text())
    folders = export_harbor("dispatch@2", seeds, out, artifacts)
    for folder in folders:
        dockerfile = folder / "environment/mcp-server/Dockerfile"
        text = dockerfile.read_text()
        lines = text.splitlines()
        if sum(line.startswith("FROM ") for line in lines) != 1:
            raise ValueError(
                "Unexpected sidecar Dockerfile: expected exactly one base image"
            )
        dockerfile.write_text(
            "\n".join(
                f"FROM {runtime['base_image']}" if line.startswith("FROM ") else line
                for line in lines
            )
            + "\n"
        )
        solution = folder / "solution/solve.sh"
        text = solution.read_text()
        original = "--python 3.14 --quiet"
        if text.count(original) != 1:
            raise ValueError(
                "Unexpected oracle solution: expected the standard Python selector"
            )
        solution.write_text(
            text.replace(original, f"--python {runtime['oracle_python']} --quiet")
        )
        compose = folder / "environment/docker-compose.yaml"
        # This file is private. Preserve all capabilities/salts in place and do
        # not copy its contents into public reports or command output.
        config = cast(dict[str, Any], yaml.safe_load(compose.read_text()))
        for service in cast(dict[str, dict[str, Any]], config["services"]).values():
            service["platform"] = runtime["platform"]
        compose.write_text(yaml.safe_dump(config, sort_keys=False))
    manifest_path = out / "manifest.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text())
    manifest["inference_runtime"] = {
        name: runtime[name]
        for name in ("base_image", "platform", "python", "oracle_python", "ortools")
    }
    manifest["runtime_pin_scope"] = (
        "New dispatch@2 batch only; sidecar base, oracle Python, and service architecture. "
        "No change to task mechanics, salts, grader capabilities, or frontier agent engine access."
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return folders


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", required=True, help="For example 6000-6002")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--requirements", type=Path)
    args = parser.parse_args()
    if bool(args.wheel) != bool(args.requirements):
        parser.error("--wheel and --requirements must be supplied together")
    seeds = parse_seeds(args.seeds)
    if args.out.exists() and (not args.out.is_dir() or any(args.out.iterdir())):
        parser.error("Output is not empty; choose a fresh directory")
    with tempfile.TemporaryDirectory(prefix="dispatch-v2-export-") as directory:
        artifacts = (
            EngineArtifacts(wheel=args.wheel, requirements=args.requirements)
            if args.wheel
            else build_engine_artifacts(Path(directory))
        )
        folders = export_pinned(artifacts, seeds, args.out)
    for folder in folders:
        print(folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
