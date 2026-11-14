# Crucible Spike: Dynamic Delivery Dispatch

**Environment:** `dispatch@1`
**Type:** Real-backend, stateful, online decision-making benchmark
**Status:** Proposed experiment
**Goal:** Demonstrate that a reproducible heuristic can outperform a frontier AI agent at dynamic vehicle dispatch.

## 1. Problem definition

The agent operates a last-mile delivery company during a simulated six-hour shift.

It controls four delivery vans responsible for fulfilling customer orders across a small city. Orders arrive throughout the shift, vehicles have limited carrying capacity, and customers expect delivery before specified deadlines.

The agent must continually decide:

- Which orders to assign to each vehicle.
- In what sequence to deliver them.
- Whether to prioritize urgent orders over geographically convenient ones.
- Whether to preserve available vehicle capacity for future orders.
- How to adapt existing routes when traffic conditions change.

**Success means completing valuable deliveries on time while minimizing driving costs and missed deadlines.**

The benchmark should expose a genuine online planning problem: decisions that look locally optimal can reduce the number of deliveries completed later.

## 2. Initial environment

| Parameter | Initial configuration |
|---|---|
| Simulated city | Synthetic road network, approximately 8 × 8 km |
| Vehicles | 4 |
| Total orders | 28–36 |
| Starting orders | 8–12 |
| Shift length | 6 simulated hours |
| Decision frequency | Every 30 simulated minutes |
| Decision rounds | 12 |
| Vehicle capacity | 6–10 parcels |
| Delivery deadlines | Mix of normal and urgent |
| Depot | One central depot |
| Disruptions | Traffic slowdowns and occasional road closures |

All orders originate from the depot. A vehicle must physically visit the depot to collect parcels before delivering them.

Each order has a destination, parcel size, delivery deadline, and economic value.

The city topology is fixed and known. Order arrivals and disruptions vary by seed.

### Important simplifications

For the initial version:

- No real-world map APIs.
- No driver employment regulations.
- No multiple depots.
- No customer cancellations.
- No vehicle breakdowns initially.
- No arbitrary agent shell access to backend internals.

These can be introduced later if the core benchmark works.

## 3. Backend architecture

Use a real FastAPI application backed by SQLite.

The backend owns:

- Customer orders and their lifecycle.
- Vehicle manifests and assignments.
- Vehicle cargo.
- Delivery completion records.
- Persistent transaction history.
- Validation of dispatch operations.

The Python world simulator owns:

- The city road network and travel-time model.
- External customer order arrivals.
- Traffic conditions and disruptions.
- Deterministic vehicle movement.
- The virtual clock's external events.

Crucible orchestrates both and exposes MCP tools.

**Interaction loop**

1. Crucible initializes a seeded world and resets the backend database.
2. The simulator creates initial customer orders through the backend API.
3. The agent retrieves the current dispatch state using MCP.
4. The agent submits vehicle routes through Crucible.
5. The backend validates and commits those assignments.
6. Crucible advances simulated time by 30 minutes.
7. The simulator moves vehicles along their committed routes and sends verified pickup/delivery updates to the backend.
8. New customer orders and traffic events are generated.
9. The agent receives updated information and makes another decision.

The backend remains authoritative for order statuses and cargo. The simulator remains authoritative for movement and external events.

The backend cannot independently decide that a customer order was delivered; it requires a verified simulator-generated delivery event.

## 4. Agent action and observation space

Keep the MCP interface small.

### Tools

| Tool | Purpose |
|---|---|
| `get_dispatch_state()` | Current orders, vehicles, cargo, routes, deadlines, costs, and traffic estimates |
| `get_city_map()` | Static road network and location information |
| `submit_dispatch_plan(routes)` | Replace permitted route suffixes and advance one decision interval |
| `finish()` | Stop making decisions; existing committed routes continue, but no new assignments are made |

A dispatch plan contains an ordered list of delivery IDs for each affected vehicle.

The backend automatically enforces depot pickup requirements. It never assumes a vehicle has a parcel it has not collected.

Agents may change unvisited route stops but cannot teleport vehicles, undo completed deliveries, or modify a route segment already in progress.

The `submit_dispatch_plan` response includes the updated state after the 30-minute interval. This avoids requiring a separate read and clock-advance call every round.

### Observation rules

The agent sees:

- All currently released orders.
- Vehicle locations, routes, capacity, and cargo.
- Delivery values and deadlines.
- Public roads and estimated travel times.
- Current traffic warnings.
- Historical delivery performance.

The agent cannot see future orders or exact future disruptions.

The reference algorithm receives precisely the same information.

**Target:** Approximately 12 action calls per full episode, plus optional reads, comfortably below a 40-call budget.

## 5. Reward function

The reward should measure completed delivery value, adjusted for lateness and operating costs.

