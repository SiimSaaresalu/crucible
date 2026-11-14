# Dispatch v2 calibration record

Status: the full A–D matrix completed on 20 development and 20 calibration
worlds per regime. Regime D is frozen as `candidate-d-v1` for the frontier pilot.
No held-out experiment informed selection.

The fixed seed splits are development 3000–3019, calibration 3100–3119,
held-out 4000–4049, and salted validation 5000–5019. A–D regimes are defined in
`engine/src/crucible/envs/dispatch/v2/scenarios.py`. Development and calibration
may inform workload/reference selection; held-out and salted checks may not.

Use the normal public-tool session path:

```bash
engine/.venv/bin/python spikes/dynamic-dispatch-v2/evaluate.py \
  --task dispatch@2 --regime A --split development \
  --candidate candidate-name \
  --policies rolling_horizon_solver,nearest_first,deadline_first,greedy_insertion,stop_early,do_nothing
```

Run each A–D regime under the same candidate and stable source fingerprint.
A `--limit` run is explicitly marked as a partial split. Measurements are rejected
if source changes during the run, if any episode fails, or if a policy issues an
invalid action. Errors remain in raw rows; summaries disclose metric sample sizes.
Wall-clock runtime is observational and never controls solver search.

Outputs default to `experiments/<candidate>/<regime>/<split>/`. They include raw
CSV records, source/dependency/evaluator hashes, paired reward and raw-utility
comparisons, normal-approximation intervals, and artifact paths. Existing output
cannot be overwritten using the same candidate. A replacement needs an explicit
new candidate and `--overwrite`; prior output is archived rather than deleted.

Raw transcripts, solver traces, and salt manifests remain under gitignored
`engine/runs/dispatch-v2/`. Salted validation generates a private manifest once
per candidate/regime, then reuses it. Exact replay under a different output can
use `--salt-manifest <private-manifest-path>`; public reports contain only its
SHA-256. Do not copy raw private manifests into this experiment directory.

Tooling smoke checks (not a regime-selection result):

| Candidate | Split / worlds | Greedy reward | Greedy raw utility | No-op reward / utility |
| --- | --- | ---: | ---: | --- |
| `tooling-final-smoke-v2` | Development / 1 | 0.9565266792 | 2680.1877551 | 0 / 0 |
| `tooling-salt-smoke-v2` | Salted / 1 | 0.9646276043 | 2549.5107583 | 0 / 0 |
| `tooling-salt-replay-v2` | Same private world / 1 | Same as prior | Same as prior | 0 / 0 |

The one-world smoke checks validate evaluator mechanics, not difficulty or an
optimization advantage. Solver and baseline comparisons across the full
predeclared development/calibration matrix remain to be run after source freeze.

## Native Mac matrix (exploratory)

All 960 native Mac episodes passed within-run integrity with zero environment
errors and invalid actions. These are exploratory measurements: compiled OR-Tools
choices differ from Linux. Canonical Linux results are recorded in findings.md.
Reward normalization and the 0.35/km cost were unchanged. Source fingerprint:
`ca62a4ad0e7fdbc92261b23faf9619018c6c6b16189f419912cf641b31b10d05`.

| Regime | Split (20 worlds) | Solver | Greedy | Deadline | Paired solver−greedy, 95% CI |
| --- | --- | ---: | ---: | ---: | --- |
| A | development | 0.9679 | 0.9613 | 0.9133 | +0.0066 [+0.0024, +0.0108] |
| A | calibration | 0.9652 | 0.9568 | 0.9008 | +0.0084 [-0.0003, +0.0171] |
| B | development | 0.9119 | 0.8801 | 0.7488 | +0.0318 [+0.0180, +0.0455] |
| B | calibration | 0.9066 | 0.8738 | 0.6851 | +0.0328 [+0.0153, +0.0503] |
| C | development | 0.8928 | 0.8737 | 0.7173 | +0.0190 [+0.0045, +0.0335] |
| C | calibration | 0.8878 | 0.8616 | 0.6974 | +0.0262 [+0.0110, +0.0415] |
| D | development | 0.8872 | 0.8674 | 0.6374 | +0.0198 [+0.0001, +0.0394] |
| D | calibration | 0.8766 | 0.8583 | 0.6399 | +0.0183 [+0.0061, +0.0304] |

D was selected because bursts and high-value later arrivals exercise economic
allocation while the reference still realizes approximately 88% of potential
value after driving costs. The solver advantage over greedy is modest; this is
not evidence of the desired frontier gap. B has a larger solver–greedy gap, but
lacks D's high-value-arrival component. All measured regimes remain disclosed.

The final default changes from A to D without altering any regime distribution.
The full D calibration was repeated as `candidate-d-v1` and reproduced every
policy mean exactly. Final source fingerprint is recorded in
`candidate-d-v1.json`. The solver diagnostic report identifies myopic commitment
losses and quantifies the conservative terminal-return restriction; no harder
workload was introduced in response.
