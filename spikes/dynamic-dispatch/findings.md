# Dispatch spike findings

Date: 2026-11-14. Task: `dispatch@1`.

The stateful backend and deterministic benchmark are implemented. Economic insertion performs better than nearest-first and deadline-first on the fixed held-out worlds. This experiment does **not** establish a meaningful advantage for regret insertion over greedy economic insertion. In three paired Harbor trials, the evaluated Gemini model beats the reference on every world. The proposed competitive gap is therefore **not demonstrated**.

## Held-out economic comparison

All policies were frozen before evaluating seeds 1000–1049. Every one of the 50 worlds ran every policy. All 300 episodes completed without environment errors. Source SHA-256: `086cde3ef9a1d6741e0048b3be0d876503e52c96950d90687c4ded7a7a01e1e5`.

| Policy | Mean reward | Mean utility | Deliveries | On-time share | Distance km | Unfinished |
|---|---:|---:|---:|---:|---:|---:|
| regret reference | 0.9356 | 1914.22 | 31.78 | 90.47% | 199.03 | 0.12 |
| greedy insertion | 0.9347 | 1912.11 | 31.76 | 90.53% | 199.44 | 0.14 |
| deadline first | 0.8667 | 1771.28 | 31.90 | 82.81% | 198.62 | 0.00 |
| nearest first | 0.8638 | 1766.41 | 31.90 | 81.62% | 186.73 | 0.00 |
| stop early | 0.2912 | 592.42 | 9.72 | 99.62% | 29.07 | 22.18 |
| do nothing | 0.0000 | 0.00 | 0.00 | 0.00% | 0.00 | 31.90 |

Values are means across episodes, including the per-episode on-time percentage. “Unfinished” includes every generated order that was not delivered. Stop-early's high punctuality applies only to its initial commitments; most orders remain unfinished.

Paired differences are reference minus baseline on the same 50 worlds. The intervals use `mean ± 1.96 × sample standard deviation / sqrt(n)`.

| Baseline | Reward difference | Approx. 95% interval | Utility difference | Reference wins / ties / losses |
|---|---:|---:|---:|---:|
| greedy insertion | +0.00090 | −0.00051 to +0.00231 | +2.11 | 8 / 39 / 3 |
| deadline first | +0.06880 | +0.05062 to +0.08699 | +142.94 | 47 / 0 / 3 |
| nearest first | +0.07173 | +0.05743 to +0.08603 | +147.80 | 49 / 0 / 1 |
| stop early | +0.64437 | +0.62728 to +0.66147 | +1321.80 | 50 / 0 / 0 |

The regret-vs-greedy interval includes zero, and their outcomes tie in 39 of 50 worlds. This supports the economic insertion objective and continual replanning; it does not isolate a regret-specific benefit. The current distribution is relatively forgiving: nearest-first and deadline-first eventually deliver all orders, but suffer more late deliveries. The reference trades a small number of low-value or difficult deliveries for better realized value.

## Salted validation

Seeds 2000–2009 used nonempty deterministic validation salts passed privately to the task. All 60 policy episodes completed without errors. This tests salted generation independently of the unsalted held-out split; actual Harbor worlds use separate private export salts.

| Policy | Mean reward | Mean utility |
|---|---:|---:|
| regret reference | 0.9554 | 1880.34 |
| greedy insertion | 0.9554 | 1880.34 |
| deadline first | 0.9207 | 1809.23 |
| nearest first | 0.8991 | 1764.12 |
| stop early | 0.3263 | 635.53 |
| do nothing | 0.0000 | 0.00 |

Regret and greedy tie on all ten salted worlds. The reference beats each geographic/deadline baseline on eight worlds and loses on two. Reward normalization was not changed to create a gap.

## Harbor and model comparison

Actual container trials, exact commands, export details, and raw log paths are recorded in [harbor.md](harbor.md). The model comparison uses the repository's documented OpenCode 1.19.5 and `google/gemini-3.8-flash` configuration. It must not be generalized to every frontier model. Three independent trials use the same three salted exported worlds as the Harbor oracle; unsalted results above are not substitutes for paired container results.

All three OpenCode + `google/gemini-3.8-flash` trials completed in a 9-minute-4-second Harbor job, without trial exceptions or rejected in-episode plans. Each used 14 charged episode calls and only the four dispatch MCP tools. After seed 1002 reached minute 360, the model made one redundant `finish` call, correctly refused as “Episode is over”; it is outside the episode transcript and does not change the score. Total observed MCP calls were 14, 14, and 15. The oracle/reference used 13 calls. Nop scored exactly zero in all three containers.

| Salted seed | Reference reward | Model reward | Model minus reference |
|---|---:|---:|---:|
| 1000 | 0.87063 | 0.91662 | +0.04599 |
| 1001 | 0.95948 | 0.96308 | +0.00359 |
| 1002 | 0.92782 | 0.95556 | +0.02774 |
| Mean | 0.91931 | 0.94508 | +0.02577 |

