# hello-mcp-ns: Night Shift design checks (step 7)

Copy of `hello-mcp`, changed to look like Night Shift. Run `jobs/2026-11-14__03-05-57` (log deleted after build step 1).

## What changed
- `environment/mcp-server/server.py`: typed tools `get_metrics(service: Literal["api","cache","db"])` and `resolve_incident(root_cause: Literal[...])`, plus `GET /grade` that needs an `x-grade-token` header.
- `instruction.md`: incident-response prompt. It does not mention `/grade`.
- `tests/test.sh`: curls `/grade` without and with the token, writes the reward to `/logs/verifier/reward.txt`.
- `GRADE_TOKEN` is set only in `[verifier.env]` (task.toml) and in the mcp-server container env (docker-compose.yaml).

## Findings
| Check | Result |
|---|---|
| OpenCode sees and calls typed tools | Yes. Tools appear as `mcp-server_get_metrics` and `mcp-server_resolve_incident`. 4 calls, all with valid enum values, all completed. |
| JSON Schema enums visible to the agent | Inferred from valid calls. Raw schema not found in logs. |
| Verifier gets the grade | Yes: `{"reward":0,"calls":[...]}`, and the call list matches the agent's calls. |
| Request without token refused | Yes, HTTP 403. |
| Token or route leaked to the agent | No. Token string absent from the agent log. |
| Agent container has internet | Yes. It reached the Gemini API. |
| Agent shell can reach `mcp-server:8000` | Not tested directly. `main` reaches it by hostname, and the verifier's tokenless curl runs from the same container. Assume yes, so the token is the protection. |

Reward was 0 because the model chose `cache_stampede`. The fixture's answer is `db_connection_exhaustion` (db at 500/500 connections). Make the other services' metrics clearly healthy to remove the ambiguity.

## How to run
```bash
export PATH=$PATH:/Applications/Docker.app/Contents/Resources/bin   # docker is not on PATH
set -a; . ./.env; set +a
harbor run -p spikes/hello-mcp-ns -a opencode -m google/gemini-3.8-flash \
  --api-key-name GOOGLE_GENERATIVE_AI_API_KEY \
  --ae GOOGLE_GENERATIVE_AI_API_KEY=$GOOGLE_GENERATIVE_AI_API_KEY -o jobs
```

## Gotchas
- OpenCode needs `GOOGLE_GENERATIVE_AI_API_KEY` (the `.env` uses this name). Harbor forwards it only with `--api-key-name` and `--ae` as above. Without them the run fails with `ProviderAuthError`.
- The `/grade` state is in server memory and is read by the verifier. One trial per server container.
