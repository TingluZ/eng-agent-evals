# Evaluation run log

One entry per full evaluation run, newest last. Each entry records the configuration, the headline result, the failures found (with their cause, read from the saved per-case output), and the change made in response. Findings keep their ID across runs so a fix can be traced to the run that verifies it.

Result files are in `results/`; intervals are Wilson 95 %.

---

## Run 1: baseline

| | |
|---|---|
| Date | 20 Nov 2026 |
| Model | `openai:gpt-5.4-mini` |
| Code | `acdc670` (Step 3 evaluators, agent unchanged from Step 2 apart from the `mean_velocity` and `relative_roughness` tools) |
| Cases | 60 (full dataset) |
| Result file | `results/20261120T165054Z-openai_gpt-5.4-mini.json` |
| Cost | ≈ $0.29 total, ≈ $0.005 per case |

### Results

| | Passed | Rate | 95 % CI |
|---|---|---|---|
| **Overall** | 53/60 | 88 % | [78 %, 94 %] |
| heat_transfer | 15/15 | 100 % | [80 %, 100 %] |
| pressure_drop | 18/20 | 90 % | [70 %, 97 %] |
| reynolds_regime | 7/10 | 70 % | [40 %, 89 %] |
| trap | 13/15 | 87 % | [62 %, 96 %] |
| tool_correctness | 50/55 | 91 % | [80 %, 96 %] |
| ValueWithinTolerance | 57/60 | 95 % | [86 %, 98 %] |
| RegimeCorrect | 60/60 | 100 % | [94 %, 100 %] |
| FlagsMissingInputs | 58/58 | 100 % | [94 %, 100 %] |
| ValueTraceableToTool | 54/55 | 98 % | [90 %, 100 %] |
| reports_requested_unit | 2/2 | 100 % | [34 %, 100 %] |

Mean trajectory score 0.787; mean tool calls per case 6.4; retry rate 7.0 % (27 of 384 calls); no task errors.

Per-case-type intervals overlap, so differences between case types are not significant at this sample size.

### Findings

| ID | Type | Cases | Observation | Cause |
|---|---|---|---|---|
| F1 | Agent: wrong answer | `reynolds_regime-06`, `-08` | Re 30–40 % low; regime still correct | `water_properties` was called, but `reynolds_number` was given generic values (ρ = 1000, μ = 0.001) instead of its output |
| F2 | Agent: wrong answer | `pressure_drop-06` | Value 1000× low | Tool returned 53 799 Pa; answer reported 53.799 with unit "Pa" (scaled to kPa without changing the unit). Absolute roughness (1.5×10⁻⁶ m) was also passed as ε/D, skipping `relative_roughness` |
| F3 | Agent: rule violation, correct answer | `pressure_drop-12`, `reynolds_regime-09`, `trap-transition-02`, `trap-units-03` | `tool_correctness` failed | mm → m, and in one case ε/D, computed by the model instead of a tool |
| F4 | Agent: inefficiency | 82 calls across most cases | Identity conversions (`convert_units(85, "m", "m")`) | Instruction "convert every input to SI with `convert_units`" followed literally; main reason for the 0.787 trajectory score |
| F5 | Agent: tool misuse | 17 calls | `mean_velocity` called when velocity was given, including with a velocity passed as flow rate (776 m/s result, then ignored) | Tool description does not say when not to use it |
| F6 | Agent: inefficiency | 24 of 27 retries | Calls issued with placeholder zeros, e.g. `pressure_drop(friction_factor=0, …)` | Parallel tool calls emitted before dependent results exist; caught by `ModelRetry` |
| F7 | Agent: transcription drift | `pressure_drop-12`, `trap-units-03` | `friction_factor` given Re = 9145.7 (tool returned 9132.3) and 40 838 (tool returned 41 046.8) | Numbers re-typed rather than copied; within tolerance here |
| E1 | Eval gap | `reynolds_regime-06` | `ValueTraceableToTool` passed a wrong answer | Checks only that the final value came from a tool, not that the tool's inputs did (F1, F7) |
| E2 | Eval design | F3 cases | Harmless shortcuts fail a case exactly as a 1000× error does | Correctness and process checks are combined in one pass/fail |

### Actions

