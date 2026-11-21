# eng-agent-evals

An evaluation suite for an LLM agent that answers questions about internal flow of liquid water in circular pipes (pressure drop, Reynolds number and regime, heat transfer coefficient) by calling calculation tools. The agent is built with PydanticAI; the evaluation uses Pydantic Evals, with deterministic checks on the answer, trajectory checks on the tool calls, and Logfire tracing. Expected answers are computed by the same reference functions the tools wrap.

## Layout

| Path | Contents |
|---|---|
| `src/eng_agent_evals/physics.py` | Reference calculations; the single source of truth |
| `src/eng_agent_evals/units.py` | Unit conversion (pint) |
| `src/eng_agent_evals/calculator.py` | Plain arithmetic for the `calculate` tool |
| `src/eng_agent_evals/agent.py` | Agent, tools, instructions, `Answer` schema |
| `src/eng_agent_evals/ask.py` | Single-question CLI with tracing |
| `src/eng_agent_evals/dataset.py` | Generates the regression set (60 cases) |
| `src/eng_agent_evals/hard_dataset.py` | Generates the hard set (30 cases) |
| `src/eng_agent_evals/evaluators.py` | Custom evaluators |
| `src/eng_agent_evals/run_evals.py` | Runs the suite and summarises results |
| `data/` | Generated datasets (`cases.yaml`, `hard_cases.yaml`) |
| `results/` | Saved evaluation reports |
| `tests/` | Unit tests (no API calls) |
| `docs/` | Methodology, run log and results |

## Commands

```bash
uv sync                                                   # install from uv.lock
uv run pytest                                             # unit tests
uv run ask "..."                                          # ask the agent one question
uv run python -m eng_agent_evals.run_evals [--dataset regression|hard] [--subset N] [--repeat N] [--model ...]   # run the evals (calls the API)
```

## Conventions

- `physics.py` is the single source of truth: agent tools and the dataset both use it.
- Agent tools take inputs in m, m/s, m^3/s and °C; unit conversion is a separate tool. Calculation tools compute intermediate values internally rather than taking them from other tools.
- Tests never call real model APIs; use PydanticAI's `TestModel` or `FunctionModel`. Set `PYDANTIC_AI_NO_BANNER=1`.
- Add dependencies with `uv add`.
- One change per branch and pull request; do not push to `main`.
- No secrets in the repository (`.env`, `.logfire/` are ignored).
- `notes/` is local and ignored.
- Update `docs/methodology.md` when the evaluation design changes.
- After every full evaluation run, commit its file in `results/` and add an entry to `docs/run-log.md`: configuration, results with intervals, findings with their cause (from the per-case output), and actions. Keep finding IDs stable across runs and mark actions as verified by the run that confirms them. Smoke-test runs (`--subset`) are not logged.

## Required checks

`uv run pytest` passes before a pull request is opened.
