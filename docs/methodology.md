# Methodology

## Ground truth

Expected answers for the evaluation dataset are computed by the reference functions in `src/eng_agent_evals/physics.py`. The agent's tools wrap the same functions. The evaluation therefore measures the agent's reasoning and orchestration: unit handling, property lookup, tool selection and ordering, input transcription, handling of missing information and reporting. It does not measure the correctness of the correlations themselves, which is covered by the unit tests below.

Scope: incompressible flow of liquid water (0–100 °C) in circular pipes, fully developed, SI units internally.

## Reference calculations

| Quantity | Method | Notes |
|---|---|---|
| Water properties ρ, μ, k, Pr | Tabulated saturated-liquid values at 10 °C intervals, linear interpolation | Out-of-range temperatures raise an error |
| Reynolds number | Re = ρVD/μ | |
| Flow regime | laminar Re < 2300; transitional 2300 ≤ Re < 4000; turbulent Re ≥ 4000 | |
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
