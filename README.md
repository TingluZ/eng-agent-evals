# eng-agent-evals

Evaluating an LLM agent that answers engineering questions about water flowing in pipes (pressure drop, Reynolds number, heat transfer coefficient) by calling calculation tools.

The point of the project is the evaluation, not the agent. Expected answers are computed by the same reference functions the tools wrap, so every check is deterministic. Correctness checks (value, unit, regime, missing inputs) decide whether a case passes. Process checks (trajectory, values and inputs traceable to tools) explain why it failed. Runs are traced with Logfire, repeated to measure variation, and reported with Wilson 95 % intervals. Unit tests run in CI on Linux and macOS. Planned next: an LLM judge for the explanations, calibrated against human labels, and a CI gate on evaluation results.

## Results

Two datasets:
- **regression set:** 60 generated cases, including traps for units, transitional flow, missing inputs and out-of-range temperatures;
- **hard set:** 30 hand-written cases needing engineering reading: geometry given indirectly, schedule-40 bores, roughness from the pipe material, pipes in series, an inverse question, wrong premises and other fluids.

Current agent (run 5), correct answers:

| Model | Regression set | Hard set |
|---|---|---|
| gpt-5.4-mini (3 repeats) | 170/180, 94 % [90 %, 97 %] | 62/90, 69 % [59 %, 78 %] |
| gpt-6.1-sol | 59/60, 98 % [91 %, 100 %] | 90/90 (3 repeats), 100 % [96 %, 100 %] |

How the agent got there. Each run changed one thing and was compared with the previous one; finding IDs (F = agent, E = evaluation) are stable across [`docs/run-log.md`](docs/run-log.md):

| Run | Change | What it showed |
|---|---|---|
| 1 | Baseline, gpt-5.4-mini | 53/60 correct. The agent ignored its own property lookup and used generic values (F1), and relabelled a unit without converting it, a 1000× error (F2). The evaluator could not see wrong tool *inputs* (E1) |
| 1, re-scored | Evaluation only: correctness separated from process; new `InputsTraceable` check | 57/60 correct, but only 31/60 clean: re-typed intermediate values (F7) were far more common than spotted by eye |
| 2 | Six instruction fixes | Three worked, three had no effect, and one caused a regression: "don't convert SI values" was generalised to millimetres (F3). Re-typed Reynolds numbers now produced errors of up to 98 % (F8) |
| 3 | Structural fix: tools compute properties, Re and f internally | F1, F7 and F8 impossible by construction. New regression: a required tool argument for a missing input was filled with 1 instead of being reported (F11) |
| 3b, 3c | Stronger models, same agent | gpt-6.1-sol 60/60 on every check: the run 3 failures depend on capability, and the set is saturated, bounding the error rate only below ≈ 5 % (E5) |
| 4a–4c | 30-case hard set, three models | 63 %, 79 %, 94 %, non-overlapping per repeat. The strongest model's failures were all one specification conflict: arithmetic was forbidden but no tool did it, so it declined while weaker models broke the rule and passed (F14) |
| 5 | `calculate` tool, with its operands checked for traceability | gpt-6.1-sol 90/90 on the hard set. gpt-5.4-mini gained, but used the calculator on invented values (F21) |

The evaluation itself was revised whenever a run showed it wrong (E1–E8): checks that could not see invented inputs, rejected legitimate answers, or rested on an ambiguous question.

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
uv run python -m eng_agent_evals.run_evals                 # the 60-case regression set
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
| `src/eng_agent_evals/gate.py` | CI gate: compares a results file with a committed baseline |
| `src/eng_agent_evals/hard_dataset.py` | Generates the 30-case hard set |
| `data/cases.yaml`, `data/hard_cases.yaml` | Generated regression and hard sets (committed for review) |
| `results/` | Saved evaluation reports |
| `tests/` | Reference physics, agent wiring, dataset, evaluators and runner; no API calls |
| `docs/methodology.md` | Ground truth, reference correlations, verification, agent and evaluation design |
| `docs/run-log.md` | Each evaluation run: results, failures and their causes, and the changes made |
