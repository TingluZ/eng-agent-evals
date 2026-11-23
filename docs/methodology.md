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

| Tool | Inputs | Wraps |
|---|---|---|
| `convert_units` | value, from unit, to unit | `units.convert` (pint) |
| `mean_velocity` | flow rate, diameter | `physics.mean_velocity` (V = Q / (πD²/4)), for questions that give a flow rate |
| `relative_roughness` | roughness, diameter | `physics.relative_roughness` (ε/D), for questions that give absolute roughness |
| `reynolds_number` | velocity, diameter, temperature | `physics.pipe_flow`: Re and regime |
| `pressure_drop` | velocity, diameter, length, temperature, ε/D | `physics.pipe_flow`, `physics.pressure_drop`: Δp with Re, regime and f |
| `heat_transfer_coefficient` | velocity, diameter, temperature, ε/D | `physics.pipe_flow`: h with Nu, Re and regime |
| `water_properties` | temperature | `physics.water_properties`; informational, not required by the other tools |
| `calculate` | arithmetic expression | `calculator.evaluate`: numbers, π, parentheses and + − × ÷ ^ only, for arithmetic no other tool does (an inside diameter from the outside diameter and wall, a sum of pressure drops, a mass flow rate divided by density) |

Tools take inputs in m, m/s, m³/s and °C; any other unit is converted with `convert_units`, so unit conversion is an explicit, observable step in the trajectory. The calculation tools take the quantities stated in the question and compute water properties, the Reynolds number and the friction factor internally (`physics.pipe_flow`). An earlier design exposed each step as a separate tool; evaluation runs 1 and 2 showed the model substituting generic property values and re-typing intermediate results between tools, including errors of an order of magnitude, and instructions did not prevent it (`docs/run-log.md`, F1, F7, F8). Removing the need to copy intermediate values removes that failure mode. The instructions forbid mental arithmetic; `calculate` makes that rule satisfiable for questions that need arithmetic between steps, which without it the most capable model declined to answer (F14). Invalid inputs raise `ModelRetry`: the error message is returned to the model, which may correct the call (up to two retries per tool). Parallel tool calls are disabled, since parallel calls were issued with placeholder arguments before the results they depended on existed (F6).

The output is an `Answer`: `value`, `unit`, `regime`, `assumptions`, `missing_inputs` and `explanation`. When a required input is absent, the agent is instructed to return `value = null` and name the missing input rather than assume one; when no roughness is given, it assumes a smooth pipe and states the assumption. Both behaviours are evaluated.

The model is set by `AGENT_MODEL` (default `openai:gpt-5.4-mini`). Agent wiring is unit-tested without API calls using PydanticAI's `TestModel` and `FunctionModel`, including a scripted trajectory that must reproduce the reference pressure drop exactly.

## Evaluation design

### Dataset

`src/eng_agent_evals/dataset.py` generates 60 cases from seeded parameter sweeps and writes them to `data/cases.yaml`, which is committed so the cases can be reviewed without running code. A unit test regenerates the file and fails if the committed copy differs. Expected values are rounded to 10 significant figures, far below the 2 % tolerance, so that last-digit differences in floating-point maths between operating systems do not make the file differ. Expected answers are computed with `physics.py`; values are stored in SI, with any unit requested in the question recorded separately.

| Case type | n | Content |
|---|---|---|
| `pressure_drop` | 20 | T 5–95 °C, D 10–100 mm, L 1–200 m; 4 laminar, 16 turbulent; 8 with a stated material, giving absolute roughness (4) or ε/D (4) |
| `heat_transfer` | 15 | 5 laminar, 10 turbulent |
| `reynolds_regime` | 10 | Reynolds number and regime only; diameter in mm or m |
| `trap` | 15 | 4 near or inside the transitional band; 4 imperial units or a flow rate in place of velocity; 3 with a required input missing; 2 with temperature outside 0–100 °C; 2 requesting kPa or psi |

Regular cases are sampled from Reynolds-number bands (300–1800 laminar, 8000–3×10⁵ turbulent) so that rounding the velocity cannot move them across a regime boundary; only the traps sit near 2300–4000.

Each case carries metadata: `case_type`, `tags` (units, roughness form, regime, trap kind) for slicing, and `expected_trajectory`. The trajectory is derived from the question: one `convert_units` call per non-SI input, `mean_velocity` when a flow rate is given, `relative_roughness` when absolute roughness is given, then the calculation tool for the requested quantity, and a final `convert_units` when an output unit is requested. Cases with a missing input or an out-of-range temperature have no expected trajectory, since no single path is correct.

### Hard set

