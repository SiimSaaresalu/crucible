# dispatch@2

Dispatch three vans on a seeded road grid during a 360-minute shift. The frozen
candidate uses regime D: 50–55 orders, capacities 6–8, parcel sizes 2–3,
clustered bursts, competing deadlines, and some later urgent orders worth
$180–300. Future arrivals and disruptions are private and action independent.
Regimes A–C remain available for development comparisons.

The four public tools are:

- `get_city_map()`: static nodes, roads, depot; read once and cache.
- `get_dispatch_state()`: released orders, vehicles, cargo, remaining tours,
  immutable commitments, current edges, and complete currently observed traffic.
  Re-read this tool for current travel changes; reads do not advance time.
- `submit_dispatch_plan(routes)`: replace permitted future tours atomically,
  advance 30 minutes, and return operational state without repeating topology.
- `finish()`: continue reserved tours and returns to minute 360 without making
  new assignments. Early stopping and call exhaustion have the same continuation.

```json
{"routes":{"v0":[["o001","o004"],["o012"]],"v1":[["o002"]]}}
```

Every inner list is one depot load followed by its delivery sequence and a
physical depot return. Capacity applies separately to each load. Loading and
delivery service times are zero. The remaining first tour of an active load is
immutable; retain it exactly when replacing that vehicle's plan. Future tours
may be changed. An omitted vehicle retains its plan; an empty routes dictionary
waits. Empty inner tours, duplicate orders, uncollected deliveries, overloaded
loads, unreleased IDs, and edits to loaded commitments are rejected atomically.
An edge already in progress and a depot return cannot be canceled.

There are 12 decision intervals and 40 total tool calls, including reads and
invalid attempts. Vehicles never teleport; deliveries require depot pickup and
physical arrival. Mandatory return travel, including a partial edge at shift end,
costs distance. A load is not started at minute 360.

Reward is the unchanged economic formula: delivered value declines linearly
from full value at the deadline to zero at deadline plus grace; subtract 0.35
per physically driven kilometre; divide by all generated order value and clip
to [0,1]. Unserved orders earn zero. Neither policy estimates nor a reference
score determine the reward. No-op has exactly zero utility and reward.

`reference` and `rolling_horizon_solver` use OR-Tools CP-SAT with deterministic
single-worker work limits. Candidate tours are globally selected and scheduled
with optional orders, per-load capacity, vehicle continuity and lateness losses.
The bounded candidate set and current-traffic assumption limit optimality.
Unpicked optional tours conservatively require a final depot return by minute
360, although physical execution can truncate a return at the boundary. The
reference never observes future orders, disruption schedules, or grading data.
Structured decision traces reconcile selected variables with objective terms.

`nearest_first`, `deadline_first`, `greedy_insertion`, `regret_insertion`,
`stop_early`, and `do_nothing` provide comparisons. Version adapters preserve
dispatch@1's original reference and behavior. New policy and experimental
results live under `spikes/dynamic-dispatch-v2/`; the original experiment folder
and reproduction script remain unchanged.

See the v2 implementation plan, contracts, candidate manifest, calibration and
Harbor reports for exact configurations, commands, evidence and research limits.