| ID | Action | Status |
|---|---|---|
| F1, F7, E1 | Add `InputsTraceable`: every numeric tool argument must be traceable to the question or an earlier tool output | Done (eval change); run 1 re-scored below |
| E2 | Separate correctness assertions (decide a pass, gated) from process assertions (reported) | Done (eval change); run 1 re-scored below |
| F1 | Instruction: use `water_properties` output exactly; never substitute typical values | Not effective in run 2 |
| F2 | Instruction: report in the SI unit of the producing tool; convert with `convert_units` only when another unit is requested | Verified in run 2 (no recurrence) |
| F4 | Instruction: convert only inputs not already in SI | Verified in run 2; caused regression F3 |
| F5 | `mean_velocity` description: only when a volumetric flow rate is given | Verified in run 2 |
| F6 | Instruction: no tool calls with placeholder values. Disabling parallel tool calls was not chosen, as it adds a model request per tool call | Not effective in run 2 |
| F7 | Instruction: pass numbers between tools exactly as returned | Not effective in run 2; errors larger (F8) |

---

## Run 1, re-scored: evaluation change only

The run 1 outputs re-scored offline under the revised evaluation (correctness/process split and `InputsTraceable`), with no new model calls. The agent is unchanged, so differences from run 1 are due to the evaluation alone. This is the baseline for run 2.

| | Passed | Rate | 95 % CI |
|---|---|---|---|
| **Correct answers** (gated) | 57/60 | 95 % | [86 %, 98 %] |
| Process clean | 31/60 | 52 % | [39 %, 64 %] |
| `InputsTraceable` | 31/59 | 53 % | [40 %, 65 %] |

The three incorrect cases are F1 (2) and F2 (1). The previous 53/60 counted the F3 shortcuts as failures; they are now process failures.

### Findings

| ID | Type | Cases | Observation |
|---|---|---|---|
| F1 | Confirmed by new check | `reynolds_regime-06`, `-08` | `InputsTraceable` flags `density=1000`, `viscosity=0.001` directly |
| F5 | Wider than seen in run 1 | 14 cases | A flow rate computed by the model (V·A) and passed to `mean_velocity`, when the question gave a velocity |
| F7 | Wider than seen in run 1 | 10 cases | Values rounded or re-typed between tools: Reynolds numbers in 9 cases (e.g. 13 500 for 13 466.8; 500 for 502.5; 9145.7 for 9132.3), a friction factor in 1. Within value tolerance, but not copied |
| F3 | Confirmed by new check | `pressure_drop-12`, `reynolds_regime-09`, `trap-transition-02` | Diameters converted by the model (`diameter=0.065` from "65 mm") |

---

## Run 2: instruction changes

| | |
|---|---|
| Date | 20 Nov 2026 |
| Model | `openai:gpt-5.4-mini` |
| Code | `bdba754` (revised instructions and `mean_velocity` description; evaluation as in run 1 re-scored) |
| Cases | 60 (full dataset) |
| Result file | `results/20261120T175851Z-openai_gpt-5.4-mini.json` |
| Cost | ≈ $0.27 total |

### Results, against run 1 re-scored

| | Run 1 (re-scored) | Run 2 |
|---|---|---|
| **Correct answers** | 57/60, 95 % [86 %, 98 %] | 54/60, 90 % [80 %, 95 %] |
| Process clean | 31/60, 52 % [39 %, 64 %] | 24/60, 40 % [29 %, 53 %] |
| `tool_correctness` | 50/55 | 24/55 |
| `InputsTraceable` | 31/59 | 24/59 |
| Mean tool calls per case | 6.4 | 4.1 |
| Mean trajectory score | 0.787 | 0.856 |
| Retry rate | 7.0 % (27/384) | 8.9 % (22/247) |

The intervals on the correctness rate overlap; the change in pass rate is not significant. The finding-level counts below change by much more than run-to-run noise would explain, but each run is a single sample.

### Finding-level counts

| ID | Measure | Run 1 | Run 2 | Outcome |
|---|---|---|---|---|
| F4 | Identity conversions (`m` → `m`) | 82 calls | 1 call | Fixed |
| F5 | `mean_velocity` calls when a velocity was given | 25 calls | 1 call | Fixed |
| F2 | Value scaled without changing the unit | 1 case | 0 | Not recurring |
| F3 | Cases failing `tool_correctness` for a missing `convert_units` (mm converted by the model) | 3 cases | 30 cases | **Regression** |
| F1 | Generic properties passed to `reynolds_number` | 3 cases | 5 cases | Not fixed |
| F6 | Retries from placeholder arguments | 24 | 19 | Not fixed |

### Findings

