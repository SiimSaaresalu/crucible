# Dynamic delivery dispatch spike

`dispatch@1` operates four vans over a six-hour shift through a persistent FastAPI/SQLite backend. Orders arrive online; parcels require physical collection at the depot. Vehicles retain an edge already being driven when routes change. The environment exposes four tools and a 40-call budget; scripted full-shift policies use 13 calls.

The held-out results support economic insertion over geographic/deadline ordering, but not a distinct regret advantage. The evaluated Gemini model beat the reference on all three paired Harbor worlds, so the intended competitive gap was not demonstrated.

The implementation plan is in [plan.md](plan.md), and the supplied research proposal is preserved in [proposal.md](proposal.md). Actual measurements and remaining research criteria are reported in [findings.md](findings.md). Harbor commands and logs are documented in [harbor.md](harbor.md).

## Reproduce

From the repository root:

```bash
uv sync --directory engine --locked
uv run --directory engine python ../spikes/dynamic-dispatch/evaluate.py --split heldout
uv run --directory engine python ../spikes/dynamic-dispatch/evaluate.py --split salted
```

An intentional rerun uses `--overwrite`. `--split development --limit 2 --out /tmp/dispatch-smoke` makes a development smoke run; omit `--limit` for reported comparisons. Full transcripts go to `engine/runs/dispatch-spike/<split>/` (gitignored). Compact per-episode economic measurements go to `experiments/<split>/results.csv`; `summary.json` contains aggregate and paired statistics, source fingerprints, and errors.

For the one-command local and Harbor path, see [reproduce.sh](reproduce.sh). Frontier/model trials require a configured provider key and an explicit frontier invocation; they incur provider usage.

## Fixed experiment protocol

| Split | Seeds | Purpose |
|---|---|---|
| Development | 0–19 | Mechanics and policy development |
| Calibration | 100–119 | Reserved scenario/policy calibration |
| Held-out | 1000–1049 | All 50 worlds, evaluated after policies froze |
| Salted validation | 2000–2009 | All 10 worlds with deterministic nonempty salts |
| Harbor salted | 1000–1002 with private export salts | Paired oracle, nop, model container trials |

The salted validation namespace is `dispatch-spike-v1-salted-validation`; its SHA-256-derived salts are passed privately to the task, never to policies. They exercise salted generation reproducibly. Harbor uses independently generated secret salts stored only in ignored export artifacts. A Harbor salted world differs from the unsalted held-out world with the same seed; no unsalted result is substituted for its oracle result.

Every policy receives the same public state through `EpisodeSession` and the task's typed tool contract. No policy imports backend, simulator, or grader code. Policies are `reference` (alias `regret_insertion`), `nearest_first`, `deadline_first`, `greedy_insertion`, `stop_early`, and `do_nothing`. Each held-out/salted seed runs all six policies. No seeds are selected after viewing outcomes, and no code tuning uses held-out outcomes.

The evaluator reports raw economic utility as well as normalized reward, deliveries, on-time share, late deliveries, distance, and unfinished orders. Paired differences compare the reference against each baseline on the same worlds; 95% confidence intervals use the documented normal approximation to the mean difference. These intervals characterize this fixed synthetic distribution; they do not establish performance on other delivery workloads.

## Implementation limits

The city is a connected 5×5 grid spanning 8×8 km with seed-dependent shortcuts and road speeds. There are 28–36 orders, 8–12 initially released, four vans with capacities 6–10, and traffic slowdowns/closures. Movement advances in one-minute ticks. The complete assigned manifest must fit a van's capacity, including parcels awaiting pickup. Any missing parcel triggers a depot visit before the route continues; multi-load tours within a single assigned route are outside this version.

The backend adapter performs serialized HTTP requests against the real FastAPI application using ASGI transport in local and sidecar execution. The same application runs independently under Uvicorn, and the adapter can instead connect over TCP HTTP. SQLite state and transaction history are persisted on disk in an episode-isolated temporary directory. The backend control application is private inside the simulator sidecar; the agent reaches only the Crucible MCP application.

A higher reference score than simple heuristics is useful evidence about planning. A reproducible gap against a named model must be measured separately and cannot be inferred from that baseline comparison.
