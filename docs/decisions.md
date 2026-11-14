# Decisions

One line for each design decision, with the date and the reason.

- 2026-11-14 — Agent interface: MCP, not the CLI fallback. In Harbor 0.26.0, OpenCode with `google/gemini-3.8-flash` saw and called the typed MCP tools (valid enum arguments). The MCP server ran in its own container. The verifier got the grade from a token-protected `/grade` route, and a request without the token got 403. Evidence: `spikes/hello-mcp-ns/README.md`.
- 2026-11-14 — Gemini key name: `GOOGLE_GENERATIVE_AI_API_KEY`. OpenCode reads this name. Pass it with `--api-key-name GOOGLE_GENERATIVE_AI_API_KEY --ae GOOGLE_GENERATIVE_AI_API_KEY=$GOOGLE_GENERATIVE_AI_API_KEY`; without these flags the run fails with `ProviderAuthError`.
- 2026-11-14 — Pin Harbor to 0.26.0. OpenCode is still unpinned (`opencode-ai@latest`); pin it before the submission runs and write both versions in the README.
