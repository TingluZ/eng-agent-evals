# eng-agent-evals

Evaluating an LLM agent that answers engineering questions about water flowing in pipes (pressure drop, Reynolds number, heat transfer coefficient) by calling calculation tools.

The point of the project is the evaluation, not the agent: deterministic checks on the numbers, trajectory checks on which tools the agent called and in what order, a calibrated LLM judge for its explanations, tracing with Logfire, and a CI gate that blocks changes that make the agent worse.

> Work in progress. Built in steps; see [docs/design-notes.md](docs/design-notes.md) for what each part does and why.

## Quick start

```bash
uv sync          # create the environment from uv.lock
uv run pytest    # check the reference physics
```

## Layout

| Path | What it is |
|---|---|
| `src/eng_agent_evals/physics.py` | Reference calculations (ground truth for the evals) |
| `src/eng_agent_evals/units.py` | Unit conversion with pint |
| `tests/` | pytest checks against textbook values |
| `docs/design-notes.md` | Design decisions, one section per step |
