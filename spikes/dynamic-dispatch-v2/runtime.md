> Current limitation: exact oracle Python patch selectors failed under uv0.12.18.
> New exports now use the successful pilot selector `--python 3.14`; the separate
> evaluator remains pinned to Python3.14.10 and120 native binary hashes.
> A fresh export retry after this correction remains pending. Earlier text about
> an exact oracle patch pin is superseded by this note.

# Canonical reference runtime

OR-Tools' single-worker deterministic work limit reproduces inference within a
fixed compiled runtime. Native macOS and Linux produced different choices in
this experiment: both equal-value optimal assignments and different limited
search incumbents. Seed, salt, and recorded actions still produced identical
simulator outcomes across those platforms. Native development measurements are
exploratory; final reference comparisons use the Linux runtime manifest.

`runtime-linux.json` records the measured Linux ARM64 image ID, Python 3.14.10,
OR-Tools 9.15.6755, immutable Python base, wheel and requirements hashes. The
trusted evaluator image includes engine code and must never be used as a
frontier-agent image. `run_linux.py` passes no provider credentials, disables
network access, mounts source read-only, permits only logs/results writes, and
checks installed execution files against the frozen candidate source manifest.

## Fresh export and runtime build

```bash
./spikes/dynamic-dispatch-v2/reproduce.sh setup
DISPATCH_EXPORT_DIR=harbor/dispatch-2-new-check \
DISPATCH_HARBOR_SEEDS=6000-6002 \
  ./spikes/dynamic-dispatch-v2/reproduce.sh export
DISPATCH_EXPORT_DIR=harbor/dispatch-2-new-check \
  ./spikes/dynamic-dispatch-v2/reproduce.sh runtime
```

The v2 export wrapper uses the existing Crucible task/Harbor export contract.
Only the new batch is changed: its sidecar uses the immutable Python base, its
oracle requests Python 3.14.10, and both services select `linux/arm64`. Task IDs,
private salts, grading capabilities, engine wheel, and tool contracts are
preserved. Public export metadata records runtime pins without credentials.
Nonempty export directories are refused. Existing exports and all v1 paths are
unchanged. The v1 script remains `spikes/dynamic-dispatch/reproduce.sh`.

The direct wrapper also accepts existing wheel/requirements artifacts:

```bash
engine/.venv/bin/python spikes/dynamic-dispatch-v2/export_v2.py \
  --seeds 6000-6002 --out harbor/dispatch-2-new-check \
  --wheel path/to/crucible-0.1.0-py3-none-any.whl \
  --requirements path/to/requirements.txt
```

Rebuilding an image can change its OCI ID despite identical pinned execution
files. The reproduction script uses the named locally built runtime image;
direct `run_linux.py` defaults to the originally measured immutable ID. Use an
explicit rebuilt tag when reproducing on another host. Frozen source checks
still apply; a stale wheel fails before measurement.

## Canonical comparisons and replay

```bash
DISPATCH_CANDIDATE=replication-1 \
  ./spikes/dynamic-dispatch-v2/reproduce.sh calibration
DISPATCH_CANDIDATE=replication-1 \
  ./spikes/dynamic-dispatch-v2/reproduce.sh local

engine/.venv/bin/python spikes/dynamic-dispatch-v2/run_linux.py \
  --image crucible-dispatch-v2-runtime:candidate-d-v1 evaluate \
  --task dispatch@2 --regime D --split heldout --candidate replication-2
```

`reproduce.sh replay` also uses this canonical runtime for dispatch@2. Its export
and job prefix must identify the same preserved worlds as the oracle/model
comparison. The collector reports model repeats by paired world; it does not
make a native reference score equivalent to a Linux reference score.

On an x86 host, the explicit ARM64 runtime can require emulation. Timing may
change, while inference is controlled by deterministic work units. Do not
silently switch architecture to improve speed and then claim identical scores.
