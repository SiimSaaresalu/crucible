# Reward integrity

An agent should earn its reward by doing the task. This page lists how Crucible blocks other ways to get it, the attacks we tried, and the gap that is still open.

## Protections

**No engine in the agent's container.** In Harbor, the agent's container (`main`) is `ubuntu:24.04` with curl and python3. The engine runs only in the sidecar. With the simulator, an agent could replay seeds and match what it sees. Harbor uploads `solution/` (which holds the wheel) only for the oracle agent.

**Token-protected `/grade`.** `GET /grade` needs the `x-grade-token` header (`hmac.compare_digest`); without it the route returns 403. The token is new for each task on each export. It is only in `[verifier.env]` and in the sidecar's environment, never in `[environment.env]`. `/grade` closes the episode, so later MCP calls get `Episode is closed`.

**No `/v1` in the sidecar.** The agent can reach every sidecar route. In sidecar mode the multi-episode API is off, so the agent cannot start its own episodes on the same seed and read their scores. API mode in turn has no `/grade` and no `/mcp`.

**Salt.** Each exported task gets a random `SIDECAR_SALT`, only in the sidecar's environment. The seed in `task.toml` is public, but the salted world cannot be rebuilt from it, even with a copy of the engine from this repo. Transcripts record `"salted": true`, never the salt.

**Leak tests.** Tool results are built from public view models, never from the hidden world. Tests check every successful result for hidden words (`Task.hidden_strings()`: root-cause names, `fault_kind`, `fault_start`, `herring`, `is_bad`) in process, over HTTP and over MCP. The instruction is checked not to name the seed's fault. Policies may not import a task's hidden modules (an `ast` test).

**Web search off.** OpenCode runs with `--ak disable_web_search=true`.

**Generated folders stay private.** `harbor/` is gitignored, because a token in a public repo is readable by an agent with internet.

## Exploit log

Each row is an attack, what blocks it, and the score it gets. In-process scores are means over seeds 0–99 (reference: 0.968).

| Attack | Block | Score |
|---|---|---|
| Do nothing (`do_nothing`, Harbor `nop`) | Reward is measured against the do-nothing replay | 0.000 in process; 0.000 in Harbor (3 trials) |
| Guess the cause after two reads (`stop_early`) | Diagnosis bonus multiplies recovered requests; no repair means nothing to multiply | 0.000 (right cause on 38 of 100 seeds, still 0) |
| Try every repair without triage (`shotgun`) | Penalty per wrong repair and per repair in the healthy phase; side effects in the sim | 0.012 (max 0.162) |
| Only scale, never repair (scripted `scale_only`) | Scaling eases load but repairs nothing, and costs instance-minutes | 0.000 on seeds 0–29 |
| Repair before the fault starts | `healthy_damage` penalty; restarts cause downtime | penalty of 2000 requests per action |
| Read the answer from alert names | Alert names never equal a root-cause string | covered by the leak test (root-cause names are hidden words) |
| Read hidden values from error messages | Errors list only visible values (e.g. visible deploy ids) | checked by test |
| Call `/grade` from the agent | Token required | 403 |
| Start side episodes on `/v1` from the agent | `/v1` is off in sidecar mode | 404 |
| Rebuild the world from the public seed | Per-task salt | world differs from the unsalted seed |
| Invalid calls to probe state | A refused call changes nothing and takes no simulated time, but costs one of 40 calls | checked by test |

OpenCode with Gemini used only the MCP tools in our runs. Its logs show no shell or web calls, and no mention of the grade token or `/grade`.

## Known gap: the agent has internet

Harbor 0.26.0 cannot restrict the agent's network for these tasks. `[agent] network_mode = "allowlist"` with `allowed_hosts` fails at startup on the sidecar with `conflicting options: port exposing and the container type network mode`. So the generated tasks set no allowlist, and the agent's shell can reach the internet.

What that allows:

- The agent can download this repo and the engine. Without the salt it cannot rebuild the world.
- The agent can call any public API from the shell. Web search is off in OpenCode, but a shell `curl` is not blocked.

What still holds: no engine in `main`, a new token and salt on every export, and `disable_web_search=true`. Try the allowlist again on a newer Harbor version. See [decisions.md](decisions.md).
