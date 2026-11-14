# Harbor runs

Runs of the generated `incident@1` tasks (`nightshift export harbor --seeds 0-9`) on a laptop with local Docker. Harbor 0.26.0, OpenCode 1.19.5. The job folders are in `jobs/` (gitignored); this file keeps the scores.

Date: 2026-11-14. Engine commit: `44f0dca` plus the step 7 export.

## Summary

| Agent | Model | Job | Tasks | Mean reward | Root cause correct |
|---|---|---|---|---|---|
| oracle (`solve.sh`, reference policy) | none | `incident-1-oracle` | 10 | 0.971 | 10/10 |
| opencode | `google/gemini-3.8-flash` | `incident-1-opencode-gemini` | 3 | 0.955 | 3/3 |
| nop | none | `incident-1-nop` | 3 | 0.000 | 0/3 |

The oracle reward is equal to the in-process reference score (`nightshift eval --task incident@1 --policy reference --seeds 0-9`) on each seed, to all printed digits.

## Per trial

| Job | Seed | Reward | Steps | Stop reason | Root cause correct |
|---|---|---|---|---|---|
| incident-1-oracle | 0 | 0.935 | 8 | task_done | yes |
| incident-1-oracle | 1 | 0.983 | 7 | task_done | yes |
| incident-1-oracle | 2 | 0.977 | 6 | task_done | yes |
| incident-1-oracle | 3 | 0.976 | 6 | task_done | yes |
| incident-1-oracle | 4 | 0.977 | 6 | task_done | yes |
| incident-1-oracle | 5 | 0.958 | 7 | task_done | yes |
| incident-1-oracle | 6 | 0.980 | 6 | task_done | yes |
| incident-1-oracle | 7 | 0.962 | 7 | task_done | yes |
| incident-1-oracle | 8 | 0.981 | 7 | task_done | yes |
| incident-1-oracle | 9 | 0.981 | 8 | task_done | yes |
| incident-1-opencode-gemini | 0 | 0.918 | 13 | task_done | yes |
| incident-1-opencode-gemini | 1 | 0.983 | 13 | task_done | yes |
| incident-1-opencode-gemini | 2 | 0.965 | 10 | task_done | yes |
| incident-1-nop | 0 | 0.000 | 0 | policy_stop | no |
| incident-1-nop | 1 | 0.000 | 0 | policy_stop | no |
| incident-1-nop | 2 | 0.000 | 0 | policy_stop | no |

## Observations

- OpenCode used only the MCP tools (`mcp-server_*`). There were no shell or web calls. The grade token and `/grade` do not appear in its logs.
- Gemini 3.8 Flash scores close to the reference. It does not score above it: the reward is mostly set by how soon the repair starts (see `docs/decisions.md`, calibration finding), and Gemini made 3–6 more calls than the reference. With only a small gap between the reference and the model, this task cannot separate strong models well. Faults 4–6 should be harder.
- `[agent] network_mode = "allowlist"` with `allowed_hosts` fails at startup in Harbor 0.26.0. The error is `conflicting options: port exposing and the container type network mode` on the sidecar. Job `incident-1-opencode-allowed-hosts`.

## Commands

```bash
export PATH=$PATH:/Applications/Docker.app/Contents/Resources/bin
uv run --directory engine nightshift export harbor --task incident@1 --seeds 0-9 --out ../harbor/incident-1
harbor run -p harbor/incident-1 -a oracle -o jobs --job-name incident-1-oracle
harbor run -p harbor/incident-1 -a nop -o jobs --job-name incident-1-nop -l 3
set -a; . ./.env; set +a
harbor run -p harbor/incident-1 -a opencode -m google/gemini-3.8-flash \
  --ak version=1.19.5 --ak disable_web_search=true \
  --api-key-name GOOGLE_GENERATIVE_AI_API_KEY \
  --ae GOOGLE_GENERATIVE_AI_API_KEY=$GOOGLE_GENERATIVE_AI_API_KEY \
  -o jobs --job-name incident-1-opencode-gemini -l 3
```