`src/eng_agent_evals/hard_dataset.py` writes 30 hand-written cases to `data/hard_cases.yaml`. They were added when gpt-6.1-sol answered every regression case correctly (run 3c): with no failures in 60 cases, the regression set bounds the error rate only to below about 5 % (3/n), and can no longer distinguish strong models. The hard set is kept separate so regression results stay comparable across runs, and is run with `--dataset hard`.

| Category | n | What it requires |
|---|---|---|
| Geometry given indirectly | 6 | Inner diameter from outer diameter and wall thickness, from a radius, or from a nominal pipe size (schedule 40 bore); diameter in cm |
| Irrelevant information | 3 | Ignoring inlet pressure, elevation, wall temperature, pump power |
| Flow in other forms | 3 | Mass flow (kg/s), m³/h, L/min |
| Temperature in other forms | 2 | Kelvin; mean bulk temperature from inlet and outlet |
| Derived quantities | 4 | Friction factor, Nusselt number, pressure gradient (Pa/m), head loss (m of water) |
| Multi-step and inverse | 2 | Two pipe sections in series; velocity for a given pressure drop |
| Roughness from the material | 2 | Commercial steel and PVC with no roughness stated |
| Wrong or conflicting premise | 2 | A claim that turbulent flow is laminar; a datasheet Reynolds number that contradicts the inputs |
| Outside scope | 4 | Engine oil, air, a sand slurry, ethylene glycol: expected to decline |
| Missing input, unusual phrasing | 2 | "Hot water" with no temperature; numbers written in words |

Cases have no expected trajectory, since most admit several reasonable tool paths; trajectory evaluators are therefore not applied, and process checks still are. Where a case tests knowledge, a test confirms that the naive answer falls outside the 2 % tolerance (a smooth-pipe assumption for commercial steel: 18 % low; a 2.000 in bore for 2-inch schedule 40: 4 % high) and that a reasonable alternative falls inside it (PVC treated as smooth: 0.7 %).

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
| `InputsTraceable` | process | Every numeric argument of every successful tool call equals, within 0.1 %, a number in the question or an output of an earlier call (zero allowed) | A final value can be a genuine tool output computed from wrong inputs, for example generic water properties substituted for the looked-up ones; this check finds substituted, re-typed and mentally computed inputs. The numbers inside a `calculate` expression are checked the same way, apart from integer coefficients up to 10, so the calculator cannot introduce an untraceable value |
| `ToolCorrectness` (built-in) | process | Every tool in the expected trajectory was called; extra calls allowed | Required steps were taken |
| `TrajectoryMatch` (built-in) | score | F1 of the longest common subsequence of actual and expected tool names | See below |
| `ToolCallStats` | metric | Tool calls and retried calls per case | Efficiency and robustness, reported but not gated |

Unit strings are normalised before conversion so that common engineering notations (`W/m²K`, `-` for dimensionless) are read as intended.

The built-in trajectory evaluators read tool calls from the OpenTelemetry span tree, so the agent is instrumented during evaluation. Retried calls are excluded from the trajectory and counted separately.

### Trajectory scoring

Exact matching is too strict for this agent: observed runs repeat calls (for example `friction_factor` twice) and some steps have no fixed order (`mean_velocity` or `relative_roughness` may come before or after the property lookup). In-order LCS F1 penalises extra calls through precision and missing calls through recall while tolerating such reorderings partially. It is reported as a score, not gated; `ToolCorrectness` provides the pass/fail check on required steps.

### Statistics

Pass rates are reported overall, per case type and per evaluator, each with a Wilson 95 % score interval. With n between 10 and 60, the normal approximation is unreliable and can fall outside [0, 1] when rates are near 0 or 1; the Wilson interval does not. The lower bound of the interval on the correctness pass rate is the quantity intended for the CI gate.

`run_evals.py` also reports the mean trajectory score, mean tool calls per case, the retry rate (retried calls over all calls) and task errors, and saves the full report, per-case outputs and the git revision as JSON in `results/`. `--subset N` selects a fixed subset taken round-robin across case types. `--repeat N` runs every case N times to estimate run-to-run variation: the summary reports the pass rate of each repeat and the cases whose outcome differed between repeats. Rates pooled over repeats treat attempts at the same case as independent, which understates the width of their intervals; the per-repeat rates are the comparable figures.

## CI gate

`src/eng_agent_evals/gate.py` compares a results file with a committed baseline for the same model and dataset, and fails (exit status 1) when the agent has become worse. The main difficulty is run-to-run noise: with no code change, gpt-5.4-mini's correct count varies between 55 and 57 of 60 from one repeat to the next. A threshold on the headline rate alone would either fail at random or catch only large regressions. The gate therefore compares cases individually against the baseline.