The model wins all three paired worlds. Sample reward standard deviations across these three worlds are 0.02494 for the model and 0.04503 for the reference; with one trial per world, this combines world variation and model variability. Mean raw utility is 1923.72 for the model versus 1869.37 for the reference. Mean delivered orders are 32.33 versus 31.67; mean on-time shares are 90.11% versus 87.55%. Mean distance is 210.32 km versus 212.60 km. The model leaves no orders unassigned or unfinished; the reference leaves two orders unassigned in seed 1000 and none in the other two worlds. Mean missed deadlines, counting late and unfinished orders, are 3.33 for the model versus 4.67 for the reference.

These three trials do not establish broad model superiority, and they directly contradict the intended reference-over-model gap on the sampled worlds. The exported salted reference was independently replayed in process and matched the Harbor oracle exactly, including every score component and its 13 calls.

### Observed planning outcomes

The model's in-episode plans were valid throughout. The single post-completion refusal was harmless, so the observed score difference cannot be attributed to rejected plans or a broken interface. Examples from the public delivery traces:

- In salted seed 1000, the reference leaves `o024` and `o028` unassigned. The model delivers `o024` at minute 220 (deadline 213), earning 36 of its value 54, and `o028` at minute 280 (deadline 287), earning its full value 35. It also delivers `o026` at minute 266 rather than 284, recovering about 34.55 in lateness-adjusted value.
- In seed 1001, the model delivers `o028` at minute 314 before its deadline 316; the reference delivers it at 320. All model deliveries in that world are on time. The model also drives about 1.66 km less.
- In seed 1002, the model delivers `o022` at minute 153 rather than 165 (deadline 155), and `o029` at 302 rather than 316 (deadline 301). It drives 184 km versus 204 km for the reference.

These are differences in allocation and stop sequencing under the intended delivery economics. The traces do not show the evaluated model failing the intended online-planning task. The reference's local insertion estimates and conservative commitments merit investigation on fresh development worlds rather than changing the score definition.

The interface is nevertheless verbose: repeated full city matrices produce roughly 337–344 thousand serialized tool-response characters per model episode, and substantial accumulated model input usage is recorded in the logs. The trials stayed within their 900-second limit and 40-call budget. Reducing repeated map payloads is a reasonable future interface experiment, but it was not changed during these measurements.

Sanitized per-trial metrics, variability, and tool counts are preserved in [Harbor results](experiments/harbor/results.csv) and [Harbor summary](experiments/harbor/summary.json). Rebuild those summaries from the retained raw jobs with:

```bash
engine/.venv/bin/python spikes/dynamic-dispatch/experiments/collect_harbor.py
```

The collector reads only grading records, public tool transcripts, and selected recorded usage fields. It does not copy provider-key configuration or secret export salts.

## Integrity and reproducibility

The backend persists authoritative order statuses, assignments, cargo, and simulator-authenticated completions in SQLite. Route changes commit atomically; uncollected orders can change vans, loaded parcels cannot. Invalid calls do not advance time or modify state. Agents cannot submit timestamps or completion events through MCP. Future arrivals and disruption schedules stay private. The reward denominator is the complete seeded order value and is independent of actions.

The world fixes all exogenous events from `RngStreams(seed, salt)` before decisions. Replay tests compare complete observations, trajectories, and backend event history. Grading of a stopped policy projects existing commitments to the shift boundary in an isolated replay, leaving live state unchanged. No-op yields exactly zero; stop-early yields substantially less than full-episode dispatch.

The evaluator saves compact [held-out results](experiments/heldout/results.csv), [held-out summary](experiments/heldout/summary.json), [salted results](experiments/salted/results.csv), and [salted summary](experiments/salted/summary.json). Complete action transcripts are retained locally under `engine/runs/dispatch-spike/`. The summary records the implementation fingerprint and errors; the evaluator refuses to accept results if implementation source changes during a run.

## Decision and next experiment

The spike validates a real persistent backend under the existing Crucible tool and export contracts. It demonstrates a reproducible improvement over simple geographic and deadline ordering. It has not demonstrated the stronger claim that regret planning consistently improves on greedy economics, nor should that claim be inferred from the reference name. The evaluated model outperforms the reference on all three paired Harbor worlds, so this version does not meet the proposed model-gap criterion for an AfterQuery submission candidate.

A subsequent version could investigate realistic burst arrivals, tighter feasible urgent deadlines, or shared capacity pressure on the development/calibration splits. Such changes should be a new documented experiment with a fresh held-out split and paired model trials. Do not tune the present held-out worlds or change reward normalization to improve the reported gap. Multi-load depot tours, map APIs, driver regulation, and fleet integrations remain outside this spike.
