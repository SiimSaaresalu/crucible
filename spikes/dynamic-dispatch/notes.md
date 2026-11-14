# Experiment notes

- The proposal and six-step implementation plan are preserved alongside the code.
- Local development smoke seeds 0–1 exercised the evaluation script before the policy freeze. They were not used to alter environment reward or select held-out worlds.
- Policies froze before the full 50-world held-out evaluation and ten-world salted validation. Both runs verified the same source fingerprint and completed with zero environment errors.
- No calibration split was used in this implementation. Its seeds remain reserved.
- The measured regret-specific increment over greedy insertion is negligible and uncertain. Geographic and deadline baselines provide a clear economic contrast; they do not demonstrate long-horizon regret planning by themselves.
- Salted reference validation uses a reproducible private-to-policy salt namespace. Harbor exports instead use independent secret salts and must be compared against the oracle run on those exact exports.
- Exact actual Harbor jobs, export artifacts, and reproduction commands are in `harbor.md`. Generated exports and detailed jobs are ignored because they contain capabilities or large traces; only sanitized measurements belong in version control.

- Three actual OpenCode + `google/gemini-3.8-flash` trials completed without trial exceptions or rejected in-episode plans. One redundant post-completion `finish` was correctly refused after seed 1002 was already done. The model won all three paired salted worlds (mean reward 0.94508 vs reference 0.91931); the intended competitive gap was not demonstrated. No further model trials or scenario tuning were performed.
