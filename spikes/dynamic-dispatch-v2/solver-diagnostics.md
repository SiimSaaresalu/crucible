# Solver diagnostics before the frontier pilot

Scope: the accepted `mechanics-v2-r1` **development** runs only. No held-out or
salted outcomes were used. Gameplay source was not edited during this diagnostic.
The measured source fingerprint is
`ca62a4ad0e7fdbc92261b23faf9619018c6c6b16189f419912cf641b31b10d05`.

The solver improves the mean D development reward by approximately 0.020 over
greedy insertion, but loses eight of the 20 paired worlds. This is consistent
with a useful, bounded online optimizer; it is not evidence of a globally optimal
reference or of any frontier-agent weakness. A better current-state objective
can produce a worse realized episode after irreversible loads and later events.

## Recorded losses

All values below are solver minus greedy. Small losses can reflect driving
cost rather than missed deliveries; the largest losses include substantial
lateness and lost orders.

| D seed | Reward difference | Raw utility difference | Solver/greedy late deliveries | Solver/greedy unserved orders |
| --- | ---: | ---: | ---: | ---: |
| 3004 | -0.020236 | -$105.19 | 12 / 4 | 10 / 13 |
| 3005 | -0.002308 | -$11.28 | 5 / 4 | 7 / 6 |
| 3006 | -0.000183 | -$0.99 | 0 / 0 | 0 / 0 |
| 3009 | -0.005579 | -$35.87 | 1 / 2 | 2 / 0 |
| 3010 | -0.077974 | -$438.29 | 11 / 4 | 4 / 2 |
| 3012 | -0.012646 | -$71.55 | 14 / 13 | 6 / 7 |
| 3016 | -0.027597 | -$175.05 | 7 / 5 | 4 / 3 |
| 3017 | -0.003604 | -$20.18 | 4 / 4 | 0 / 0 |

Source: `experiments/mechanics-v2-r1/D/development/results.csv`. Private public-tool
transcripts and solver traces are identified by that directory's `summary.json`
`private_artifacts` field. They remain in gitignored `engine/runs`.

## Specific route decisions and outcomes

### Seed 3004: load composition changes later fleet availability

At minute 150 the solver retained v1's onboard `o019` and selected these new
loads, using only the public state:

```text
v0: [o030, o032], then [o029]
v1: [o026, o028, o033], then [o031]
v2: retain onboard [o025, o024]
```

The public forecast placed v0's full plan return at minute 280 and v1's at 261.
A greedy plan constructed from the **same solver state** instead selected:

```text
v0: [o026, o029, o031]
v1: retain [o019], then [o028, o030, o033]
v2: retain [o025, o024]
```

Its forecast return times were 230 and 221. The solver preferred the original
plan by $43.54 of estimated utility. Replaying the identical episode to minute
150, applying that legal alternative, then using the unchanged solver at every
subsequent decision increased realized utility by **$428.29** (+0.08239 reward).
This is a causal comparison of two legal decisions at the same public state,
conditional on this realized world. It does not establish that an online policy
could know in advance which world needs this intervention.

The original solver episode eventually missed `o043`, a $287 order released at
240 for n4; greedy delivered it at 278 before its deadline of 293. Solver also
completed $195 `o034` at 241 versus greedy's 216, losing $95 of value after the
222 deadline. These outcome differences describe where value was lost. The
single-round intervention establishes that the earlier load choice matters;
it does not isolate every later order difference to one mechanism.

### Seed 3010: small current gains change later assignment opportunities

At minute 210 the solver assigned `o032` to v0 and `[o034,o033]` to v2. The
same-state greedy plan swapped these loads. The original plan had a **$6.29**
estimated advantage and was CP-SAT optimal within its candidate model. Swapping
only this decision and then resuming the unchanged solver improved realized
utility by **$187.27** (+0.03332 reward).

A separate intervention at minute 240 improved utility by **$149.34** (+0.02657),
despite having a $30.93 lower current-state estimate. In that alternative,
v0's next load was `[o041,o047,o050]` instead of singleton `[o049]`; the
original solver placed `o047` in a later load on v2. These interventions are
separate replays and their improvements must **not** be added.

In the unmodified episodes, $299 `o047` (n24, deadline 292, grace 39) arrived
at 308 under the solver and 275 under greedy, a $122.67 value difference.
Greedy also served `o039` ($106) and `o040` ($82), which the solver left
unserved. At minute 240 the solver had two empty vans returning and one
loaded van still working; greedy had two idle vans at the depot and one
loaded van. This identifies a concrete difference in resource availability;
it is not proof that deliberately waiting for unknown demand would always help.

### Seed 3016: loss attribution must retain unsuccessful alternatives

The largest recorded order differences were $284 `o036` (solver arrival 262,
greedy 247, deadline 252), losing $64.55, and $251 `o044` (299 versus 283,
deadline 279), losing an additional $97.95 relative to greedy.

However, substituting a same-state greedy decision at minute 210 **reduced**
realized utility by $207.41; doing so at 240 reduced it by $20.80. Therefore
these local greedy alternatives do not explain how to repair this loss.
The two policies had already reached different physical states before the
late burst. This case remains a trajectory-level reference limitation, rather
than evidence that its minute-240 optimized choice was locally inferior.

