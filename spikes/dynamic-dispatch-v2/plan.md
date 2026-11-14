# Crucible Phase 1 — Dynamic Dispatch v2 implementation plan

Status: implementation and pilot completed; the research submission gate is no-go.
See `findings.md` for measured results and remaining reproduction limitations.
Starting branch: `codex/dynamic-delivery-dispatch`. Task: `dispatch@2`.

## Research contract

Test whether online capacity allocation, time windows, geography, and changing
demand produce a repeatable optimization-reference advantage over frontier
agents. The desired reward gap of 0.15–0.30 is a research target, not a result
to manufacture or an unconditional delivery promise.

Preserve `dispatch@1` behavior, golden entries, measured artifacts, and Harbor
reproduction. Retain FastAPI, SQLite, authenticated simulator events, and the
four MCP tools. Exclude Fleetbase, mapping APIs, RL, and general framework
redesign. All decision policies use only public observations and actions.

Keep the economic definition unchanged:

```text
realized_value(order) = value * max(0, 1 - max(0, delivered_at-deadline)/grace)
utility = sum(realized_value) - 0.35 * physically_driven_km
reward = clip(utility / total_generated_order_value, 0, 1)
```

The private denominator includes unreleased and unserved generated orders. It
is action independent. Do not normalize by solver performance or retune costs
to enlarge a gap. No-op must have exactly zero distance, utility, and reward.
Finishing early executes existing commitments to minute 360 without adding a
special stopping penalty.

## Repository findings and implementation boundaries

- Existing mechanics live in `engine/src/crucible/envs/dispatch/{world,backend}.py`
  and `backend_service/{models,database,app}.py`. `Plan.routes` currently maps
  vehicles to a single list; the store validates total assigned route capacity.
- `world.py:state()` currently embeds `city_map()`, including complete time and
  distance matrices, in every operational response. V2 needs its own projection.
- `task.py` registers version 1; the registry already supports `dispatch@2`.
  Policy registration, however, keys on `(policy_name, task_id)` and discards
  the version. Do not introduce a general registry migration.
- Keep existing v1 policy implementations intact. Use dispatch-local policy
  factories/adapters which select an implementation in `reset(task_spec, ...)`.
  `reference` selects regret insertion for v1 and the solver for v2. Adapt named
  baselines the same way. Register `rolling_horizon_solver` explicitly; reject
  an unsupported task version clearly. Test v1 aliases and v2 oracle selection.
- Prefer a `dispatch/v2/` package for version-specific task, scenario, observation,
  scoring configuration, and policies. Reuse existing HTTP/authentication and
  numerical scoring components. Extend shared mechanics through explicit v1/v2
  configuration with v1 defaults; avoid copying the entire environment.
- Existing golden coverage requires every registered task to have entries.
  Add v2 entries only after deterministic behavior is frozen; never regenerate
  or overwrite v1 values. Update exact task-list assertions for the new version.
- `spikes/dynamic-dispatch/` is the v1 experiment record. Put v2 scripts and all
  new results in this directory. Preserve the original scripts and artifacts.

## Coordination and handoff rules

The coordinator owns sequencing, shared-file integration, interface review,
and milestone decisions. Use dedicated subagents for the assignments below.
There are four concurrency slots: at most three implementation subagents run
alongside the coordinator. A subagent may proceed only when its inputs exist;
do not fill an unspecified contract with incompatible local assumptions.

The coordinator alone integrates edits to `dispatch/__init__.py`, existing
policy registration modules, `engine/pyproject.toml`, the dependency lockfile,
and generic task-list/golden fixtures. Backend and simulator owners agree on
the contract before editing their separate files. Each handoff includes:

1. Files changed and the supported interface/schema version.
2. Commands run and their actual outcomes, including unresolved failures.
3. A small reproducible fixture or transcript demonstrating the deliverable.
4. Remaining limitations and evidence required by the next gate.

