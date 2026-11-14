# Dispatch v2 implementation contract

This contract is the integration boundary for the implementation agents.

## Modules and versioning

Shared backend is `dispatch/backend_service` plus `dispatch/backend.py`.
Version-specific scenario and simulation: `dispatch/v2/world.py` (reuse v1
dataclasses/path helpers where practical). Task: `dispatch/v2/task.py`.
All public-only policy code lives under `dispatch/policies/v2/`, so the existing
policy-import isolation check covers it. Policies must not import world/task/grader.
Coordinator alone edits existing policy registration modules, package registration,
dependency files, and generic golden/task-list fixtures.

## Backend contract

`DispatchBackend.reset(vehicles, depot, *, version=1)` selects mechanics explicitly.
V1 reset payload/history must remain byte-for-byte compatible when version omitted.
V2 `routes` is `dict[str, list[list[str]]]`. V1 flat routes remain supported only
for a v1 episode. Backend rejects mixed/incorrect shapes atomically.

V2 vehicles expose existing id/capacity/node/edge/cargo/distance plus:

- `tours`: remaining tours; while loaded, first tour is remaining loaded sequence.
- `tour_active`: true after pickup until its last delivery.
- `returning`: true after that delivery until physically back at depot.
- `locked_route`: remaining loaded sequence (can be derived from first tour).

Omitted vehicles retain plans; `{}` waits; `[]` cancels future tours but never
loaded commitments. Empty inner lists are invalid. Loaded first-tour sequence is
immutable, cannot gain additional parcels, and must remain first in replacements.
Future tours can be replaced/reassigned atomically across all vehicles. Only
released, unfinished orders are assignable. Capacity is sum of parcel sizes
within each tour, never sum over all tours.

Pickup loads exactly the first tour at the stationary depot, requires no cargo,
no active tour, and no pending return. Delivery follows the loaded sequence.
Last delivery removes first tour, sets `tour_active=false`, `returning=true`.
Authenticated event type `depot_return` requires empty cargo, stationary depot,
and `returning=true`; it clears returning, allowing next pickup. Final returns
are mandatory and cost time/distance. Pending tours do not prevent that return.

Existing authenticated clock/move/pickup/deliver events remain. Movement continuity
is simulator-owned; public route replacement never changes an edge or a return.
Simulator publishes current edge before decision observations. No agent-controlled
movement, delivery, value, distance, timestamp or location fields are accepted.
Rejecting a plan changes neither clock nor state/history. Reset/plan request
models and backend adapter must support this contract with explicit versioning.

## Simulation and public observations

360 minutes, 30-minute successful submissions, 12 intervals. Loading and delivery
service duration are explicitly **zero minutes** initially; travel, capacity,
release and deadline pressure are the decision constraints. Document any later
calibration change and freeze it before evaluation. Event order is deterministic:
sorted vehicle IDs, arrival/delivery/depot-return/pickup transitions, then releases
at a tick; all releases at the new decision minute are visible before action.
No new edge begins after minute 360. Partial-edge distance is charged.

`get_city_map`: depot, static nodes and roads with a map version, no future data.
`get_dispatch_state` and submit responses: minute/horizon/decision interval,
released orders, vehicles/commitments, current `traffic` edge changes containing
only a/b/multiplier/closed (no disruption end times), `traffic_revision`, scoring
and service constants. No topology or matrices repeated in state. Active traffic
must be enough to reconstruct current graph from the cached static map. Full
current traffic is returned each time, not a delta requiring hidden history.

V2 simulation API: `create(seed, salt=None, backend=None, *, regime="A")`,
`state()`, `city_map()`, `submit_plan(routes)`, `finish()`, `close()`; expose
`world`, `minute`, `_snapshot`, `backend` for private tests/grading. A regime
config is immutable. Task uses a single frozen default regime (initially A)
and task difficulty may select development regime through documented mapping.
Generated world retains v1-compatible `orders/roads/disruptions/capacities` and
`total_value`, but uses separate v2 salted random streams. Policies cannot see
future schedule, salt, total generated value, or regime-specific future events.

Four tool names remain unchanged. Policies first read static map then current
state, cache map, and consume submit state thereafter. `finish` executes all
currently reserved/loaded tours and final returns until shift end; no new
assignment. Grading after stopping replays only successful actions then finishes.

## Scoring and solver

Reuse v1 numerical economic formula and 0.35/km, with explicit v2 scoring config.
Reference and heuristics consume only observations. All v2 public models/path
helpers are in `dispatch/policies/v2/`. Solver is OR-Tools, optional orders,
per-tour capacity, return continuity, loaded prefix, published linear lateness,
shift boundary, deterministic budget and single worker. Explanations are local
diagnostics derived from selected variables, never extra public observations.
