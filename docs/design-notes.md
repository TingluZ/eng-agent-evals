# Design notes

One section per build step: what was built, why, and what to be ready to explain in an interview.

---

## Step 1: Foundations

### What was built
- **`physics.py`**: reference calculations for water flowing in a circular pipe:
  - water properties from 0 to 100 °C, interpolated from a textbook table
  - Reynolds number and flow regime
  - Darcy friction factor
  - Darcy-Weisbach pressure drop
  - Nusselt number
  - convective heat transfer coefficient
- **`units.py`**: unit conversion with pint.
- **`tests/`**: 32 pytest checks.
- **`pyproject.toml` + `uv.lock`**: the environment.

### Why the ground truth lives in code
The evals need an expected answer for every question. Labelling by hand is slow and error-prone, and it was the bottleneck at CPI. Here the expected answers are computed by `physics.py`. The agent's tools call the *same* functions.

So the evals do **not** test whether the physics is right. pytest does that. The evals test whether the **agent** reasoned correctly:
- Did it convert units before calculating?
- Did it look up properties at the right temperature?
- Did it pass the right numbers to each tool, in the right order?
- Did it report the answer properly?

That is exactly what goes wrong with LLM agents in practice. Keeping these two jobs separate is the first design choice to explain: **unit tests check the code, evals check the model.**

### Physics choices, and why

| Choice | Reason |
|---|---|
| Laminar below Re = 2300, turbulent from 4000, transitional in between | Standard textbook limits. The transitional band is where an engineer should flag uncertainty, so it becomes a trap case in the eval |
| Laminar friction factor f = 64/Re | Exact for fully developed laminar pipe flow |
| Turbulent friction factor: Colebrook-White, solved iteratively, starting from Haaland | Colebrook is the standard implicit equation behind the Moody chart. Haaland is an explicit approximation, used as a good first guess so the iteration converges in a few steps |
| Colebrook also used in the transitional band | A conservative engineering choice, documented as such rather than hidden |
| Laminar Nusselt number = 3.66 | Fully developed flow, constant wall temperature |
| Turbulent Nusselt number: Gnielinski | More accurate than Dittus-Boelter (typically within ~10 % rather than ~25 %) and valid down to lower Re. Dittus-Boelter is used only as a sanity check in the tests |
| Water property table, linear interpolation | Simple and transparent. The exact values matter less than consistency, because the agent and the reference use the same table |

### How the tests check the ground truth
Each function is checked against something independent of itself:
- **Colebrook** is checked by plugging the answer back into its own equation (residual < 1e-8), against Haaland for a rough pipe, against Blasius for a smooth pipe, and against a Moody chart reading.
- **Pressure drop** is checked against a worked hand calculation: water at 20 °C, 2 m/s, 30 m of 50 mm commercial steel pipe gives about 26 kPa.
- **Gnielinski** is checked against Dittus-Boelter: within 15 % at Re = 5×10⁴.
- **The water table** is checked for internal consistency: Pr ≈ μ·cp/k.
- **Bad inputs** (negative diameter, temperature outside 0–100 °C, incompatible units) raise clear errors. This matters later: the agent receives the error message and can correct itself.

> Honest note: two of my first hand-calculated test values were wrong (I mis-estimated the Reynolds number and the friction factor in the worked example). The tests caught it, and I corrected the *expected* values after checking them against Haaland. That is what tests on the ground truth are for.

### Tools in this step
- **uv** replaces pip + venv + pip-tools. `pyproject.toml` lists the dependencies, `uv.lock` pins the exact versions, and `uv sync` recreates the same environment on any machine, including CI.
- **pint** parses unit strings ("L/min", "inch", "degF") and converts between them. It refuses incompatible conversions (metres to pascals), which turns a silent unit bug into a visible error. Temperatures are handled as absolute values, so 68 °F becomes 20 °C rather than a temperature difference.
- **pytest** finds functions named `test_*` and runs them. `pytest.approx` compares floating-point numbers with a tolerance, and `parametrize` runs one test over several inputs.

### Questions you might get
- *Why not let the LLM compute the numbers directly?* LLMs make arithmetic slips and unit errors. Tools make each step deterministic and inspectable, which is what you want in engineering.
- *Why 2300 and 4000?* Conventional limits. Real transition depends on inlet conditions and disturbances, which is why the transitional band is flagged rather than trusted.
- *How do you know your ground truth is right?* Independent checks: published approximations, a Moody chart reading, a hand calculation, and the equation's own residual.
