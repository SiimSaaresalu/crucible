# Scoring

A good reward is 0 for doing nothing, 1 for the best possible play, and hard to game in between. These patterns get there. `incident@1` is the example throughout. Its code is `engine/src/crucible/envs/incident/grader.py` and its weights are in `scoring.yaml`.

## 1. The do-nothing floor

Measure the agent against what happens with no action. Subtract that, so doing nothing scores exactly 0.

In `incident@1` the simulator counts requests served per minute. The grader replays the same world with no interventions:

```text
recovered = served_agent - served_nothing
```

Because the simulator is pure functions over `(world, interventions, minute)`, the replay uses the same code as the agent's run. Do-nothing is exactly 0, with no rounding.

## 2. The ceiling: oracle or anchor

Divide by the gain of the best possible play, so that play scores exactly 1.

In `incident@1` the oracle applies the right repair at `fault_start`:

```text
oracle_gain    = served_oracle - served_nothing
recovered_frac = recovered / oracle_gain
```

Even a perfect repair loses some requests while it runs. Dividing by `served_nothing` alone would cap the best score below 1, so we divide by the oracle's gain.

When no oracle exists, use a fixed anchor instead: a strong scripted solution, or a known bound.

The main part alone has a ceiling of `base_weight` = **0.70**: a correct, fast repair with no diagnosis bonus. The diagnosis bonus (below) lifts it to 1. The reference reaches 0.968 on seeds 0–99 because it repairs soon after the alert and diagnoses right.

Calibration finding: for a correct repair, `recovered_frac` is about the share of the fault window before the repair starts. Fault effects and repair times cancel out. So the reward mostly measures detection time and how many reads come before the repair.

## 3. Bonuses multiply

A secondary goal scales the main outcome. It never adds points on its own.

```text
raw = recovered_frac * (base_weight + diagnosis_weight * diagnosis) - penalty_frac
    = recovered_frac * (0.7 + 0.3 * diagnosis) - penalty_frac
```

`diagnosis` is 1 only when all four hold:

- the `resolve_incident` cause is right,
- the first repair was right,
- the agent read metrics or logs of the faulty service after the fault started and before the first repair,
- all probes are healthy at the end.

So a correct diagnosis with no repair earns nothing, and `stop_early` (resolve after two reads) scores 0.000 even when its guess is right.

## 4. Penalties

Penalties are counted in lost requests and divided by the same `oracle_gain`:

```text
penalty = wrong_repairs          * 3000
        + healthy_damage_actions * 2000   # repairs before any fault
        + instance_minutes       * 15     # scaling cost
penalty_frac = penalty / oracle_gain
```

Each action is charged once. Wrong repairs also have real side effects in the simulator, such as downtime or an old bug coming back.

## 5. Clamping

```text
total = min(1, max(0, raw))
```

`shotgun` (tries every repair) has a raw value below 0 on 80 of seeds 0–99. After the clamp it scores 0.012 on average.

## 6. Raw parts in `grade.json`

The `Score` keeps every part, so you can see why a reward is what it is. From the reference on seed 2:

```json
{
  "total": 0.977,
  "breakdown": {
    "served_agent": 117539.15, "served_nothing": 72953.26, "served_oracle": 118588.60,
    "recovered": 44585.90, "oracle_gain": 45635.34,
    "wrong_repairs": 0.0, "healthy_damage_actions": 0.0, "instance_minutes": 0.0,
    "penalty_requests": 0.0, "recovered_frac": 0.977, "penalty_frac": 0.0,
    "diagnosis": 1.0, "raw": 0.977
  },
  "flags": {
    "resolved": true, "root_cause_correct": true, "first_repair_correct": true,
    "evidence_read": true, "probes_healthy": true, "budget_exhausted": false
  }
}
```

In Harbor the same data is in `verifier/grade.json`; `reward.txt` holds only `total`.

## Changing the scoring

- Weights live in `scoring.yaml`. The validated config is hashed into `scoring_hash`, and every transcript header records it.
- Golden scores per (task ref, policy, seed) are in `engine/tests/policies/golden_scores.json`. A score change without a version bump fails CI. Bump the task version (`incident@2`) for a real change.
