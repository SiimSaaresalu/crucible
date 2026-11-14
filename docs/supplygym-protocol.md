# Fixed SupplyGym protocol

The first frozen protocol loses to `(s,S)` on every dev and held-out seed.
It is executable through the same tools and call budget as an LLM, but these
results do not support replacing the default baseline or reward anchor.

The branch starts at `supplygym-task` commit `1e830d4`. The decision rules and
constants were committed in `9e25286` before evaluation. Subsequent edits fix
registration typing, add tests and record results. No constants were adjusted
after evaluation. The default, costs, disruption rates, PPO weights, grading
formula and existing golden scores retain their original definitions.

## Executable decision procedure

Call `get_briefing` and `get_status` once. Then call `set_controls` once per week,
using the returned status for the next decision. A complete year uses 55 calls
out of the shared 120-call allowance. Both `supplygym@1` and `supplygym-fresh@1`
register the policy as `protocol`.

1. Treat a route as disrupted when it has any overdue orders or its public fill
   ratio falls below 0.8. If another active, healthy route can deliver before
   year end, reduce the disrupted route to the minimum allowed weight. Healthy
   routes keep weight 1 and absorb the share through the simulator's capacity
   normalization. When no usable route remains, approve the next backup only
   if its onboarding plus lead time fits before year end.
2. For a route with any unresolved regional rumour or risk at least 0.5, target
   the public mean daily demand times its lead days plus two decision intervals.
3. Otherwise use the mean daily demand observed over the last four weeks, or
   the briefing's mean before history exists, times lead days plus one interval.
   Demand is recovered from sales revenue divided by sale price plus lost-sales
   penalty divided by penalty per unit. Each week's total is divided by its
   observed elapsed days. No future demand or incident tape is read.
4. When days remaining are fewer than a route's lead days, set its target to
   zero and its weight to the schema minimum. This overrides its risk buffer.

The simulator represents one product and one buyer inventory. Route targets are
aggregated with capacity times adjusted supplier weight. Desired net orders are
`max(0, target - inventory_position)`, which accounts for stock and observed transit.
Extra safety days are `(target - base_stock_level) / nominal_daily_demand`, clipped
to the tool schema. The order multiplier is desired net orders divided by
`max(nominal_daily_demand, base_stock_level + safety_days * nominal_daily_demand
- inventory_position)`, also clipped to the schema. The one-day denominator floor
avoids division by zero and makes the approximation explicit. Expediting is zero
because the requested protocol has no expedite rule.

The constants are one month of demand history, one ordinary review interval,
two risk review intervals, the midpoint risk threshold, and a 20% missing-fill
threshold. Each has a one-line rationale in the policy. Remaining numbers come
from the briefing and schema. The policy ignores the ledger's counterfactual
profit signal and uses only realised sales and unmet demand from that ledger.

## Limits imposed by the existing tools

An agent observes delayed fulfilment evidence and noisy risk, so the disruption
test is an inference. It cannot identify actual hidden incidents with certainty.
Both low- and medium-credibility unresolved rumours trigger caution.

The tool caps safety stock at three days, requires positive supplier weights,
and constrains the order multiplier to approximately 0.607–1.649. It provides
no direct quantity, order-up-to, or zero-order action. Daily automatic `(s,S)`
ordering still executes under these controls. Consequently the implementation
approximates the requested one/two-week buffers and wind-down. Exact zero orders,
complete route exclusion, and per-item/location control require an API change.
All agents would need that same change for a fair comparison.

## Paired results

One run per policy per seed, unsalted `supplygym@1`, complete dev 1000–1029 and
held-out 10000–10049 banks. Cost is purchase + holding + stockout penalties +
expedite + switching. Profit also includes realised sales and terminal stock
credit. A profit win requires strictly greater annual profit on the paired seed.

| Bank | Seeds | Protocol mean cost | sS mean cost | Cost difference, protocol minus sS | Lower-cost wins | Profit difference, protocol minus sS | Profit wins |
|---|---:|---:|---:|---:|---:|---:|---:|
| Dev | 30 | $404,215.29 | $388,707.08 | +$15,508.20 | 1/30 (3.33%) | −$35,419.70 | 0/30 (0%) |
| Held-out | 50 | $405,157.51 | $387,380.20 | +$17,777.31 | 1/50 (2%) | −$42,644.61 | 0/50 (0%) |