Do not label an unrun experiment complete. Record unavailable runtime or
credentials as blockers for the affected experiment while finishing independent
work. Never substitute local policy trials for actual OpenCode/Harbor trials.

## Step 0 — Freeze v1 and publish the v2 contract

**Owner:** coordinator. **Dependencies:** none.

1. Record the base commit, clean/dirty state, v1 source/config fingerprints,
   current golden entries, and existing result-file hashes in `v1-baseline.json`.
2. Run v1 tests and golden comparisons; capture the existing Harbor reproduction
   commands and model pin without changing historical results.
3. Publish `contracts.md` with route JSON examples, HTTP/event schemas, public
   observations, scoring, service times, and deterministic tie-breaking.
4. Establish v2 registration/module layout and policy reset adapters. Verify
   both explicit task versions; bare `dispatch` resolving to v2 is expected.
5. Verify that a pinned OR-Tools release has an installable build for the
   repository's Python >=3.14 runtime and Harbor target architecture. Test a
   minimal solve in that runtime. Resolve packaging compatibility before solver
   implementation; do not silently replace OR-Tools or downgrade the project.

Contract decisions to make concrete before Steps 1–3:

- `routes: dict[str, list[list[str]]]`; omitted vehicles retain their complete
  plans; `{}` waits 30 minutes; `[]` clears only legally cancellable work.
  Reject empty inner tours and ambiguous legacy flat lists in v2.
- Each inner list is one load and its delivery order. Capacity uses parcel
  sizes, independently per load. Every tour requires physical depot pickup;
  tours return to the depot, including the final tour. Return travel consumes
  time and costs distance, including movement truncated at shift end.
- Once pickup starts, the remaining loaded delivery sequence is immutable.
  The first submitted list for that vehicle must preserve that suffix. Later
  unstarted tours can be atomically replaced or reassigned. Onboard parcels
  cannot be removed, reordered, moved to later tours, or transferred.
- A road edge already entered must complete before a new movement decision.
  A return already started remains a depot commitment. Unstarted future plans
  may change without canceling that edge or return. Expose these locks explicitly.
- No mid-tour top-ups: finish the current load, return, then collect the next
  load. Declare fixed loading/delivery service times (zero is allowed if chosen
  deliberately) and enforce them consistently in simulator and solver.
- An order must already be released to be assigned. All listed unpicked orders
  remain depot inventory reserved by the current plan. Reject delivered orders,
  duplicate IDs across all retained/replaced tours, and future/invented IDs.
- Define event ordering for equal timestamps, pickups at decision boundaries,
  canceled reservations, interrupted loading, and shift-end partial edges.
  Invalid submissions change neither clock, plans, reservations, nor history.

**Exit:** contract fixtures agreed; v1 baseline passes; OR-Tools packaging works.

## Step 1 — Multi-tour backend and durable validation

**Subagent:** `v2_backend`. **Dependencies:** Step 0 contract.
**Owns:** `backend_service/{models,database,app}.py`, `backend.py`, backend tests.

1. Add explicit versioned plan/state contracts with v1 defaults preserved.
2. Persist current tour, pending tours, cargo, reservations, locked sequence,
   depot return/loading state, and physical edge information needed for validation.
3. Build a complete proposed fleet plan, including omitted vehicles, then validate
   every tour and cross-vehicle constraint before a single SQLite transaction.
4. Authenticate pickup/move/delivery events. Require depot presence and eligible
   reserved inventory at pickup; enforce cargo, order, and destination at delivery.
   Public plan fields cannot claim movement, pickup, completion, or value.
5. Retain deterministic transaction ordering and restart persistence. Ensure a
   failed replacement cannot partially release reservations or mutate history.
6. Test two tours, later-tour overload, duplicate retained/replaced assignments,
   cargo reassignment, locked suffix edits, replayed completion, fake depot visits,
   invalid events, and rollback. Exercise ASGI and standalone HTTP persistence.

**Exit evidence:** durable two-tour backend trace and atomic-rejection tests.

## Step 2 — Physical simulator and action-independent demand

