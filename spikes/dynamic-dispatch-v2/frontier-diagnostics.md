# Actual frontier pilot r0: route-level findings

Status: completed analysis of **three actual paired Harbor trials**, one model
trial per world. The model wins all three. There are no model-loss cases in
this batch. These calibration-pilot results do not establish held-out performance
or estimate model repeat variance.

The comparison uses the identical preserved export
`harbor/dispatch-2-candidate-d-v1-pilot`, with jobs
`dispatch-2-candidate-d-v1-pilot-oracle` and
`dispatch-2-candidate-d-v1-pilot-opencode-gemini-r0`. Routes, timestamps, cargo,
traffic observations, and final economics below come from each trial's
`verifier/transcript.json` and `verifier/grade.json`.
The frontier configuration is the documented OpenCode/Gemini pilot configuration.
The reference scores here are the **actual Linux Harbor oracle scores**, not
native Mac regenerated-reference scores.

## Paired economic result

Dollar differences below are model minus reference. Positive driving-cost
change is an extra cost for the model; utility subtracts that change.

| Seed | Reference reward | Model reward | Reward difference | Realized value change | Driving cost change | Raw utility change |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 3100 | 0.880798451 | 0.944811486 | +0.064013035 | +$371.42 | +$1.81 | +$369.61 |
| 3101 | 0.801535907 | 0.874307939 | +0.072772032 | +$406.78 | +$0.20 | +$406.58 |
| 3102 | 0.938579327 | 0.970778295 | +0.032198968 | +$151.26 | -$2.46 | +$153.72 |
| Mean | 0.873637895 | 0.929965906 | **+0.056328012** | | | **+$309.97** |

| Seed | Reference/model deliveries | Reference/model on-time deliveries | Reference/model distance km |
| --- | ---: | ---: | ---: |
| 3100 | 48 / 52 | 40 / 45 | 240.485281 / 245.656854 |
| 3101 | 48 / 51 | 38 / 43 | 261.656854 / 262.228283 |
| 3102 | 50 / 52 | 44 / 49 | 280.568540 / 273.539102 |

All six episodes used **14 calls**. Each model episode made 11 plan submissions,
two reads, and `finish`; each reference episode completed its 12 submissions
plus two reads. The model made **zero rejected calls**. This batch shows no
model tool-endurance or invalid-plan disadvantage. Both parties earned only
physically completed deliveries under the same economic formula.

## Seed 3100: urgent cluster loads and later capacity availability

At minute 60, the reference assigned its capacity-eight v2 this load:

```text
[o018 at n24, o012 at n18, o024 at n18, o014 at n10]
```

The sizes were 2+2+2+2. This is a full load, but it traverses three geographic
areas. Its same submission reserved additional future tours across all vans.
The model instead gave its capacity-eight v2:

```text
[o021 at n18, o024 at n18, o025 at n18]
```

Sizes were 3+2+3, also exactly capacity eight. All three deadlines were urgent:
108, 104, and 106. The model gave a capacity-seven van `[o026,o019,o018]`,
combining two other n18 orders with n24. This directly records different capacity
and geographic choices; the policies had already reached different physical
states, so the comparison alone is not an isolated causal intervention.

The resulting order-level economic differences include:

| Order | Value | Deadline / grace | Reference arrival | Model arrival | Model value gain |
| --- | ---: | ---: | ---: | ---: | ---: |
| o025, n18 | $46 | 106 / 27 | 123 | 78 | $28.96 |
| o026, n18 | $32 | 119 / 30 | Unserved | 82 | $32.00 |
| o019, n18 | $71 | 151 / 41 | 160 | 82 | $15.59 |
| o020, n18 | $56 | 176 / 29 | Unserved | 120 | $56.00 |
| o010, n7 | $24 | 119 / 49 | Unserved | 40 | $24.00 |

At the minute-150 burst, all three reference vans still had onboard deliveries:
v0 `[o029,o030]`, v1 `[o017]`, and v2 `[o019,o027,o028]`. The model had only
one loaded van (`o030`); its other vans were returning empty. It assigned
`[o034,o037]` to v0 and `[o031,o040,o032]` to v2. The reference allocated other
new orders but did not complete `o037`.

`o037` is a $224 n20 order, released at 150, deadline 191, grace 45. The model
completed it at 200 for **$179.20**; the reference earned zero. This single order
accounts for approximately 48% of the model's $371.42 realized-value gain.
The model also reached $256 `o032` at 191 versus 207 and $234 `o033` at 206
versus 216, preserving another $101.57 of value in total.

The model did not improve every choice. It reached $227 `o043` at 232 versus
reference 224, losing $50.44 relative to the reference, and had smaller losses
on several ordinary orders. Overall, its extra $371.42 delivery value outweighed
$1.81 of additional driving cost. The evidence supports better realized capacity
and timing in this trajectory; it does not prove the model anticipated unknown
arrivals or that long future queues are always harmful.

## Seed 3101: urgent value tradeoffs and a legal terminal tour

At minute 150, the reference's idle capacity-seven van first served singleton
`o030` at nearby n17, then scheduled `[o028,o034,o036]` at n20. The model's idle
capacity-seven van immediately loaded `[o036,o034,o028]`, totaling seven units,
and reached n20 at 177. Reference `o036` arrived at 195.

`o036` is worth $289, with deadline 189 and grace 50. That arrival difference
preserved **$34.68**. The alternative had an economic tradeoff: the model later
reached $289 `o030` at 213 instead of the reference's 159, losing **$67.64**
on that order. A claim that every model priority was superior would be false.