| ID | Type | Cases | Observation | Cause |
|---|---|---|---|---|
| F3 | Regression | 30 cases | "50 mm" passed as `diameter=0.05` without `convert_units`; 30 of the 31 `tool_correctness` failures | The F4 instruction ("do not convert values already in SI") was generalised by the model to conversions it considers trivial |
| F1 | Persisting wrong answer | `pressure_drop-07`, `-08` (wrong); 5 cases in total | ρ = 1000 and μ = 0.001 still substituted; 16 % and 5 % value errors | The explicit instruction did not change the behaviour. Naming the values in the instruction may reinforce them; not tested |
| F8 | New wrong answer | `heat_transfer-10`, `-14` | Reynolds number re-typed with the wrong magnitude: 107 492 passed as 10 800, 283 695 as 2 840; h 87 % and 98 % low | Same mechanism as F7 (numbers re-typed between tools), now with large errors |
| F9 | New wrong answer | `trap-missing-01` | Pipe length listed as missing, but a value computed with an assumed length of 1 m | Refusal regressed; in run 1 the agent returned null |
| F10 | New wrong answer | `trap-units-03` | Declined to answer: "US gal/min" not recognised by the unit registry after two attempts; a length was also passed to `mean_velocity` as a flow rate | pint does not parse "US gal"; in run 1 the agent recovered by trying "gal/min" |

### Actions

| ID | Action | Status |
|---|---|---|
| F3 | Instruction defines SI as the seven base units and coherent derived units (no prefix or numerical factor), with °C as the temperature exception; every other unit, including prefixed units such as mm, goes through `convert_units` | Not effective in run 3 |
| F1, F7, F8 | Structural fix: calculation tools take velocity, diameter, temperature (and length, ε/D) and compute properties, Re and f internally (`physics.pipe_flow`); `friction_factor` tool removed; property instructions removed | Verified in run 3; caused regression F11 |
| F6 | Parallel tool calls disabled (`parallel_tool_calls=False`); placeholder instruction removed | Verified in run 3; requests per case fell |
| F10 | `convert_units` accepts "US gal", "imperial gal" and "gpm" | Verified in run 3 (conversion succeeds; see F12) |
| — | `run_evals --repeat N`; per-repeat pass rates and cases with inconsistent outcomes | Done; used in run 3 |

---

## Run 3: tools compute intermediate values

