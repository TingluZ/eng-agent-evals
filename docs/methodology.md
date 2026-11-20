# Methodology

## Ground truth

Expected answers for the evaluation dataset are computed by the reference functions in `src/eng_agent_evals/physics.py`. The agent's tools wrap the same functions. The evaluation therefore measures the agent's reasoning and orchestration: unit handling, property lookup, tool selection and ordering, input transcription, handling of missing information and reporting. It does not measure the correctness of the correlations themselves, which is covered by the unit tests below.

Scope: incompressible flow of liquid water (0–100 °C) in circular pipes, fully developed, SI units internally.

## Reference calculations

| Quantity | Method | Notes |
|---|---|---|
| Water properties ρ, μ, k, Pr | Tabulated saturated-liquid values at 10 °C intervals, linear interpolation | Out-of-range temperatures raise an error |
| Mean velocity | V = Q / (πD²/4) | |
| Reynolds number | Re = ρVD/μ | |
| Flow regime | laminar Re < 2300; transitional 2300 ≤ Re < 4000; turbulent Re ≥ 4000 | |
| Relative roughness | ε/D | |
| Darcy friction factor | Laminar: f = 64/Re. Otherwise: Colebrook-White, fixed-point iteration from the Haaland approximation | Colebrook in the transitional band is a conservative choice |
| Pressure drop | Darcy-Weisbach, Δp = f (L/D) ρV²/2 | |
| Nusselt number | Laminar: Nu = 3.66 (constant wall temperature). Otherwise: Gnielinski | |
| Heat transfer coefficient | h = Nu·k/D | |

## Verification

The reference functions are checked against independent sources (`tests/test_physics.py`):

- Colebrook solution residual below 1e-8; agreement with Haaland (2 %) for rough pipes and Blasius (3 %) for smooth pipes; Moody chart value at Re = 10⁵, ε/D = 10⁻⁴.
- Worked example: water at 20 °C, 2 m/s, 30 m of 50 mm commercial steel (ε = 0.045 mm) gives f ≈ 0.0218 and Δp ≈ 26.2 kPa.
- Gnielinski within 15 % of Dittus-Boelter at Re = 5×10⁴, Pr = 7.
- Internal consistency of the property table: Pr ≈ μc_p/k.
- Invalid inputs (non-positive dimensions, out-of-range temperature, incompatible units) raise descriptive errors, which the agent receives as retry prompts.

## Agent

The agent (`src/eng_agent_evals/agent.py`) is a PydanticAI `Agent` with eight tools and a typed output.

| Tool | Wraps |
|---|---|
| `convert_units` | `units.convert` (pint) |
| `water_properties` | `physics.water_properties` |
| `mean_velocity` | `physics.mean_velocity` (V = Q / (πD²/4)), for questions that give a flow rate |
| `reynolds_number` | `physics.reynolds_number`, `physics.flow_regime` |
| `relative_roughness` | `physics.relative_roughness` (ε/D), for questions that give absolute roughness |
| `friction_factor` | `physics.friction_factor` |
| `pressure_drop` | `physics.pressure_drop` |
| `heat_transfer_coefficient` | `physics.nusselt_number`, `physics.heat_transfer_coefficient` |

Tools take SI inputs, so unit conversion is an explicit, observable step in the trajectory. Invalid inputs raise `ModelRetry`: the error message is returned to the model, which may correct the call (up to two retries per tool).

The output is an `Answer`: `value`, `unit`, `regime`, `assumptions`, `missing_inputs` and `explanation`. When a required input is absent, the agent is instructed to return `value = null` and name the missing input rather than assume one; when no roughness is given, it assumes a smooth pipe and states the assumption. Both behaviours are evaluated.

The model is set by `AGENT_MODEL` (default `openai:gpt-5.4-mini`). Agent wiring is unit-tested without API calls using PydanticAI's `TestModel` and `FunctionModel`, including a scripted trajectory that must reproduce the reference pressure drop exactly.

## Evaluation design

### Dataset

`src/eng_agent_evals/dataset.py` generates 60 cases from seeded parameter sweeps and writes them to `data/cases.yaml`, which is committed so the cases can be reviewed without running code. A unit test regenerates the file and fails if the committed copy differs. Expected answers are computed with `physics.py`; values are stored in SI, with any unit requested in the question recorded separately.