**Subagent:** `v2_mechanics`. **Dependencies:** Step 0; integrate Step 1 API.
**Owns:** `world.py`, v2 scenario/config modules, world/replay tests.

1. Introduce explicit v2 configuration without changing the v1 random stream,
   event ordering, movement rules, or generated worlds.
2. Implement depot → load → deliveries → depot → reload as a physical state
   machine. Account for service time, all driven distance, locked edges, and
   shift truncation. Avoid zero-time loops and duplicate pickups/deliveries.
3. Derive named random streams from seed, salt, version, and regime. Generate
   the full private order/traffic schedule before actions; actions cannot affect
   future arrivals, values, geographic clusters, or disruptions.
4. Add steady arrivals, urgent bursts, deadline-competing clusters, and high-value
   later arrivals. Start with 3 vehicles, capacities 6–8, 40–55 orders, a 360-minute
   horizon, and 12 decisions; vary these only through recorded regime configs.
5. Check individual service feasibility using physical depot travel and service
   time. Use private feasibility diagnostics to detect impossible workloads;
   do not feed those diagnostics or future schedules to any policy.
6. Test two consecutive complete tours, in-transit replanning, depot return
   continuity, boundary releases, closure handling, end-of-shift cost, independent
   episode databases, and exact seed/salt/action-sequence replay.

**Exit / M1:** full deterministic multi-tour episode; backend and movement tests
pass together; v1 replay/golden behavior is unchanged.

## Step 3 — Compact public task and baseline policies

**Subagent:** `v2_interface_baselines`. **Dependencies:** Step 0; integrate Steps 1–2.
**Owns:** v2 task/public-view/routing modules, v2 baseline policies, interface tests.

1. Expose exactly `get_dispatch_state`, `get_city_map`,
   `submit_dispatch_plan`, and `finish` with strict v2 argument schemas.
2. Return static topology through `get_city_map`. Return current observed edge
   closures/multipliers and a traffic revision through operational state. This
   makes `get_dispatch_state` the documented read for changed travel information.
   Include no future disruption start/end schedule or future order metadata.
3. Expose minute, release/deadline/grace/value/size/status, depot, capacities,
   cargo, tour suffixes, locks, edge remaining time/distance, service completion
   times, and scoring constants. Responses to submissions contain this same state
   without static topology or repeated all-pairs matrices.
4. Provide a public-data-only shortest-path helper. Time and distance must refer
   to the same selected path. Recompute from observed traffic; do not assume
   closures expire unless that information is explicitly public for everyone.
5. Implement `nearest_first`, `deadline_first`, and `greedy_insertion` with valid
   capacity-limited multi-tour plans. Implement meaningful `stop_early` (one valid
   assignment then finish) and exact `do_nothing`; adapt regret only if useful.
6. All routing policies read/cache the map through MCP and subsequently consume
   operational responses. Keep within the existing 40-call/900-second task budget
   unless evidence requires a documented v2-only adjustment before freeze.
7. Measure full-episode response characters and tool calls. Initial payload target:
   <=100,000 characters per standard 40–55-order episode; report actual numbers.
   Verify public data suffices to reconstruct valid routes and travel estimates.

**Exit:** all baselines complete valid public-tool episodes; observation/schema,
payload, and hidden-information tests pass. Coordinator integrates registration.

## Step 4 — Deterministic OR-Tools rolling-horizon reference

**Subagent:** `v2_solver`. **Dependencies:** Step 3 public contracts; M1 mechanics.
**Owns:** v2 `policies/{solver,reference}.py`, solver config/tests/decision traces.

Build and validate in this order:

1. Static capacity-constrained routing on tiny hand-checked instances.
2. Optional deliveries, time windows, linear grace-period value loss, and driving
   cost. Distinguish deadline (full value) from deadline+grace (zero value); do not
   model every deadline as an absolute hard cutoff. Permit rejecting negative
   incremental-utility orders. Integer scaling/rounding must be explicit.
