#!/usr/bin/env python3
"""Run canonical reference measurements in the frozen Linux inference image."""

import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SCRIPTS = {
    "evaluate": "evaluate.py",
    "replay": "replay_harbor.py",
    "golden": "golden_v2.py",
}

# Check the installed wheel against the frozen source before consuming private
# salts or measuring policies. The mounted repository is read-only except logs
# and experiment outputs. Provider credentials are never passed into this image.
PREFLIGHT = """
import hashlib,json,os,platform,runpy,sys,uuid
from pathlib import Path
import crucible
import ortools
manifest=json.loads(Path('/repo/spikes/dynamic-dispatch-v2/candidate-d-v1.json').read_text())
runtime=json.loads(Path('/repo/spikes/dynamic-dispatch-v2/runtime-linux.json').read_text())
identity=runtime['runtime_identity']
actual={'system':platform.system(),'machine':platform.machine(),
        'python':platform.python_version(),'ortools':ortools.__version__,
        'libc':list(platform.libc_ver())}
for name,value in actual.items():
    if value!=identity[name]:
        raise RuntimeError('Unsupported compiled inference runtime: '+name)
for name,expected in identity['binary_sha256'].items():
    path=Path(name)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise RuntimeError('Canonical runtime binary differs: '+name)
package=Path(crucible.__file__).parent
prefix='engine/src/crucible/'
for name,expected in manifest['source_files'].items():
    mounted=Path('/repo')/name
    if not mounted.is_file() or hashlib.sha256(mounted.read_bytes()).hexdigest()!=expected:
        raise RuntimeError('Mounted source/dependency/evaluator differs from freeze: '+name)
    if name.startswith(prefix):
        path=package/name.removeprefix(prefix)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise RuntimeError('Installed runtime wheel differs from frozen source: '+name)
proof={'image_id':os.environ['CRUCIBLE_DISPATCH_RUNTIME_IMAGE_ID'],
       'source_sha256':manifest['source_sha256'],'runtime':actual,
       'native_binary_files_checked':len(identity['binary_sha256']),
       'source_files_checked':len(manifest['source_files']),'script':sys.argv[1],
       'arguments':sys.argv[2:]}
proofs=Path('/repo/spikes/dynamic-dispatch-v2/experiments/runtime-proofs')
proofs.mkdir(exist_ok=True)
(proofs/(uuid.uuid4().hex+'.json')).write_text(json.dumps(proof,indent=2,sort_keys=True)+'\\n')
script=sys.argv[1]
sys.argv=sys.argv[1:]
runpy.run_path(script,run_name='__main__')
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", help="Explicit retained/rebuilt canonical Linux image"
    )
    parser.add_argument("script", choices=SCRIPTS)
    parser.add_argument("script_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    image = args.image or os.environ.get("DISPATCH_RUNTIME_IMAGE")
    if not image:
        manifest = HERE / "runtime-linux.json"
        if not manifest.is_file():
            parser.error(
                "Runtime manifest missing; provide --image after building the runtime"
            )
        image = json.loads(manifest.read_text())["image_id"]
    # Resolve tags once so the run cannot race a mutable tag replacement.
    resolved_image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image], text=True
    ).strip()
    writable = (ROOT / "engine/runs", HERE / "experiments")
    for path in writable:
        path.mkdir(parents=True, exist_ok=True)
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--platform",
        "linux/arm64",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--workdir",
        "/repo",
        "--env",
        f"CRUCIBLE_DISPATCH_RUNTIME_IMAGE_ID={resolved_image}",
        "--mount",
        f"type=bind,source={ROOT},target=/repo,readonly",
    ]
    for path in writable:
        command.extend(
            [
                "--mount",
                f"type=bind,source={path},target=/repo/{path.relative_to(ROOT)}",
            ]
        )
    command.extend(
        [
            "--entrypoint",
            "python",
            resolved_image,
            "-c",
            PREFLIGHT,
            f"/repo/spikes/dynamic-dispatch-v2/{SCRIPTS[args.script]}",
            *args.script_args,
        ]
    )
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
