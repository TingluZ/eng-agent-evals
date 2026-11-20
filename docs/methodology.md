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