3. Multiple reloads. First try a bounded depot-reload RoutingModel formulation
   with time/capacity dimensions and vehicle continuity. Prove that it cannot
   model sequential tours as independently starting vans.
4. If that model is unwieldy, generate deterministic capacity-feasible candidate
   depot tours and use single-worker CP-SAT to select, sequence, and schedule them.
   Enforce cross-tour order uniqueness, per-vehicle non-overlap, time-dependent
   value, depot turnaround, active suffixes, and shift limits. Document candidate
   limits and pruning; omitted candidates are a solver limitation.
5. Start vehicles from their public physical state. Include the remaining locked
   edge, service, loaded sequence, and required return before any new pickup.
   Cargo commitments remain even when their remaining expected value is zero.
6. Replan online using only released orders and currently observed traffic.
   Price routes against current estimates; never import the private world,
   denominator, future schedule, grader records, or exported salt.
7. Freeze pinned solver version, seed, sorted inputs, tie-breaking, integer units,
   candidate/search limits, and single-worker settings. Use deterministic work
   budgets (CP-SAT deterministic time or fixed search/solution counts supported
   by the chosen solver), not wall time as the normal stopping rule. A safety
   timeout marks a failed run; it must not silently select a variable incumbent.
8. Define deterministic valid fallback behavior for no incumbent or model failure,
   preserve commitments, and report fallback counts. Do not hide these as solves.
9. Run repeated and concurrent identical episodes to establish action, score, and
   trace determinism on the pinned local and Harbor runtime.

Trace each solve with round/minute, public-input hash, solver status/work units,
orders considered/selected, tours, estimated arrival times, realized-value
estimates, lateness loss, distance/cost, total objective, and fallback status.
Reasons for exclusion must come from feasibility checks or explicit marginal
comparisons. Use `not selected by optimization` when no stronger reason was
computed. Never infer a proof of infeasibility from an unselected variable.
Keep traces out of operational responses and reconcile their objective with
the emitted plan. Estimated future utility is distinct from realized grade.

**Exit / M2:** zero invalid actions in complete development episodes; repeated
deterministic actions; objective tests pass; solver improves paired raw utility
over simple baselines on development worlds. If it does not, diagnose modeling,
candidate quality, and workload before increasing difficulty.

## Step 5 — Economic grading and adversarial integrity

**Subagent:** `v2_integrity`. **Dependencies:** M1 and Step 3; solver for parity tests.
**Owns:** v2 scoring/grading adapter, exploit/replay/reward tests.

1. Reuse numerical economic scoring with explicit v2 configuration/hash. Build
   grades from private generated denominator and authenticated, persisted physical
   outcomes. Include partial-edge and return distance.
2. Reconcile simulator events, backend final cargo/status, delivery timestamps,
   distance, and independently replayed actions. No delivery may be credited twice.
3. Cover every exploit listed in the proposal: multiple pickups, later-tour
   capacity, duplicates across tours/vehicles, loaded reassignment, invented depot
   visits, canceled in-progress edges, fake completions, invalid replacements,
   and replayed completed orders. Add timestamp/value/location edits and attempts
   to use unreleased IDs. Verify rejection leaves time and history unchanged.
4. Verify no-op is exactly zero; stop-early and call exhaustion earn only executed
   commitments; finishing does not assign pending unreserved orders.
5. Test public-data/import isolation and sidecar endpoints. Simulator/grader
   capabilities, database, hidden schedules, and salts stay outside agent access.

**Exit / M4:** deterministic replay, adversarial, grading, isolation, no-op, and
stop-early tests pass. Preliminary integrity runs precede calibration; this gate
must pass before any frontier experiment is accepted.

## Step 6 — Regime matrix and candidate freeze

**Subagent:** `v2_evaluation`. **Dependencies:** Steps 3–4; preliminary Step 5 checks.
**Owns:** v2 `evaluate.py`, experiment manifests/metrics, calibration report.

