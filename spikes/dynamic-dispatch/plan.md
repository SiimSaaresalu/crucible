# Dynamic dispatch implementation plan

Source: “Crucible Spike: Dynamic Delivery Dispatch”, supplied on 2026-11-14.
Environment: `dispatch@1`. Each numbered step has its own implementation subagent;
the coordinating agent reviews the interfaces and validates the combined change.

1. **Persistent HTTP backend — `step1_backend`.** Build a standalone FastAPI
   service, isolated SQLite database, immutable released orders, vehicle cargo,
   atomic route assignment, and authenticated simulator events. Validate pickup,
   capacity, identifiers, duplicate assignments, and transaction persistence.
2. **Deterministic world — `step2_world`.** Build the city, salted random streams,
   order arrivals, traffic, clock, and edge-by-edge vehicle movement. Commit
   physical outcomes through the backend HTTP interface. Test seeded replay and
   in-progress route changes. Depends on the backend interface in step 1.
3. **Task and economic grading — `step3_integration_scoring`.** Replace the
   generated environment with the four typed MCP tools, a 40-call budget, private
   future schedules, and a repeatable economic grader. Grade delivery outcomes
   against all generated value, including unfulfilled orders. Depends on steps 1–2.
4. **Observation-only dispatch policies — `step4_policies`.** Implement nearest,
   deadline, greedy insertion, regret insertion, and stop-early policies. Respect
   capacity, physical pickup, and locked edges using public observations only.
   Compare heuristics without altering reward normalization. Depends on step 3's
   observation contract.
5. **Integrity and Harbor — `step5_integrity_harbor`.** Add exploit and export
   checks; package the backend inside the existing simulator sidecar with
   episode-isolated databases and no agent access to control credentials. Preserve
   token-protected grading. Document exact export and execution commands. Depends
   on steps 1–4.
6. **Evaluation and findings — `step6_evaluation`.** Run disjoint development,
   calibration, 50+ held-out, and salted comparisons. Save raw results and paired
   statistics. Attempt actual Harbor oracle/nop and three independent frontier
   runs when the required runtime and credentials are available. Record any
   unavailable execution honestly and preserve commands for reproduction.

Steps 1–2 begin together with a shared interface. Later steps start as their
dependencies become concrete. Final validation includes environment-specific,
generic, policy isolation, golden-score, lint, type, and export checks as
appropriate. A competitive gap is a measured research result, not an acceptance
condition that may be manufactured by changing normalization.

## Execution record

- Steps 1–5 are implemented and reviewed. Each step was assigned to the named
  dedicated subagent above.
- Step 6 completed all 300 held-out and 60 salted policy episodes with zero
  environment errors. The implementation fingerprint still matches both saved
  measurements. All three paired OpenCode/Gemini trials also completed without
  exceptions or rejected in-episode tool calls. One redundant call after the
  shift ended was correctly refused and did not affect scoring.
- Repository verification: 542 tests passed and 4 skipped before the final HTTP
  test additions; the final dispatch plus golden-score checks pass all 76 tests.
  Ruff lint, formatting, and strict Pyright are clean.
- Three actual salted Harbor oracle runs and three nop runs completed without
  exceptions. Reference mean reward is 0.9193123593; nop is exactly zero. Local
  salted replay matches Harbor scores, breakdowns, and call counts exactly.
- Gemini's mean reward was 0.945085, beating the reference on each of the three
  paired exported worlds. The implementation steps are complete, but the research
  criterion of a heuristic advantage over the model was not achieved. The local
  experiments also do not establish a regret-specific advantage over greedy
  insertion. No scenario or scoring changes were made to manufacture a gap.
- Read [findings.md](findings.md) for economic comparisons and research limits,
  and [harbor.md](harbor.md) for artifact digests and reproduction commands.
