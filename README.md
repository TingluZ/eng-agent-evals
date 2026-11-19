# eng-agent-evals

Evaluating an LLM agent that answers engineering questions about water flowing in pipes (pressure drop, Reynolds number, heat transfer coefficient) by calling calculation tools.

The point of the project is the evaluation, not the agent: deterministic checks on the numbers, trajectory checks on which tools the agent called and in what order, a calibrated LLM judge for its explanations, tracing with Logfire, and a CI gate that blocks changes that make the agent worse.

> Work in progress.

## Quick start

```bash
uv sync          # create the environment from uv.lock
uv run pytest    # reference physics and agent wiring (no API calls)
```

Ask the agent a question (requires `OPENAI_API_KEY`; traces go to Logfire if configured):

```bash
uv run ask "Water at 20 °C flows at 2 m/s through 30 m of 50 mm smooth pipe. What is the pressure drop in kPa?"
```

Set `AGENT_MODEL` to change the model, e.g. `AGENT_MODEL=openai:gpt-5.4`.

## Layout

| Path | What it is |
|---|---|
| `src/eng_agent_evals/physics.py` | Reference calculations (ground truth for the evals) |
| `src/eng_agent_evals/units.py` | Unit conversion with pint |
| `src/eng_agent_evals/agent.py` | PydanticAI agent: tools, instructions, `Answer` schema |
| `src/eng_agent_evals/ask.py` | Command-line entry point with Logfire tracing |
| `tests/` | Reference physics against textbook values; agent wiring with fake models |
| `docs/methodology.md` | Ground truth, reference correlations, verification and agent design |