1. Parameterize the existing evaluator pattern in a new v2 script with `--task`,
   `--regime`, `--split`, `--policies`, and version-specific outputs. Keep v1 script
   defaults and archived outputs intact; never overwrite completed measurements.
2. Predeclare disjoint v2 splits: development 3000–3019, calibration 3100–3119,
   held-out 4000–4049 (minimum 50), salted validation 5000–5019, and fresh Harbor
   seed groups. Use a v2 salt namespace; private salts stay out of public reports.
3. Evaluate a small frozen matrix on development/calibration only:

   | Regime | Fleet pressure | Demand | Question |
   | --- | --- | --- | --- |
   | A | Moderate | Mixed steady/bursts | Do mechanics and planning work? |
   | B | High | Same mixed family | Does limited capacity affect allocation? |
   | C | High | Stronger bursts | Does online replanning matter? |
   | D | High | Bursts plus urgent high values | Does economic opportunity cost matter? |

   Keep costs and reward definition fixed. Record all workload changes, including
   order sizes, service times, deadlines/grace, geography, and burst parameters.
4. Collect reward, raw utility, realized value, distance/cost, on-time counts and
   percentages (both delivered-order and generated-order denominators), unserved
   count/value, solver work/runtime, fallbacks, tool calls/characters, invalid
   actions, and simulator errors. Distinguish solver exclusions from ex-post
   unserved orders; do not claim they are all economically optimal exclusions.
5. Report paired solver-minus-baseline differences, wins/ties/losses, and confidence
   intervals. Keep errors visible rather than dropping their worlds.
6. Diagnose feasibility separately: constructed feasible fixtures, per-order
   reachability, capacity utilization, depot tours, and best legitimate achieved
   utility. A private offline diagnostic may help explain limits, but is never
   the reference or a source of policy inputs. Low scores alone prove nothing.
7. Select a candidate with meaningful solver success and a reproducible advantage
   over heuristics. Freeze workload, scoring, solver, policy prompts/tool contract,
   dependency lock, and source fingerprints. Run required lint/type/test checks;
   add v2 golden entries without touching v1 values.

**Exit / M3:** written selection rationale and locked candidate manifest. If no
regime is both solvable and discriminating, publish that finding and improve
the planning model or reduce infeasibility before proposing another matrix.

## Step 7 — Actual frontier pilot and evidence-based diagnosis

**Subagent:** `v2_frontier`. **Dependencies:** frozen candidate and M4.
**Owns:** pilot commands/results, route-level failure analysis.

1. Use the existing actual Harbor OpenCode configuration initially:
   `opencode`, `google/gemini-3.8-flash`, OpenCode `1.19.5`, web search disabled,
   and the documented environment-provided API key. Record availability and
   resolved versions; do not silently substitute a model.
2. Run a pilot on 3–5 calibration worlds with paired reference runs and at least
   three independent model trials per world. Use the same exported worlds for
   each comparison. Record trial failures and tool/runtime budget usage.
3. For losses, cite actual round, order, vehicle, tours, arrival/delivery times,
   and economic losses. Categories include capacity allocation, order value,
   depot returns, wasteful travel, commitment handling, and remaining-shift
   tradeoffs. Replay a legal alternative from the same public decision state
   where practical; distinguish causal evidence from descriptive differences.
4. If the agent matches the solver, inspect solver quality, instance scale, and
   legitimate agent strength. If all policies fail, reduce infeasibility. Any
   revision creates a new candidate manifest and requires another freeze; do
   not tune using held-out or salted outcomes.

**Exit / M5:** real paired pilot measurements and a decision to advance, revise,
or report no gap. Planning weakness is not inferred from aggregate score alone.

## Step 8 — Held-out proof, salted checks, and Harbor reproduction

**Subagent:** `v2_harbor_repro` with evaluation-owner handoff.
**Dependencies:** M5 go decision; final frozen candidate.
**Owns:** v2 `reproduce.sh`, Harbor collector/tests, held-out/salted/final reports.

