# Build step 3 — Core interfaces and runner

Source: *Night Shift Gym — MVP Technical Brief*, "Build order", step 3:

> Core interfaces and runner (45 min). Write the core/ modules (async) and LocalExecutor.
> Add a small dummy task ("guess the number") that shows the loop works from start to end.

Steps 1 (Harbor check) and 2 (repo and tooling) are merged. `engine/src/nightshift/core/` and the
other subpackages exist but are empty. This step fills `core/` and adds one throwaway task so the
loop runs end to end. The Incident family (step 4), the CLI (step 5) and the server (step 6) are
**out of scope**.

## Goal and definition of done

- All ten `core/` modules from the brief's repo layout exist, are async where the brief says, and
  pass `ruff`, `ruff format --check` and `pyright` (strict).
- A `guess@1` task and a binary-search reference policy run through `LocalExecutor` with
  `concurrency=8`, write one JSONL transcript per episode, and give the same scores as a sequential
  run.
- `uv run pytest` is green locally and in the `engine` CI workflow.
- `docs/decisions.md` has one line for each interface choice the brief leaves open (listed below).

## Dependencies

```bash
cd engine
uv add pydantic pyyaml
uv add --dev types-pyyaml
```

`pyyaml` is for `load_scoring_config()` (step 4's `scoring.yaml`). `types-pyyaml` keeps strict
pyright quiet. No typer, FastAPI or MCP yet — those belong to steps 5 and 6.

## Module plan (`engine/src/nightshift/core/`)

Order matters: each module uses only the ones above it.

### 1. `tools.py` — ToolSpec, ToolCall, ToolResult, Observation

- `ToolSpec` (frozen Pydantic model): `name`, `description`, `kind: Literal["read", "action", "clock", "end"]`,
  `args_model: type[BaseModel]` (excluded from serialisation), plus `json_schema()` that returns
  `args_model.model_json_schema()`. This is the "define each tool one time" rule: the same model
  validates calls and makes the schema for MCP and HTTP later.
- `ToolCall`: `name: str`, `args: dict[str, JsonValue]`.
- `ToolResult`: `ok: bool`, `content: JsonValue`, `error: str | None`. Only explicit fields — no
  hidden state ever goes in here.
- `Observation`: `text: str`, `data: dict[str, JsonValue]`, `last_result: ToolResult | None`.
  The brief uses `Observation` but does not define it; this is the minimal shape (record it in
  decisions.md).
- `validate_call(specs, call) -> BaseModel | ToolResult`: unknown tool or bad arguments return an
  `ok=False` result with a clear message built from the Pydantic error, and the caller must not
  change state. Error text must not echo hidden values (only the argument names and the given
  values).
- `make_tool(name, description, kind, **fields)` helper over `pydantic.create_model`, for game
  adapters later. Small and tested now, so its use later is cheap.

### 2. `rng.py` — RngStreams

- `RngStreams(seed)` with `exogenous` and `actions` as separate `random.Random` instances, plus
  `stream(name)` for more named streams.
- Derive each sub-seed with `hashlib.sha256(f"{seed}:{name}")`, never `hash()` (salted per
  process) — so the same seed gives the same world on every machine and in every container.
- Exogenous events (traffic, fault minute, red herrings) are drawn from `exogenous` in `reset()`,
  before the first action; actions never touch it. Test this property directly.

### 3. `scoring.py` — Score, ScoreSpec, load_scoring_config

- `Score` exactly as in the brief: `total`, `breakdown: dict[str, float]`, `flags: dict[str, bool]`.
- `ScoreSpec` Protocol: `def compute(self, episode: E) -> Score` (generic over the task's episode
  record) — a pluggable calculator, so step 4's grader plugs in without core changes.
- `load_scoring_config(path, model: type[T]) -> tuple[T, str]`: load YAML, validate with the given
  Pydantic model, return the model and a config hash (sha256 of canonical JSON of the *validated*
  model, so formatting changes do not change the hash).

### 4. `task.py` — TaskSpec, Task

- `TaskSpec` as in the brief: `id`, `version`, `max_steps`, plus a `ref` property giving
  `"id@version"`.
- `Task` ABC: `reset(seed, difficulty=0) -> Observation`, `tools()`, `step(call) -> ToolResult`,
  `done()`, `grade() -> Score`, and one addition: `instruction() -> str`. `Policy.reset` needs the
  instruction text and the task is the only source of it (record in decisions.md).
- Add a `scoring_hash` class attribute or property (default `"none"`) so transcripts can record it.

### 5. `policy.py` — Policy

- `Policy` Protocol exactly as the brief: `name`, `reset(task_spec, tools, instruction)`,
  `act(obs) -> ToolCall | None` (`None` = stop). `runtime_checkable` is not needed.

### 6. `registry.py` — tasks and policies

- `register_task(cls)` decorator keyed by `cls.spec.ref`; `get_task("guess@1")` returns a fresh
  instance; `get_task("guess")` returns the newest version; unknown ids raise with the list of
  known ids. `list_tasks()` for the CLI later.
- Also `register_policy(name)` / `get_policy(name, task_ref)`. The brief only names the task
  registry, but jobs are data (`Job(policy="reference")`), so the executor must build policies
  from a name. Reference policies are per task, so the key is `(name, task_id)` with a fallback to
  task-agnostic policies such as `do_nothing`.
- Registration happens at import: `nightshift.tasks` and `nightshift.policies` import their
  modules, and the registry imports those packages lazily on first lookup.

### 7. `transcript.py` — versioned JSONL records

- One file per episode, three record types, each with `type` and `schema_version: 1`:
  - `header`: task ref, scoring hash, policy name, seed, repeat, job id, git commit, start time.
  - `step`: step index, `ToolCall`, `ToolResult`.
  - `footer`: `Score`, steps used, stop reason (`policy_stop`, `task_done`, `budget_exhausted`,
    `error`), end time.
- `TranscriptWriter` writes to `<path>.tmp` and renames on close, so a file that exists is
  complete — this is what makes resume safe.
- `git_commit()`: `NIGHTSHIFT_GIT_COMMIT` env var first (for Docker images), else
  `git rev-parse HEAD`, else `"unknown"`. Computed once per process.
- `read_transcript(path)` for tests and for the summary merge.
- Determinism comparisons ignore the time fields; everything else must match byte for byte.

### 8. `episode.py` — run_episode, EpisodeResult

The only loop in the engine:

```python
async def run_episode(task, policy, seed, *, job, writer) -> EpisodeResult:
    obs = await task.reset(seed)
    await policy.reset(task.spec, task.tools(), task.instruction())
    for step in range(task.spec.max_steps):
        if task.done():
            break
        call = await policy.act(obs)
        if call is None:
            break                       # stop_reason = policy_stop
        result = await task.step(call)
        writer.step(step, call, result)
        obs = Observation(text=..., last_result=result)
    else:
        ...                             # stop_reason = budget_exhausted
    score = await task.grade()
```

- `EpisodeResult`: job, task ref, policy, seed, `Score`, steps, stop reason, transcript path.
- Set `score.flags["budget_exhausted"]` here, so every task gets it for free.
- An exception from the policy or task ends the episode with `stop_reason="error"`,
  `flags["env_error"]=True` and a zero score, and the transcript footer records the error type.
  Only `InfraError` (new, small exception class in this module) is retryable — never a bad agent
  result.

### 9. `jobs.py` — RunSpec, Job, job_id

- `RunSpec`: `task`, `policy`, `seeds: list[int]`, `repeats: int = 1`.
- `Job` (frozen): `task`, `policy`, `seed`, `repeat`, and `job_id` = first 16 hex chars of sha256 of
  the canonical JSON of those four fields (as in the brief: `hash(task@version, agent, seed, repeat)`).
- `parse_seeds("0-9,12,20-21") -> list[int]` for the CLI in step 5; reject empty, reversed or
  negative ranges.
- `expand(run_spec) -> list[Job]` in a stable order.

### 10. `executor.py` — Executor, LocalExecutor

- `Executor` Protocol: `async def run(self, jobs: Sequence[Job], out_dir: Path) -> list[EpisodeResult]`.
- `LocalExecutor(concurrency=1, max_retries=2)`:
  - `asyncio.Semaphore(concurrency)` inside an `asyncio.TaskGroup`; a fresh task and policy
    instance per job (no shared state between episodes).
  - Transcript path `out_dir/episodes/<job_id>.jsonl`. If it already exists, read its footer and
    skip the job (resume).
  - Retry only on `InfraError`, mark the result `retried=N`.
  - Results returned in job order, whatever the finish order.
  - At the end, write `out_dir/summary.csv` from all results (one writer, after all episodes).
- Per-provider rate limiters come with LLM policies later; leave a comment, not code.

## Dummy task: `guess@1`

`engine/src/nightshift/tasks/guess/task.py` — throwaway, but it exercises every core path.

- Hidden state (`_Hidden`: `target`) is a separate type from the observation and is never put in a
  `ToolResult`. `target` comes from `RngStreams(seed).exogenous.randint(1, 100)` in `reset()`.
- Tools: `guess(n: int, 1 ≤ n ≤ 100)` → `"higher" | "lower" | "correct"`;
  `give_up()` (kind `end`).
- `max_steps = 10`, done when correct or after `give_up`.
- Grade: `total = 1 - (guesses - 1) / max_steps` when solved, else `0`; `breakdown` has
  `guesses`; `flags` has `solved`.
- Policies in `policies/`: `reference/guess.py` (binary search, always solves in ≤ 7) and a
  task-agnostic `do_nothing` in `baselines.py` (returns `None` at once — step 5 reuses it for
  Incident).

## Tests (`engine/tests/core/`)

These are core-level tests. The nine Incident tests in the brief come in step 6.

| Test | Checks |
|---|---|
| `test_tools.py` | JSON Schema has the arg constraints; unknown tool and bad args give `ok=False` with a clear message; `make_tool` works |
| `test_rng.py` | pinned first values for seed 0 (catches accidental changes); streams independent; drawing from `actions` does not change `exogenous` |
| `test_scoring.py` | config loads and validates; hash is stable and ignores YAML formatting; bad config gives a clear error |
| `test_registry.py` | `guess@1` and `guess` resolve; unknown id lists known ids; policy fallback to task-agnostic |
| `test_jobs.py` | `job_id` stable and unique; `parse_seeds` happy and error cases |
| `test_episode_guess.py` | reference solves seeds 0–49; do-nothing = 0; budget stop sets the flag; bad tool call does not change state; policy exception → `env_error` |
| `test_transcript.py` | header/step/footer round trip; no `.tmp` file left; no hidden `target` value in any step record before the correct guess |
| `test_determinism_guess.py` | hypothesis over seeds and guess sequences: same input → same transcript (minus times) and score |
| `test_executor.py` | `concurrency=8` gives the same results as `concurrency=1`; a second run skips all jobs; `InfraError` is retried and marked; `summary.csv` has one row per job |

Keep `test_smoke.py`.

## Order of work (about 45–60 min)

1. `uv add` dependencies; commit the lockfile.
2. `tools.py`, `rng.py`, `scoring.py` with their tests.
3. `task.py`, `policy.py`, `registry.py`.
4. `guess` task + reference + do-nothing; `transcript.py`; `episode.py`; episode tests.
5. `jobs.py`, `executor.py`; executor and determinism tests.
6. `ruff check --fix`, `ruff format`, `pyright`, `pytest`; fix until clean.
7. Add the decisions.md lines; open a small PR into `main`.

## decisions.md lines to add

- `Observation` shape: `text`, `data`, `last_result`.
- `Task.instruction()` added to the brief's `Task` interface.
- Policy registry keyed by `(name, task_id)` with task-agnostic fallback.
- RNG sub-seeds from sha256, not `hash()`.
- Transcript: one JSONL file per episode, written via `.tmp` + rename; resume = file exists.

## Risks and notes

- **Time:** 45 min in the brief is tight for ten modules plus tests. If it runs long, keep
  `make_tool`, `InfraError` retry and `summary.csv` as stubs with TODO and finish them in step 5.
- **Pyright strict + Pydantic generics:** `ScoreSpec` and `load_scoring_config` generics may need
  `TypeVar(bound=BaseModel)`; avoid `Any` leaks in `JsonValue` fields.
- **No hidden state in errors:** Pydantic error messages include the input value, which is the
  agent's own input, so this is safe; never format task state into error text.
- **`nightshift:main`** still prints "Hello". Leave it — step 5 replaces it with the typer app.