At 180 the model assigned `[o035,o031,o032]` to v0. It completed `o035` and
`o031` at 219. Reference completed `o035` at 228 and never served `o031`.
The two differences contributed **$57.15** and **$60.26**, respectively.
Both are urgent n20 orders with deadline 191; their grace periods are 40 and 38.

Later gains were larger:

| Order | Value | Deadline / grace | Reference arrival | Model arrival | Model value gain |
| --- | ---: | ---: | ---: | ---: | ---: |
| o048, n4 | $68 | 323 / 36 | Unserved | 274 | $68.00 |
| o044, n4 | $95 | 284 / 30 | 302 | 278 | $57.00 |
| o042, n4 | $76 | 288 / 38 | 302 | 278 | $28.00 |
| o050, n1 | $297 | 319 / 44 | 336 | 322 | $94.50 |
| o051, n4 | $104 | 319 / 48 | Unserved | 342 | $54.17 |
| o047, n4 | $38 | 350 / 28 | Unserved | 342 | $38.00 |

At minute 300 the model submitted a capacity-eight terminal load
`[o051,o047,o043]` on v0: sizes 3+2+3, all n4. They were physically delivered
at 342. V0 was still returning at minute 360; its current edge had five minutes
remaining, and the vehicle had empty cargo. The simulator charged actual
partial-return distance. This is legal under the published shift rules.
The solver's optional-tour model would reject a plan whose full return exceeds
360, even when its deliveries are profitable before that boundary.

This does **not** mean simply adding that load to the reference's minute-300
state would reproduce the gain. At that time reference v0 still carried
`[o042,o044]`; at 330 it was still returning and could not teleport to the depot.
The earlier trajectory created the terminal-tour opportunity. The recorded
model behavior demonstrates use of the legal action space; it is not an isolated
estimate of the restriction's total causal effect in this world.

The model's $406.78 higher realized delivery value required only about $0.20
extra driving cost. The main difference was service timing and completion,
not reward normalization or a large driving-distance advantage.

## Seed 3102: earlier urgent service and completion of all orders

At minute 60 the model's capacity-six v2 was already stationary at the depot.
It loaded `[o014,o017]` at n6, then planned `[o019,o020,o023]` at the same node.
The reference's capacity-six van was returning from n0, while its other two
vans still carried earlier loads. These are different physical starting states,
so the outcome comparison cannot isolate a single minute-60 planning choice.

Recorded differences include:

| Order | Value | Deadline / grace | Reference arrival | Model arrival | Model value gain |
| --- | ---: | ---: | ---: | ---: | ---: |
| o017, n6 | $109 | 116 / 33 | 119 | 69 | $9.91 |
| o023, n6 | $27 | 110 / 44 | 137 | 87 | $16.57 |
| o025, n1 | $40 | 119 / 25 | Unserved | 115 | $40.00 |
| o016, n4 | $70 | 110 / 49 | 150 | 125 | $35.71 |
| o003, n18 | $86 | 109 / 35 | 117 | 65 | $19.66 |
| o028, n4 | $59 | 237 / 28 | 245 | 209 | $16.86 |
| o051, n4 | $28 | 313 / 29 | Unserved | 329 | $12.55 |

The model served all 52 generated orders, versus reference 50, and drove
**7.03 km less**, saving $2.46. Gains on the listed orders reconcile to the
$151.26 realized-value difference; smaller order differences are retained in
the source transcripts.

### Same-state causal check: one conservative terminal exclusion

A separate legal replay demonstrates a small repair to the reference's own
state, without generating new reference actions on another OS:

1. Replay the exact first ten oracle plan submissions through the public tools,
   using that same exported private world internally.
2. At minute 300 retain the recorded oracle plan for v0 and v2. Replace v1's
   proposed `[o048]` with `[o051,o048]`, both currently released and unfulfilled.
   V1 was empty and returning; the new load still waits for its physical depot
   arrival. Total size is four, within its capacity seven.
3. Execute that submission, then call `finish`. This preserves all existing
   work and performs no new optimization; the minute-330 oracle action contained
   no new assignments.

The public forecast placed `o051` at 335, `o048` at 350, and the final depot
return at 366. Thus the solver's full-return-by-360 constraint excluded the
candidate. The replay produced exactly those delivery times and improved raw
utility by **$4.65862069** (+0.000975832 reward): $6.75862069 extra delivered
value minus $2.10 extra driving cost. This is a concrete, causal model limitation,
but is much smaller than the model's total $153.72 advantage on this world.

This check uses recorded actions plus a legal public-state route edit. It does
not depend on the native Mac optimizer, whose search choices differ from the
canonical Linux reference. Independent recorded-action replay had already
established physical/economic parity for these worlds across those runtimes.

## Interpretation and next decision

This pilot does **not** support the intended reference-over-frontier gap. The
opposite direction is observed on all three worlds. The agent handled parcel
capacity, depot reloads, changing observations, and physical commitments within
the compact interface. Its gains can be located in actual arrival/completion
records rather than inferred from an aggregate score alone.

The comparison exposes reference limits worth addressing before scenario
hardening: bounded candidate tours, current-traffic forecasts without capacity
option value for future demand, equivalent-objective vehicle allocations that
can have different online consequences, and conservative terminal-return
feasibility. These are candidate explanations for optimization headroom, not
claims that the agent used explicit versions of those algorithms or knew the
private future. Only the terminal replay above supplies an isolated causal
repair in this report; the other findings are route/outcome associations.

Retain this r0 result, run independent model repeats on the same preserved
worlds, and report repeat variance before drawing broader conclusions. Final
local/reference comparisons must use the pinned canonical Linux inference
runtime. Do not replace the observed results with native Mac scores or continue
increasing difficulty solely to reverse this pilot outcome.