1. Run all comparison policies on at least 50 previously unused held-out worlds.
   Run fresh paired frontier/reference trials on the declared evaluation worlds,
   not just the pilot; repeat model trials at least three times per selected
   world. Report the exact agent-world count and avoid calling a smaller agent
   sample 50-world frontier proof. Preferred design: all 50 paired worlds.
2. Compute paired reward and raw-utility differences. Average model repeats
   within each world before uncertainty estimation; resample worlds as clusters
   so repeated trials are not treated as independent worlds. Report win rates,
   interval method, sample sizes, variance across repeats, and all errors.
3. Run salted validation from its disjoint seed group with deterministic private
   salts and action replay. Keep sensitive manifests in private runtime artifacts;
   publish hashes, reproducible authorized commands, and aggregate evidence.
4. Export a fresh Harbor batch with fresh private salts. Reference, nop,
   stop-early, and frontier runs must use that identical preserved export.
   Match each oracle output with independently replayed salted reference actions,
   score, full economic breakdown, calls, and transaction digest where available.
5. Confirm isolated SQLite stores, normal MCP execution for reference, no hidden
   access, wheel/lock/model hashes, agent tool/runtime limits, and local/Harbor
   parity. Keep solver traces out of the agent container and tool responses.
6. Provide a new `spikes/dynamic-dispatch-v2/reproduce.sh` with explicit task,
   regime, output, split, export, and job-prefix options. Mirror the original
   setup/local/export/harbor/frontier modes, adding pilot and replay modes.
   Never overwrite an export to get new salts; use a fresh directory. Preserve
   `spikes/dynamic-dispatch/reproduce.sh` and its v1 path.
7. Write `findings.md` and `harbor.md` with exact commands, completed/uncompleted
   runs, quantitative paired results, budget/payload measurements, failures,
   confidence intervals, and route-level explanations. A positive gap outside
   0.15–0.30 is reported as measured; absence of a gap is a valid finding.

**Exit / M6:** positive repeatable unseen-world advantage with identified
decisions, or an explicit no-go research result. **Exit / M7:** actual oracle,
baseline, frontier, and salted parity evidence with reproduction commands.

## Scheduling with three subagent slots

| Wave | Slot 1 | Slot 2 | Slot 3 | Coordinator gate |
| --- | --- | --- | --- | --- |
| 0 | — | — | — | v1 freeze, contracts, packaging |
| 1 | Backend | Mechanics | Interface/baselines | review fixtures; M1 |
| 2 | Solver | Integrity | Evaluation tooling | integrate public contract |
| 3 | Solver fixes | Integrity/parity | Calibration | M2, M3, M4; freeze |
| 4 | Frontier pilot | Harbor tooling | Evaluation validation | M5 decision |
| 5 | Frontier held-out | Policy held-out/salted | Harbor parity | M6 and M7 |

Interface work may start against contract fixtures in wave 1; valid full episodes
wait for M1. Evaluation tooling can start before solver completion; candidate
selection cannot. Run integrity checks throughout, not only after measurements.

## Final acceptance checklist

- Both task versions register; v1 golden values and archived experiment files
  are unchanged; the original v1 Harbor path remains reproducible.
- Multi-tour execution has correct cargo, physical returns, immutable active
  commitments, atomic future replacement, and deterministic durable history.
- Reference and baselines act only through the same four public MCP tools.
- OR-Tools reference is deterministic on pinned runtimes, supports reloads and
  in-transit starts, and emits truthful variable-derived explanations.
- Economic reward is unchanged in form and scale; raw utility improvements
  account for physical value and distance; no-op is exactly zero.
- Capacity, replay, authentication, privacy, stopping, and exploit checks pass.
- Development, calibration, held-out, salted, and Harbor evidence are separately
  identified with immutable candidate and artifact fingerprints.
- Real frontier trials, paired raw economics, repeated-world statistics, and
  specific route decisions support any claimed planning gap.
- Missing credentials/runtime, unresolved solver weakness, or lack of a gap is
  reported directly. No submission claim is made before its gates are met.
