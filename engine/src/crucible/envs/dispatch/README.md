# `dispatch@1`: dynamic delivery dispatch

Operate four delivery vans for a six-hour shift. New customer orders and traffic events arrive during play. Choose assignments and stop sequences to collect parcels at the central depot and deliver valuable orders before their deadlines.

| Parameter | Configuration |
|---|---|
| City | 5×5 road grid spanning 8×8 km, varying shortcuts and road speeds |
| Vans | 4, each capacity 6–10 parcel units |
| Orders | 28–36 total, 8–12 initially visible |
| Clock | 360 minutes; 12 decision intervals of 30 minutes |
| Tool budget | 40 calls, including reads and rejected calls |
| Backend | FastAPI, isolated on-disk SQLite state and transaction ledger |

## Tools

- `get_dispatch_state()` reads released orders, vehicle locations, routes, cargo, capacities, current traffic, and travel estimates without advancing time.
- `get_city_map()` reads public roads and current estimates without advancing time.
- `submit_dispatch_plan(routes)` replaces routes for specified vehicles, advances 30 minutes, and returns updated state. Omitted vans keep their routes; `{}` waits.
- `finish()` stops making new assignments and lets existing routes run to minute 360.

The edge currently being driven stays committed. Later stops can change, but loaded parcels must remain assigned to the same van. A route's total parcel size must fit capacity. If any assigned parcel is missing from the van's cargo, the simulator first takes the van to the depot and collects it. The agent cannot report completion, alter values/deadlines, or see future orders and traffic.

Stopping a policy or exhausting its budget has the same committed-route continuation as `finish`. Grading projects that continuation in an isolated deterministic replay and does not change the live episode.

## Reward

Each delivered order earns its value on time, then declines linearly to zero over its grace period. Undelivered orders earn zero. Driving costs 0.35 per physically driven kilometre, including partial edges.

```text
utility = sum(value × punctuality_factor) − 0.35 × distance_km
reward = clip(utility / total_generated_order_value, 0, 1)
```

All generated orders, including unreleased or unfinished orders, contribute to the private denominator. Only simulator-authenticated backend delivery records count. `do_nothing` scores exactly zero. The breakdown includes deliveries, on-time share, late deliveries, distance, realized value, potential value, utility, unfinished orders, and reward.

## Policies and validation

`reference` / `regret_insertion` evaluates marginal economic gain, schedules high-regret opportunities, and improves routes with accepted stop swaps/reversals. `greedy_insertion`, `nearest_first`, and `deadline_first` provide simpler comparisons. `stop_early` dispatches once and finishes; `do_nothing` submits no routes. Normal full-shift scripted policies use 13 calls.

Policies import only the policy package and public Crucible contracts. They use current observations, including public travel matrices, without inspecting hidden future arrivals or disruptions. The same seed, salt, and accepted actions reproduce backend events, trajectories, and scores.

Tests cover backend persistence and atomicity, auth, pickup and capacity constraints, locked-edge rerouting, replay, reward arithmetic, budget enforcement, hidden-state isolation, and exploit attempts. Evaluation protocol, measured results, and limitations are in [the spike findings](../../../../../spikes/dynamic-dispatch/findings.md); actual Harbor execution is documented in [the Harbor report](../../../../../spikes/dynamic-dispatch/harbor.md).

The 50-world held-out comparison measured mean reward 0.9356 for the reference,
0.9347 for greedy insertion, 0.8667 for deadline-first, and 0.8638 for nearest-first.
The regret-versus-greedy paired interval includes zero. In three actual paired
salted Harbor worlds, OpenCode + `google/gemini-3.8-flash` scored 0.9451 on average
versus 0.9193 for the reference and won all three pairs. This version validates the
architecture but does not demonstrate the proposed reference-over-model gap.

## Run

From the repository root:

```bash
uv run --directory engine crucible eval --task dispatch@1 \
  --policy reference,nearest_first,deadline_first,greedy_insertion,stop_early,do_nothing --seeds 0-9
uv run --directory engine pytest src/crucible/envs/dispatch
uv run --directory engine python ../spikes/dynamic-dispatch/evaluate.py --split heldout
```

Run the backend independently (supply a private token and a persistent database path):

```bash
DISPATCH_DATABASE=/tmp/dispatch.sqlite3 DISPATCH_SIMULATOR_TOKEN=your-private-token \
  uv run --directory engine uvicorn crucible.envs.dispatch.backend_service.app:app --host 127.0.0.1 --port 8001
```

The local adapter uses HTTP over ASGI transport against this application; it can instead connect to an independently running HTTP service. Harbor packages the backend inside the existing simulator sidecar without exposing its control endpoints or credentials to the agent.
