# Dispatch v2 findings

**Decision: no-go for a submission candidate.** The mechanics and optimization
reference work, but this candidate does not expose the proposed frontier-agent
limitation. Gemini beat the reference in every completed paired pilot trial.
No reward rescaling or post-held-out workload tuning was performed.

## Implementation

`dispatch@2` adds three capacity-limited vehicles, multiple physical depot tours,
bursty demand, high-value later arrivals, immutable loaded sequences, atomic
future-plan replacement, compact observations, and an OR-Tools CP-SAT reference.
The reference consumes the same four public tools as the agent and emits
variable-derived decision traces. The economic reward and0.35/km cost are unchanged.
Version1 golden values, historical experiment files and reproduction script are
preserved. The registry itself and general execution framework are unchanged.

Regime D was selected using development/calibration data before held-out
measurements. It has50–55 orders, three vehicles of capacity6–8, parcel sizes2–3,
and high-value urgent arrivals. Initial A–D native Mac results remain exploratory;
final reference measurements use the Linux runtime in `runtime-linux.json`.

## Canonical policy results

Each world was evaluated with six policies through the public tool contract.
All660 final development/calibration/held-out/salted episodes completed without
environment errors or invalid actions. The splits are disjoint.

| Split | Worlds | Solver | Greedy insertion | Deadline first | No-op |
| --- | ---: | ---: | ---: | ---: | ---: |
| Development |20|0.8810|0.8674|0.6374|0|
| Calibration |20|0.8630|0.8583|0.6399|0|
| Held-out |50|0.8755|0.8467|0.6132|0|
| Private salted |20|0.8765|0.8667|0.6057|0|

Held-out solver-minus-greedy reward is+0.0288079,95% normal-approximation interval
[+0.0154717,+0.0421442]; raw utility improves by$147.34 per world. Solver wins38
and loses12. Salted reward difference is+0.0097422 with interval
[-0.0107152,+0.0301996], so that split does not establish a reliable greedy gap.
These are heuristic comparisons, not held-out frontier proof.

On held-out worlds the solver averages$4,517.90 utility,81.92% on-time among
completed deliveries,74.05% on-time among generated orders, and5.24 unserved
orders. Mean measured solver compute is1.22seconds per episode, with search
controlled by deterministic work rather than wall time. Responses average about
101,561 serialized characters versus roughly340,000 in the original spike.

## Actual paired frontier pilot

Model configuration is the original `google/gemini-3.8-flash` through OpenCode
1.19.5, web search disabled. Three fresh salted exported worlds were reused for
reference, no-op, stop-early and all three independent model repeats.

| Seed | Reference | Gemini mean of3 | Gemini sample SD |
| --- | ---: | ---: | ---: |
|3100|0.8807984510|0.9309017637|0.0120966266|
|3101|0.8015359065|0.8573412063|0.0149483331|
|3102|0.9385793269|0.9664235008|0.0038861468|
| Mean |0.8736378948|0.9182221569|—|

The model wins all9 trials. Reference-minus-model is-0.0445842621 reward and
-$244.67 utility per world after averaging model repeats within each world.
The descriptive world-cluster bootstrap reward interval is[-0.0558053,-0.0278442].
Only three independent worlds were sampled; this is a pilot, not a population
estimate or a50-world frontier study. All nine trials have zero exceptions and
invalid in-episode actions. The broader frontier expansion stops at the no-go gate.

The first repeat's route evidence shows efficient urgent same-destination loads,
earlier availability for high-value bursts, and profitable terminal tours that
the reference conservatively excludes. See `frontier-diagnostics.md` for order IDs,
times and economic terms. There are no model-loss cases to attribute to planning
weakness. Associations and causal replay results are distinguished explicitly.

## Reference limits and reproducibility

The reference optimizes a bounded candidate-tour set, assumes currently observed
traffic persists, and conservatively requires optional tours to return by360.
Its current-plan objective can commit vehicles poorly for later demand.
Development interventions demonstrate remaining reference headroom; this result
does not determine a general upper limit on algorithmic routing performance.

Mac and Linux compiled OR-Tools builds choose different optimal ties and
budget-limited incumbents. Canonical Linux action replay and independently
regenerated reference match Harbor exactly in two fresh batches, including all
economic fields, calls, observations and SQLite transaction digests. Native Mac
reference scores are explicitly exploratory. Canonical v2 goldens were measured
and independently checked in Linux; unsupported runtime golden checks are marked
with an explicit runtime requirement. Version1 goldens remain unchanged.

The runtime runner verifies mounted source, dependencies, evaluator, installed
engine source, runtime versions and120 compiled binary hashes. It runs offline
with read-only source and writable experiment/log directories. Private salts,
provider credentials and grader capabilities remain in ignored runtime artifacts.

Fresh pinned-export validation exposed a bootstrap incompatibility: uv0.12.18
rejects exact3.14.10 and3.14.8 interpreter selectors. Those failed oracle runs
are not accepted reference scores. The export wrapper restores the successful
pilot selector3.14; fresh validation after this correction remains pending.
The successful original pilot and its exact canonical replay remain preserved.

## Verification and gate status

Full repository snapshot:648 tests passed,5 skipped. Subsequent native golden
checks:10 passed,3 explicitly skipped for the canonical-runtime requirement;
30 independent canonical golden episodes matched. Seven collector/packaging
tests pass, including rejecting failed oracle processes as legitimate zero
scores. Ruff and strict engine Pyright pass.

M1 mechanics, M3 workload calibration and M4 integrity have supporting evidence.
M2 provides a functioning optimizer with a modest heuristic advantage. M5 is
complete and negative for the research hypothesis. M6's positive unseen frontier
gap is **not achieved**. M7 has actual paired pilot runs and exact replay; the
fresh pinned-export retry remains an identified reproduction follow-up.

Results and commands are in `calibration.md`, `harbor.md`, `runtime.md`,
`candidate-d-v1.json`, `runtime-linux.json`, and the version-specific experiments.
Do not present this candidate as evidence of frontier planning weakness.