For each customer order, define:

- `value`: Economic contribution from a successful on-time delivery.
- `deadline`: Latest on-time delivery time.
- `lateness`: Minutes delivered after the deadline.
- `grace`: Maximum lateness receiving partial credit.

For a delivered order, define a punctuality factor:

- On time: 1.0
- Late: declines linearly from 1.0 to 0.0 over its grace period
- Beyond grace: 0.0
- Undelivered: 0.0

Then compute:

`utility = sum(order_value × punctuality_factor) − driving_cost − other_operating_penalties`

Normalize:

`reward = clip(utility / sum(all_generated_order_values), 0, 1)`

The denominator includes all orders generated for the episode, including those not delivered. It is private and independent of the agent's actions.

### Why this reward?

It encourages the intended task directly:

- Completing more valuable deliveries improves the score.
- Delivering earlier than the deadline protects the full value.
- Driving unnecessary kilometers reduces the score.
- Ignoring orders results in zero contribution from those orders.
- A successful route generally scores better than an inefficient route fulfilling the same orders.

The scale has a natural interpretation: the fraction of total potential delivery value realized after operating costs.

The reference doesn't define the reward ceiling.

### Anti-exploit properties

The following must be enforced:

- Only simulator-verified deliveries count.
- Agent-assigned timestamps are not accepted as completion evidence.
- Orders cannot be fabricated or assigned inflated values.
- Deadlines are immutable.
- A parcel cannot be delivered before physical pickup.
- No vehicle may exceed its capacity.
- Travel time and driving costs cannot be set by the agent.
- Future unreleased orders cannot be assigned.
- Unfinished deliveries receive no completion reward.

`nop` must score exactly 0.

Stopping early may earn partial credit from already committed routes, but it should score substantially below successful full-episode play.

Report raw deliveries, on-time percentage, driving distance, late deliveries, total utility, and normalized reward in the score breakdown.

## 6. Explainable reference algorithm

**Reference name:** `regret_insertion`

Implement a deterministic online vehicle-routing heuristic. No neural network or trained model is required.

### Step 1 — Read the current state

Obtain released orders, vehicle locations, committed routes, remaining capacities, and estimated travel times through the same MCP tools available to the LLM.

### Step 2 — Find feasible insertions

For every unassigned order, evaluate inserting it at permissible positions in each vehicle's route.

Reject insertions violating physical constraints. Estimate the economic outcome of each remaining candidate using:

- Additional driving distance.
- Predicted delivery lateness.
- Order value.
- Vehicle capacity.
- Impact on existing deliveries.

### Step 3 — Prioritize difficult orders

Calculate the difference between each order's best and second-best feasible insertion.

This is its *regret*.

An order with one excellent route and no good alternatives has high regret. Schedule these constrained opportunities before accepting easy orders that fit almost anywhere.

Combine regret with estimated delivery value so low-value orders do not automatically dominate the plan.

### Step 4 — Improve routes

Apply local improvements, such as swapping compatible stops or 2-opt-style reordering, while respecting locked route segments, depot pickups, capacities, and deadlines.

Only accept changes that improve the estimated economic objective.

### Step 5 — Execute and repeat

Submit the resulting dispatch plan through MCP.

At the next decision round, rebuild the plan from the latest observable information.

Do not inspect hidden future arrivals or simulator disruption schedules.

### Additional policies

| Policy | Description |
|---|---|
| `do_nothing` | No routes submitted |
| `stop_early` | Make one initial dispatch, then stop |
| `nearest_first` | Prefer geographically nearby orders |
| `deadline_first` | Prioritize earliest deadlines |
| `greedy_insertion` | Choose greatest immediate expected gain |
| `regret_insertion` | Main explainable reference |

The progression helps identify whether a performance gap is due to planning, deadline management, or poor geographic allocation.

OR-Tools can later provide a separate solver-based comparison. It is not required for the initial reference.

## 7. Implementation structure

Create the environment using Crucible's existing environment generator.

Proposed layout:

    engine/src/crucible/envs/dispatch/
      __init__.py
      task.py
      world.py
      backend.py
      grader.py
      scoring.yaml

      backend_service/
        app.py
        models.py
        database.py
        routes.py

      policies/
        reference.py
        greedy.py
        deadline.py
        baselines.py

      tests/
        test_world.py
        test_backend.py
        test_replay.py
        test_reward.py
        test_exploits.py

      README.md

    spikes/dynamic-dispatch/
      README.md
      experiments/
      notes.md

Keep the backend service runnable independently of Crucible through HTTP, while keeping the world simulator and grader private.

## 8. Development milestones

### Milestone A — Real backend proof of concept

- Start FastAPI with an isolated SQLite database.
- Create customer orders.
- Assign routes to vehicles.
- Enforce pickup, capacity, and order-state rules.
- Retrieve orders and vehicles through HTTP.