PR #25's unchanged gap-proof script at `13f324b36d2bb9561920687b9638c691c68472b9`
was run on the saved profit CSVs and on negative cost, so higher values win.
Its paired 95% bootstrap intervals use 10,000 resamples and RNG seed 21.

| Bank | Profit-difference 95% CI | Cost-difference 95% CI |
|---|---:|---:|
| Dev | [−$40,053.19, −$30,839.04] | [+$12,630.38, +$18,376.96] |
| Held-out | [−$46,913.99, −$38,700.07] | [+$15,461.03, +$20,053.16] |

The protocol loses profit on all seeds. Its worst dev loss is $64,289.96 on 1016,
and worst held-out loss is $86,373.34 on 10028. Even its smallest losses are
$14,505.67 on 1020 and $17,428.34 on 10029.

Mean holding costs rise from $13,412.09 to $25,341.67 on dev and from $12,325.82
to $24,434.55 held-out. Stockout penalties also rise from $10,159.67 to $20,185.33
and from $13,227.80 to $26,614.00 respectively. Approval costs average $2,733.33
and $3,000. This combination is consistent with poorly timed inventory and route
changes. It does not establish the causal contribution of each rule, since no
ablation or parameter search was performed.

Per-seed results, including each cost component and call counts, are included in
[protocol-dev.csv](supplygym-protocol/protocol-dev.csv) and
[protocol-heldout.csv](supplygym-protocol/protocol-heldout.csv).

## Integrity and verification

The existing integrity gate passes with its three previously documented expected
failures. These include maximum-safety-stock strategies that already exploit the
default's calibration. The protocol is also run through the gate's first six dev
seeds in both regimes and checked against its exact mean reward and profit alarms.
It triggers neither alarm. Its negative results provide no evidence of a reward
exploit, while the gate is a finite diagnostic rather than a general certificate.

Tests check repeated identical tool transcripts, valid calls, completion within
budget, reset of history, four-week demand reconstruction, response to route
disruption, rumours and wind-down, and invariance to extra hidden/score fields in
serialized observations. The existing AST import-isolation tests cover the new
policy. It has no simulator or grader imports and no random, training, or search
path. Existing golden-score checks pass.

## Reproduction

From the repository root, run a single policy comparison through the normal CLI.

```bash
uv run --directory engine crucible eval --task supplygym@1 --policy protocol,ss --seeds 1000-1029 --out runs/protocol-cli
```

From `engine`, reproduce the full paired evaluation and gap proof.

```bash
uv run python scripts/evaluate_protocol.py
git fetch https://github.com/vaartnou/crucible.git refs/pull/25/head
git show 13f324b36d2bb9561920687b9638c691c68472b9:engine/src/crucible/gap_proof.py > runs/protocol/gap_proof.py
uv run python runs/protocol/gap_proof.py --runs runs/protocol/dev-profit.csv --task supplygym@1 --agents protocol,ss --seeds 1000-1029
uv run python runs/protocol/gap_proof.py --runs runs/protocol/heldout-profit.csv --task supplygym@1 --agents protocol,ss --seeds 10000-10049
uv run python runs/protocol/gap_proof.py --runs runs/protocol/dev-cost.csv --task supplygym@1 --agents protocol,ss --seeds 1000-1029
uv run python runs/protocol/gap_proof.py --runs runs/protocol/heldout-cost.csv --task supplygym@1 --agents protocol,ss --seeds 10000-10049
uv run pytest src/crucible/envs/supplygym/tests/test_integrity.py src/crucible/envs/supplygym/tests/test_protocol.py tests/policies
```

The evaluator resumes existing completed episodes. Use a fresh `runs/protocol`
directory for an independent rerun. A tool-using protocol can serve as an
additional interpretable comparator alongside `(s,S)` and PPO. This specific
frozen implementation is economically weaker than `(s,S)` on both tested banks.
