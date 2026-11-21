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

Run the evaluation suite (calls the API for every case; results are saved to `results/`):

```bash
uv run python -m eng_agent_evals.run_evals                 # all 60 cases
uv run python -m eng_agent_evals.run_evals --subset 20     # fixed stratified subset
uv run python -m eng_agent_evals.run_evals --model openai:gpt-5.4
uv run python -m eng_agent_evals.run_evals --repeat 3      # every case three times, to measure run-to-run variation
uv run python -m eng_agent_evals.run_evals --dataset hard  # the 30-case hard set
```

## Layout

| Path | What it is |
|---|---|
| `src/eng_agent_evals/physics.py` | Reference calculations (ground truth for the evals) |
| `src/eng_agent_evals/units.py` | Unit conversion with pint |
| `src/eng_agent_evals/calculator.py` | Plain arithmetic for the `calculate` tool |
| `src/eng_agent_evals/agent.py` | PydanticAI agent: tools, instructions, `Answer` schema |
| `src/eng_agent_evals/ask.py` | Command-line entry point with Logfire tracing |
| `src/eng_agent_evals/dataset.py` | Generates the evaluation cases from parameter sweeps |
| `src/eng_agent_evals/evaluators.py` | Deterministic and trajectory evaluators |
| `src/eng_agent_evals/run_evals.py` | Runs the suite, summarises pass rates with Wilson intervals |
| `src/eng_agent_evals/hard_dataset.py` | Generates the 30-case hard set |
| `data/cases.yaml`, `data/hard_cases.yaml` | Generated regression and hard sets (committed for review) |
| `results/` | Saved evaluation reports |
| `tests/` | Reference physics, agent wiring, dataset, evaluators and runner; no API calls |
| `docs/methodology.md` | Ground truth, reference correlations, verification, agent and evaluation design |
| `docs/run-log.md` | Each evaluation run: results, failures and their causes, and the changes made |