**Acceptance:** The backend stores and validates real dispatch operations, independent of the simulator.

### Milestone B — Deterministic world

- Build the synthetic city and travel-time model.
- Implement virtual time.
- Generate seeded order arrivals and disruptions.
- Advance vehicle movement and complete deliveries.
- Reset and replay an episode.

**Acceptance:** Identical seeds, salts, and actions reproduce the same committed events, trajectories, and scores.

### Milestone C — Crucible integration

- Register `dispatch@1`.
- Implement MCP tools using the existing typed task contract.
- Connect the runtime to the backend service.
- Enforce the tool-call budget.
- Ensure observations cannot reveal hidden future events.

**Acceptance:** An agent can complete an entire episode through MCP without direct backend access.

### Milestone D — Reference and scoring

- Implement economic grading.
- Implement nearest-first and deadline-first baselines.
- Implement regret-based insertion.
- Run paired evaluations on at least 50 held-out worlds.
- Inspect where the reference improves over simpler policies.

**Acceptance:** A reproducible reference policy performs meaningfully better than simple baselines in raw economic utility.

If it doesn't, inspect the scenario design rather than modifying reward normalization to manufacture a gap.

### Milestone E — Exploit and Harbor checks

Test:

- No-op and stop-early.
- All orders assigned to one vehicle.
- Repeated assignment of the same order.
- Invalid vehicle and order identifiers.
- Capacity violations.
- Route changes during an ongoing delivery.
- Artificially long routes.
- Attempts to report fake completion.
- Access to future orders or grading data.

Package the backend service inside the Harbor sidecar, with its database isolated per task. Preserve the existing agent container and token-protected verifier.

**Acceptance:** Oracle/reference and agent runs work through Harbor. Salted-world reference performance and reward integrity have been checked, not just unsalted evaluations.

### Milestone F — Frontier agent evaluation

Run OpenCode against the completed environment.

Compare the reference, frontier agent, and baselines using paired seeds. Keep at least three independent frontier-agent runs, as required by AfterQuery.

Analyze:

- Average reward and variability.
- On-time deliveries.
- Missed deadlines.
- Driving inefficiency.
- Orders left unassigned.
- Whether errors arise from long-term planning or tool-interface problems.

Preserve actual Harbor logs and exact reproduction commands.

## 9. Experiment design

Use disjoint seed groups:

| Split | Purpose |
|---|---|
| Development | Implement and tune simulator mechanics |
| Calibration | Tune heuristic parameters and scenario difficulty |
| Held-out | Compare policies without further tuning |
| Harbor salted | Validate the final exported evaluation |

Vary road topology, order distributions, deadlines, and traffic disruptions across seeds.

Use a fixed, documented distribution of scenarios. Don't selectively report worlds where the reference performs well.

If the reference does not consistently outperform the frontier agent, inspect the action traces. Possible adjustments include more realistic order bursts, shared vehicle capacity bottlenecks, or tighter but feasible deadlines.

Do not add arbitrary information restrictions or impossible tasks merely to lower agent scores.

## 10. Decision criteria

The spike is successful if:

- A genuinely stateful HTTP backend is controlled through Crucible.
- End-to-end runs are deterministic and independently reproducible.
- A scripted reference consistently beats simpler dispatch heuristics.
- The reward tracks genuine delivery outcomes and resists basic exploits.
- A full episode fits comfortably within the harness's operational limits.
- The system can be exported and rerun in Harbor.

A reproducible gap over a frontier agent would establish its value as an AfterQuery submission candidate.

If the LLM matches or exceeds the reference, the spike can still validate the real-backend architecture, but it has not yet demonstrated the competitive gap required by the challenge.

## 11. Explicitly out of scope

Until the above is demonstrated, do not implement:

- Full Fleetbase integration.
- Live GPS, Google Maps, or external routing APIs.
- Multiple warehouses or depots.
- Driver shifts and employment rules.
- Real-time map rendering.
- RL policy training.
- General-purpose Kubernetes orchestration.
- A reusable real-backend plugin system for all Crucible environments.

The priority is proving the idea with one small, complete environment rather than building a general logistics platform.

## 12. Final spike deliverables

1. Runnable `dispatch@1` environment.
2. Real HTTP dispatch service with persistent state.
3. Seeded city simulator with deterministic replay.
4. Economic reward and private verification ledger.
5. Explainable reference policy and simpler baselines.
6. Reproducible evaluation results.
7. Exploit-test results.
8. Harbor export and a documented one-command reproduction path.
9. Short findings report explaining whether agents fail at the intended planning decisions.

**Core research question:** Can a frontier AI agent repeatedly make economically good dispatch decisions in a changing environment, or does an explainable online routing heuristic outperform it by planning vehicle utilization and future commitments more effectively?