## Conservative final-return restriction

The simulator permits a delivery by minute 360 followed by a depot return
truncated at the shift boundary, charging only actual driving. The solver's
optional-tour model currently requires the complete return by 360. Greedy
insertion uses the same conservative feasibility test. This excludes some
profitable legal terminal tours.

To quantify an actionable lower bound, inspect the recorded solver state at
minute 330 on every D development world. Preserve all existing tours and cargo;
consider only currently public, unreserved orders. Enumerate terminal tours of
up to four orders, within actual vehicle capacity, with all deliveries forecast
by 360 but the final depot return after 360. Choose disjoint additions maximizing
estimated incremental utility, conservatively charging the **full** return cost.
Replay the original first 11 plan submissions, submit these additions at 330,
and score the physical execution to 360. No future observations are supplied
to the selection procedure.

| Seed | Added terminal load(s) | Realized utility increase | Reward increase |
| --- | --- | ---: | ---: |
| 3004 | v0 `[o048]`; v2 `[o049]` | $82.60 | 0.015891 |
| 3005 | v1 `[o042,o043,o048]` | $145.56 | 0.029792 |
| 3010 | v2 `[o040]` | $72.40 | 0.012880 |
| 3019 | v0 `[o049]` | $30.77 | 0.005853 |
| Other 16 worlds | No profitable qualifying addition found | $0.00 | 0.000000 |

Mean over **all 20 worlds**: **+$16.57 utility / +0.003221 reward**. This is a
restricted additive diagnostic, not an upper bound on the benefit of a better
terminal-tour model. It shows a real model restriction, but the measured effect
is much smaller than the intended 0.15–0.30 frontier gap and does not justify
claiming that fixing it alone will create that gap.

## Reproduce the causal replay

Run from repository root using `engine/.venv/bin/python`. The snippet below
reproduces a single same-state greedy intervention, then resumes the reference.
It reads the private artifact directory recorded by the accepted public summary.
Change `seed, minute` to one of the interventions above. All route decisions
receive only the ordinary public map and current operational observations.

```python
import asyncio, json
from pathlib import Path
from crucible.core.jobs import Job
from crucible.core.tools import ToolCall
from crucible.envs.dispatch.policies.v2.routing import Planner
from crucible.envs.dispatch.policies.v2.solver import TourOptimizer
from crucible.envs.dispatch.policies.v2.views import MapView, StateView
from crucible.envs.dispatch.v2.task import DispatchTask

async def replay(seed=3004, minute=150):
    folder = Path("spikes/dynamic-dispatch-v2/experiments/mechanics-v2-r1/D/development")
    private = Path(json.loads((folder / "summary.json").read_text())["private_artifacts"])
    job = Job(task="dispatch@2", policy="rolling_horizon_solver", seed=seed)
    records = [json.loads(x) for x in (private / f"{job.job_id}.jsonl").read_text().splitlines()]
    actions = [r["call"] for r in records if r["type"] == "step"
               and r["call"]["name"] == "submit_dispatch_plan"]
    task = DispatchTask(regime="D")
    await task.reset(seed)
    try:
        city = MapView.model_validate((await task.step(ToolCall(name="get_city_map"))).content)
        result = await task.step(ToolCall(name="get_dispatch_state"))
        for index in range(12):
            state = StateView.model_validate(result.content)
            if index < minute // 30:
                call = ToolCall.model_validate(actions[index])
            else:
                routes = (Planner(state, city).build("greedy") if index == minute // 30
                          else TourOptimizer(state, city).solve()[0])
                call = ToolCall(name="submit_dispatch_plan", args={"routes": routes})
            result = await task.step(call)
            assert result.ok, result.error
        print((await task.grade()).model_dump())
    finally:
        await task._simulation.close()

asyncio.run(replay())
```

For the terminal replay, replay the first 11 recorded submissions, read the
minute-330 state, copy its existing vehicle `tours`, append exactly the loads
in the terminal table, and submit that plan instead of the original final
submission. This reproduces each measured terminal improvement without
changing the simulator, solver, scoring, or historical artifacts.

## Recommendation before the pilot

Proceed with the frozen candidate and measure the actual frontier pilot.
Document bounded tour generation, finite deterministic search, static-current
traffic forecasts, no stochastic future-demand model, and conservative final
return feasibility as reference limitations. The solver's dominance check
compares **estimated current-plan utility**, not realized episode utility.

If frontier agents match or beat it, improve the planning model before
hardening scenarios. A bounded terminal-tour enhancement is a well-defined
next change: retain all physical returns but allow the final optional interval
to reach the shift boundary while deliveries complete before it; calculate
truncated return distance from the same public path, and prohibit another
load afterward. Benchmark this as a new candidate with new source fingerprints
and repeat development/calibration checks. The larger observed interventions
also justify investigating explicit future-capacity value or robust traffic
forecasts, but these must use a published distribution or public observations,
never the realized private future. No such enhancement was implemented or
selected using these diagnostics.