| | |
|---|---|
| Date | 20 Nov 2026 |
| Model | `openai:gpt-5.4-mini` |
| Code | `22cc7be` (calculation tools take the question's inputs and compute properties, Re and f internally; SI defined by rule in the instructions; parallel tool calls disabled; gallon spellings) |
| Cases | 60 × 3 repeats = 180 attempts |
| Result file | `results/20261120T185653Z-openai_gpt-5.4-mini.json` |
| Cost | ≈ $0.5 total, ≈ $0.003 per attempt (estimated from the per-case costs shown in the report) |

### Results

| | Run 1 (re-scored) | Run 2 | Run 3 (pooled over 3 repeats) |
|---|---|---|---|
| **Correct answers** | 57/60, 95 % | 54/60, 90 % | 167/180, 93 % [88 %, 96 %] |
| Regular cases (not traps) | 42/45 | 41/45 | 134/135 |
| Traps | 15/15 | 13/15 | 33/45 |
| Missing-input traps | 3/3 | 2/3 | **0/9** |
| Process clean | 31/60, 52 % | 24/60, 40 % | 76/180, 42 % |
| Model requests per case | 4.07 | 3.73 | 2.89 |
| Input tokens per case | 4373 | 4317 | 3422 |
| Mean latency per case | 7.7 s | 7.0 s | 5.0 s |
| Retries from placeholder arguments | 24 | 19 | 1 |

Correct answers per repeat: 56/60, 56/60, 55/60. One case (`pressure_drop-19`) changed outcome between repeats. Run-to-run variation is small compared with the differences between runs, so the changes below are systematic.

Pooled intervals over repeats are narrower than the evidence supports (attempts at the same case are correlated); compare the per-repeat rates (56/60 [84 %, 97 %], 56/60 [84 %, 97 %], 55/60 [82 %, 96 %]) with the single-run rates of runs 1 and 2.

### Finding-level outcomes

| ID | Measure | Run 2 | Run 3 | Outcome |
|---|---|---|---|---|
| F1 | Generic properties passed to a tool | 5 cases | 0 (no property arguments exist) | Fixed by construction |
| F7, F8 | Intermediate values re-typed between tools | 10+ cases, 2 wrong by an order of magnitude | 0 (no intermediate arguments exist) | Fixed by construction |
| F6 | Retries from placeholder arguments | 19 | 1 | Fixed |
| F10 | "US gal/min" not converted | 1 case | 0 | Fixed |
| F3 | `tool_correctness` failures from model-side mm conversion | 30/60 cases | 77/180 attempts | Not fixed by the rule-based instruction; answers unaffected |

### Findings

| ID | Type | Cases | Observation | Cause |
|---|---|---|---|---|
| F11 | **Regression**, wrong answer | `trap-missing-01`, `-02`, `-03`: 9/9 attempts | The missing input is invented as 1 (length = 1 m, velocity = 1 m/s, diameter = 1 m), passed to the tool, and not flagged; `missing_inputs` is empty | Each calculation tool now requires every input as an argument. With no partial calculation available, the model fills the required argument rather than declining. In runs 1–2 it could compute intermediate steps and stop |
| E3 | Eval gap | `trap-missing-01` | `InputsTraceable` passed `length=1` | Traceability matches numbers by value, not by quantity: "1 m/s" in the question supplies a 1 |
| F12 | Wrong answer | `trap-units-03`: 3/3 attempts | Velocity computed by the model from the flow rate (0.742 m/s; correct 0.934 m/s), skipping `mean_velocity`; 35 % error | Helper tool skipped; possibly encouraged by "use the calculation tools directly" |
| F13 | Wrong answer, intermittent | `pressure_drop-19`: 1/3 attempts | "68 °C" converted as if °F, giving 20 °C | Misreading of the question's unit |

### Actions

| ID | Action | Status |
|---|---|---|
| F11 | Proposed: make length, velocity and diameter optional tool arguments; a missing one raises a retry instructing the model to report it in `missing_inputs` | Proposed |
| E3 | Record the limitation; consider matching numbers with their units | Proposed |
| F12 | Proposed: calculation tools accept a volumetric flow rate as an alternative to velocity | Proposed |
| F3 | Decision needed: enforce through tool design, or accept model-side prefix conversion as a reported, ungated deviation | Open |

---

## Runs 3b and 3c: stronger models on the run 3 agent

Same agent code, instructions and dataset as run 3 (`src/` and `data/` unchanged since `22cc7be`); only the model differs. One repeat each.

| | |
|---|---|
| Date | 20 Nov 2026 |
| Models | 3b `openai:gpt-5.4`; 3c `openai:gpt-6.1-sol` |
| Result files | `results/20261120T191639Z-openai_gpt-5.4.json`, `results/20261120T192351Z-openai_gpt-6.1-sol.json` |
| Cost (estimated from tokens and list prices) | 3b ≈ $1.30 (≈ $0.022 per case); 3c ≈ $1.00 (≈ $0.017 per case); run 3 ≈ $0.003 per attempt |

### Results

| | gpt-5.4-mini (run 3, per repeat) | gpt-5.4 (3b) | gpt-6.1-sol (3c) |
|---|---|---|---|
| **Correct answers** | 55–56/60, 92–93 % | 59/60, 98 % [91 %, 100 %] | **60/60, 100 % [94 %, 100 %]** |
| Process clean | 76/180 pooled, 42 % | 55/60, 92 % [82 %, 96 %] | **60/60, 100 % [94 %, 100 %]** |
| Missing-input traps | 0/9 | 2/3 (see below) | 3/3 |
| `trap-units-03` (flow rate) | 0/3 | 1/1 | 1/1 |
| Model-side mm conversion (F3) | 77/180 | 3/60 | 0/60 |
| Mean trajectory score | 0.810 | 0.975 | 1.000 |
| Requests per case | 2.89 | 3.18 | 3.23 |
| Mean latency per case | 5.0 s | 6.5 s | 8.4 s |

### Findings

| ID | Observation |
|---|---|
| F11, F12, F3 | Not reproduced by gpt-6.1-sol; F11 and F12 not reproduced by gpt-5.4; F3 reduced to 3/60 with gpt-5.4. The run 3 failures are capability-dependent: the same tools and instructions are sufficient for the stronger models |
| E4 | gpt-5.4's one incorrect case (`trap-missing-01`) declined correctly and named the pipe length, but reported no regime; the reference expects "turbulent", as Re is computable from the given velocity and diameter. Arguably too strict for a refusal case; to be reviewed |
| E5 | gpt-6.1-sol saturates the suite: 60/60 on every check. With no failures, the evaluation bounds its error rate only to below ≈ 5 % (rule of three: 3/60); it cannot distinguish it from any model that is better than that |

### Actions

| ID | Action | Status |
|---|---|---|
| E5 | Extend the dataset with harder and more realistic cases so the strongest model is no longer at the ceiling | Proposed |
| — | Decide the agent's target model from cost, latency and the required pass rate, rather than continuing to adapt the agent to gpt-5.4-mini | Open |
