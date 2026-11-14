# Actual v2 outcome and reproduction status

The completed pilot has three exported salted worlds and three independent
Gemini/OpenCode trials per world. Reference mean reward is **0.8736378948**;
Gemini mean is **0.9182221569**. The model wins all nine trials. Paired
reference-minus-model reward is **-0.0445842621**, and raw utility is **-$244.67**
per world after averaging repeats. Nine trials have zero exceptions and invalid
in-episode actions. No-op is exactly zero on all three worlds; actual Harbor
stop-early mean is0.1452592750 with four calls per episode.

Two independent canonical Linux replay batches reproduce all three reference
worlds exactly: actions, observations, economics,14calls and SQLite history
hashes. Native Mac regenerated reference differs and is exploratory. See
`runtime-linux.json`, `findings.md`, and `experiments/candidate-d-v1/harbor-pilot/`.

Fresh validation exports using exact Python3.14.10 and3.14.8 selectors failed
oracle bootstrap under uv0.12.18. Their zero scores are invalid harness results,
not legitimate reference outcomes. Artifacts are retained in ignored jobs and
exports. The collector rejects nonzero agent exit codes. The wrapper now restores
the successful minor selector3.14; a fresh retry remains pending. Two earlier
custom stop-early adapter startup failures were fixed before its successful run.

The following original tooling instructions are retained for command reference;
any earlier pending status or exact-patch claim is superseded by this record.

# Dispatch v2 Harbor reproduction

Status: tooling implemented. V2 exports and actual v2 Harbor runs are pending
the coordinator's candidate freeze. Historical v1 results are unchanged.

Run commands from the repository root with the locked engine environment.
The new `reproduce.sh` retains separate setup, local, export, Harbor, and frontier
modes; the original `spikes/dynamic-dispatch/reproduce.sh` remains the v1 path.

## Freeze, export, and paired trials

First record the selected workload, solver configuration, source fingerprint,
dependency lock, and default v2 regime. The exporter uses that frozen default
regime; setting a shell variable cannot change an already exported world.

```bash
./spikes/dynamic-dispatch-v2/reproduce.sh setup
DISPATCH_CANDIDATE=candidate-1 \
DISPATCH_EXPORT_DIR=harbor/dispatch-2-candidate-1-pilot \
DISPATCH_HARBOR_SEEDS=3100-3102 \
  ./spikes/dynamic-dispatch-v2/reproduce.sh export

DISPATCH_EXPORT_DIR=harbor/dispatch-2-candidate-1-pilot \
DISPATCH_JOB_PREFIX=dispatch-2-candidate-1-pilot \
  ./spikes/dynamic-dispatch-v2/reproduce.sh harbor

DISPATCH_EXPORT_DIR=harbor/dispatch-2-candidate-1-pilot \
DISPATCH_JOB_PREFIX=dispatch-2-candidate-1-pilot \
DISPATCH_MODEL_REPEATS=3 \
  ./spikes/dynamic-dispatch-v2/reproduce.sh frontier
```

Frontier uses the established `opencode`, `google/gemini-3.8-flash`, OpenCode
`1.19.5`, web-search-disabled configuration. `frontier.py` reads the key from
the process environment, an explicit `--env-file`, the worktree `.env`, or the
main checkout's gitignored `.env`. It does not print or copy credential values
to experiment reports. Harbor's raw runtime configuration can contain provider
credentials and must stay in ignored private runtime storage.

Each OpenCode repeat has a separate `...-opencode-gemini-rN` job. All repeats,
oracle, and nop must use the same preserved export. Fresh exports generate fresh
private salts; matching public seeds alone does not establish paired worlds.
Choose fresh export directories and job prefixes rather than overwriting.

Stop-early requires a separately exported solution policy because Harbor's
oracle executes the solution specified by the export. Do not substitute `nop`
for stop-early. Copying the preserved export privately and replacing only the
solution policy requires confirming identical sidecar-world digests; alternatively
use the live MCP stop-early integrity test and report Harbor stop-early as pending.

## Public measurement collection

```bash
engine/.venv/bin/python spikes/dynamic-dispatch-v2/collect_harbor.py \
  --jobs-root jobs --job-prefix dispatch-2-candidate-1-pilot \
  --export harbor/dispatch-2-candidate-1-pilot \
  --out spikes/dynamic-dispatch-v2/experiments/candidate-1/harbor-pilot \
  --expected-repeats 3
```

The collector checks every trial's export path, records salted-world and wheel
digests, and copies only public grades/tool counts/usage metrics. It includes
missing grades, exceptions, invalid calls, unpaired worlds, and incomplete
repeats in the report. Missing outcomes are not silently assigned fabricated
economic utility. Means explicitly state the number of valid trials.

Model repeats are averaged within each world. Confidence intervals resample
paired worlds as clusters (10,000 percentile bootstrap resamples, seed 0), so
repeats do not artificially inflate the number of independent worlds. Reports
include paired reward and raw economic utility, wins/ties/losses, and repeat
variability. Three-world pilot intervals are descriptive, not held-out proof.

## Private salted replay and oracle parity

```bash
engine/.venv/bin/python spikes/dynamic-dispatch-v2/replay_harbor.py \
  --jobs-root jobs --job-prefix dispatch-2-candidate-1-pilot \
  --export harbor/dispatch-2-candidate-1-pilot \
  --out spikes/dynamic-dispatch-v2/experiments/candidate-1/harbor-pilot-replay.json
```

The replay helper reads private sidecar salts internally, independently replays
the oracle's action sequence, and regenerates the reference through ordinary
task tools in another isolated SQLite episode. It checks reward, full economic
breakdown, flags, call count, stop reason, action sequence, public observations,
and matching backend transaction digests across the independent local runs.
The comparison report contains hashes, never salts or grading capabilities.
Use `--replay-name` and a fresh report path for a second replay.

Run against the installed frozen source that produced the exported wheel. A
source change can alter the reference or scenario and must fail parity rather
than being explained away. Private transcripts stay under ignored
`engine/runs/dispatch-v2/harbor-replay/`; they contain public tool outcomes only.

## Tooling verification

The collector was checked against the existing preserved v1 Harbor artifacts:
oracle mean reward 0.919312359303174, frontier mean 0.945084879771609, and nop
exactly zero, reproducing the published values without modifying those files.
This is tooling validation, not a v2 experiment.

The new replay helper also passed against all three preserved v1 salted oracle
worlds. Action playback and regenerated reference each matched reward, economic
breakdown, flags, 13 calls, stop reason, complete actions/observations, and the
independent SQLite event-history digest. These six replay episodes wrote only
new ignored runtime files; original exports/jobs/results were read-only inputs.

Synthetic tests check world-level repeat averaging, deterministic bootstrap,
failed/missing-world accounting, rejection of a different export, and absence
of private compose contents in collected output. Actual v2 export, oracle,
frontier, salted parity, and final failure analysis must be recorded after they
run; no such measurements are claimed here.