| Rule | Fails the gate when | Justification |
|---|---|---|
| 1. No crashes | Any task error, any evaluator error, or a baseline case that was not run | Deterministic; no noise to allow for |
| 2. Stable cases stay passing | A case that passed every baseline attempt fails a majority of its attempts in this run, counting rechecks of failed cases | See below |
| 3. Headline rate | The baseline correct rate is above the Wilson 95 % upper bound of this run's first attempts | Catches a broad decline spread over many cases, which rule 2 may miss. Report-only until its false-alarm rate has been observed in CI |

**Rule 2 and rechecks.** In the saved runs with three repeats (runs 3, 4a–4c, 5a–5c), a case that passed two repeats failed the third in 4 of 688 checks: 0.58 %, Wilson 95 % interval [0.2 %, 1.5 %]. The CI baseline for gpt-5.4-mini has about 73 stable cases across both sets.
- Failing the gate on any single failed attempt would raise a false alarm in about 35 % of runs (66 % at the upper end of the interval).
- Re-running each failed case twice and requiring a majority of the three attempts to fail lowers the per-case rate to about 3p², or 0.01 %. That gives about 0.7 % per run (4.7 % at the upper end).

This assumes attempts are independent, which is reasonable for stable cases. Cases that are already inconsistent in the baseline are reported, not gated.

Power: a change that breaks a case completely is always caught. A case broken half the time is caught with probability 0.5 × 0.75 ≈ 38 % per run.

**Rule 3 power.** Only first attempts count, since rechecks are a biased sample of failures. One run of the 60-case regression set flags a drop from gpt-5.4-mini's baseline of 94.4 % to 88.3 % (53/60) or below, about 6 points. The 30-case hard set needs a drop of about 19 points. Smaller declines need a deliberate run with repeats, not CI.

**Known and flaky cases.** Cases that failed every baseline attempt (for example F11 for gpt-5.4-mini) are known failures. They never block, and the report says when one starts passing so the baseline can be updated. Cases that passed some baseline attempts are reported when they fail.

**Reported, never gated:** process checks, mean tokens per attempt and latency, compared with the baseline. A warning is shown when tokens or latency grow by more than 1.5×. This follows the correctness/process split used throughout: the gate blocks wrong answers, and process checks explain them.

**Baselines.** `baselines/<model>-<dataset>.json` is built with `gate baseline` from a logged run with at least three repeats. With fewer repeats a flaky case looks stable, and rule 2 would gate on it. A baseline holds per-case pass counts over first attempts, known failures annotated with their finding IDs, process-check rates and mean usage. Rechecks are excluded, since they sample only failures. A baseline changes only in a reviewed pull request, together with a run-log entry, so that moving the reference point is visible in the history in the same way as an agent change.

The first baselines are for gpt-5.4-mini, from runs 5b (hard) and 5c (regression):
- **Why mini:** it is the cheapest model (≈ $0.30 per gated pull request with rechecks), and its remaining failures leave room to detect regressions.
- **Why not gpt-6.1-sol:** it would give a sharper signal, since it scores 100 %, but costs ≈ $3.30 per pull request while the target model is undecided.
- **Caveat:** run 5c predates the E8 rewording of four regression questions. The regression baseline should be rebuilt from the first full three-repeat run after it.

**Validation on past runs.** Gating run 5b (hard set, `calculate` tool) against a baseline built from run 4a (before it) fails on `hard-multistep-01`: 3/3 → 1/3, finding F22, which run 5 found by reading the per-case output. The same check lists the two known failures that the change fixed (`hard-geometry-01`, `hard-flow-02`). Gating run 5c against run 3 on the regression set passes. The rise in model-side unit conversions (F3) appears in the process rows but does not block.

**Where it runs** (`.github/workflows/evals.yml`). The evaluations call a paid API, so the workflow runs:
- on pull requests labelled `run-evals` that change `src/`, `data/`, `baselines/` or the dependencies. Pull requests that change only documentation or tests cost nothing;
- weekly on `main`, with three repeats, to detect changes in the model behind the API, which code review cannot catch;
- on demand.

Each run evaluates both datasets with `--recheck-failures 2` and gates each against its baseline. The gate's report goes to the job summary, and the results files are kept as workflow artifacts for 90 days. Gate runs are not entered in the run log: it records deliberate experiments, and a gate run that changes a baseline is logged with that change.

Security: the API key is a repository secret. The workflow uses the `pull_request` event, never `pull_request_target`, and skips pull requests from forks, so untrusted code never runs with the key. Workflow inputs are validated and passed to the shell through environment variables. Permissions are read-only.

**Limits.** One repeat of 60 + 30 cases catches a case that breaks, or a headline drop of about 6 points (regression set) or 19 points (hard set). A smaller decline needs a deliberate run with repeats. The gate protects against regressions in the behaviour the datasets cover, and says nothing about cases they do not contain.
