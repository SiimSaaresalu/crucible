# Decisions

One line for each design decision, with the date and the reason.

- 2026-11-14 — Agent interface: MCP, not the CLI fallback. In Harbor 0.26.0, OpenCode with `google/gemini-3.8-flash` saw and called the typed MCP tools (valid enum arguments). The MCP server ran in its own container. The verifier got the grade from a token-protected `/grade` route, and a request without the token got 403. Evidence: `spikes/hello-mcp-ns/README.md`.
- 2026-11-14 — Gemini key name: `GOOGLE_GENERATIVE_AI_API_KEY`. OpenCode reads this name. Pass it with `--api-key-name GOOGLE_GENERATIVE_AI_API_KEY --ae GOOGLE_GENERATIVE_AI_API_KEY=$GOOGLE_GENERATIVE_AI_API_KEY`; without these flags the run fails with `ProviderAuthError`.
- 2026-11-14 — Pin Harbor to 0.26.0. OpenCode is still unpinned (`opencode-ai@latest`); pin it before the submission runs and write both versions in the README.
- 2026-11-14 — Grading path: in build step 6 or 7, try Harbor's separate verifier with a `[[verifier.collect]]` hook (the `sidecar-artifacts` example): the hook runs in the sidecar after the agent stops and writes the grade to a file; the grade route listens only on localhost. Check it with a short spike first, because `[[verifier.collect]]` is not in the config reference. Fallback: the token-protected `/grade` route from `spikes/hello-mcp-ns` (token only in `[verifier.env]`, never in `[environment.env]`).
- 2026-11-14 — Engine tooling: Python 3.14 everywhere (`engine/.python-version`, `mise.toml`, `requires-python = ">=3.14"`, ruff `py314`, pyright 3.14); no older-Python floor, because the code only runs where we control the interpreter. pyright runs in strict mode. The `engine` CI workflow runs only for changes to `engine/**`, `configs/**` and its own file, so doc-only PRs skip it.