| Case type | n | Content |
|---|---|---|
| `pressure_drop` | 20 | T 5–95 °C, D 10–100 mm, L 1–200 m; 4 laminar, 16 turbulent; 8 with a stated material, giving absolute roughness (4) or ε/D (4) |
| `heat_transfer` | 15 | 5 laminar, 10 turbulent |
| `reynolds_regime` | 10 | Reynolds number and regime only; diameter in mm or m |
| `trap` | 15 | 4 near or inside the transitional band; 4 imperial units or a flow rate in place of velocity; 3 with a required input missing; 2 with temperature outside 0–100 °C; 2 requesting kPa or psi |

Regular cases are sampled from Reynolds-number bands (300–1800 laminar, 8000–3×10⁵ turbulent) so that rounding the velocity cannot move them across a regime boundary; only the traps sit near 2300–4000.

Each case carries metadata: `case_type`, `tags` (units, roughness form, regime, trap kind) for slicing, and `expected_trajectory`. The trajectory is derived from the question: one `convert_units` call per non-SI input, `mean_velocity` when a flow rate is given, `relative_roughness` when absolute roughness is given, then the tools for the requested quantity, and a final `convert_units` when an output unit is requested. Cases with a missing input or an out-of-range temperature have no expected trajectory, since no single path is correct.

### Evaluators

The task returns the agent's `Answer` together with its tool calls (name, arguments, result, whether retried) read from the message history.

Assertions are of two kinds. **Correctness** checks concern the answer: value, requested unit, regime and missing inputs. A case passes when every applicable correctness check passes; this is the reported pass rate and the quantity intended for gating. **Process** checks concern how the answer was obtained: required tools called, final value and tool inputs traceable to tool outputs. They are reported as a separate rate. The distinction keeps a correct answer reached by a shortcut (for example a mm-to-m conversion done by the model) from counting the same as a wrong answer, while still recording it.

| Evaluator | Type | Definition | Rationale |
|---|---|---|---|
| `ValueWithinTolerance` | correctness | Value converted with pint to the expected unit is within 2 % of the reference; passes if both are null | Correlations are exact in code, so a larger error indicates a wrong input or step rather than rounding. Unit-aware so that 21.6 kPa matches 21 559 Pa |
| `ReportsRequestedUnit` | correctness | When the question names an output unit, the answer uses it | Separates "right number" from "answered the question asked" |
| `RegimeCorrect` | correctness | Regime equals the reference, including null when it cannot be computed | A correct value with the wrong regime indicates a lucky rather than sound answer |
| `FlagsMissingInputs` | correctness | If an input is missing: value is null and the input is named. Otherwise: nothing is flagged. Not applied to out-of-range cases | Refusing to guess is required behaviour; spurious flags are also errors |
| `ValueTraceableToTool` | process | The value, in SI, equals some successful tool output within 0.5 % | Detects numbers computed by the model rather than a tool. Comparison in SI allows unit scaling of the final value; 0.5 % allows rounding to three significant figures |
| `InputsTraceable` | process | Every numeric argument of every successful tool call equals, within 0.1 %, a number in the question or an output of an earlier call (zero allowed) | A final value can be a genuine tool output computed from wrong inputs, for example generic water properties substituted for the looked-up ones; this check finds substituted, re-typed and mentally computed inputs |
| `ToolCorrectness` (built-in) | process | Every tool in the expected trajectory was called; extra calls allowed | Required steps were taken |
| `TrajectoryMatch` (built-in) | score | F1 of the longest common subsequence of actual and expected tool names | See below |
| `ToolCallStats` | metric | Tool calls and retried calls per case | Efficiency and robustness, reported but not gated |

Unit strings are normalised before conversion so that common engineering notations (`W/m²K`, `-` for dimensionless) are read as intended.

The built-in trajectory evaluators read tool calls from the OpenTelemetry span tree, so the agent is instrumented during evaluation. Retried calls are excluded from the trajectory and counted separately.

### Trajectory scoring

Exact matching is too strict for this agent: observed runs repeat calls (for example `friction_factor` twice) and some steps have no fixed order (`mean_velocity` or `relative_roughness` may come before or after the property lookup). In-order LCS F1 penalises extra calls through precision and missing calls through recall while tolerating such reorderings partially. It is reported as a score, not gated; `ToolCorrectness` provides the pass/fail check on required steps.

### Statistics

Pass rates are reported overall, per case type and per evaluator, each with a Wilson 95 % score interval. With n between 10 and 60, the normal approximation is unreliable and can fall outside [0, 1] when rates are near 0 or 1; the Wilson interval does not. The lower bound of the interval on the correctness pass rate is the quantity intended for the CI gate.

`run_evals.py` also reports the mean trajectory score, mean tool calls per case, the retry rate (retried calls over all calls) and task errors, and saves the full report, per-case outputs and the git revision as JSON in `results/`. `--subset N` selects a fixed subset taken round-robin across case types.
