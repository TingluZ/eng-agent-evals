"""Generate the hard evaluation set.

Thirty hand-written cases aimed at failures the regression set does not reach:
inputs given indirectly (outer diameter and wall thickness, radius, nominal pipe
size, mass flow), irrelevant numbers, derived quantities the tools do not return
directly, multi-step and inverse problems, roughness that must be known rather
than stated, wrong premises, fluids outside the agent's scope,
and unusual phrasing. Expected answers are computed with physics.py.

The set is kept separate from data/cases.yaml so that regression results stay
comparable across runs. No expected trajectory is recorded: most cases have
more than one reasonable tool path.

    uv run python -m eng_agent_evals.hard_dataset
"""

from __future__ import annotations

import math
from pathlib import Path

from pydantic_evals import Case, Dataset

from . import physics, units
from .dataset import SI_UNIT, CaseMetadata, ExpectedAnswer, Quantity

HARD_DATASET_PATH = Path(__file__).resolve().parents[2] / "data" / "hard_cases.yaml"

G = 9.80665  # standard gravity [m/s^2]
INCH = 0.0254


def _flow(velocity: float, diameter: float, temperature_c: float, roughness: float = 0.0) -> physics.PipeFlow:
    return physics.pipe_flow(velocity, diameter, temperature_c, physics.relative_roughness(roughness, diameter))


def _dp(velocity: float, diameter: float, length: float, temperature_c: float, roughness: float = 0.0) -> float:
    flow = _flow(velocity, diameter, temperature_c, roughness)
    return physics.pressure_drop(flow.friction_factor, length, diameter, flow.water.density, velocity)


def _case(
    name: str,
    quantity: Quantity,
    question: str,
    value: float | None,
    regime: physics.Regime | None,
    tags: list[str],
    *,
    requested_unit: str | None = None,
    missing_inputs: list[str] | None = None,
) -> Case[str, ExpectedAnswer, CaseMetadata]:
    return Case(
        name=name,
        inputs=question,
        expected_output=ExpectedAnswer(
            quantity=quantity,
            value=value,
            unit=SI_UNIT[quantity],
            regime=regime,
            missing_inputs=missing_inputs or [],
            requested_unit=requested_unit,
        ),
        metadata=CaseMetadata(case_type="hard", expected_trajectory=None, tags=tags),
    )


def _dp_case(name, question, v, d, length, t, roughness=0.0, tags=(), **kw):
    regime = _flow(v, d, t, roughness).regime
    return _case(name, "pressure_drop", question, _dp(v, d, length, t, roughness), regime, list(tags), **kw)


def _h_case(name, question, v, d, t, tags=(), **kw):
    flow = _flow(v, d, t)
    return _case(name, "heat_transfer", question, flow.heat_transfer_coefficient, flow.regime, list(tags), **kw)


def _re_case(name, question, v, d, t, tags=()):
    flow = _flow(v, d, t)
    return _case(name, "reynolds_number", question, flow.reynolds_number, flow.regime, list(tags))


def _velocity_for_pressure_drop(target: float, diameter: float, length: float, temperature_c: float) -> float:
    lo, hi = 1e-3, 20.0
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _dp(mid, diameter, length, temperature_c) < target else (lo, mid)
    return (lo + hi) / 2


def build_hard_dataset() -> Dataset[str, ExpectedAnswer, CaseMetadata]:
    c = units.convert
    w20 = physics.water_properties(20)
    cases = [
        # --- geometry given indirectly ------------------------------------------
        # Inner diameter = outer diameter - 2 x wall thickness = 50 mm.
        _dp_case(
            "hard-geometry-01",
            "Water at 20 °C flows at 1.5 m/s through 40 m of smooth tube with an outside diameter of "
            "60 mm and a wall thickness of 5 mm. What is the pressure drop?",
            1.5, 0.050, 40, 20, tags=["hard:geometry", "roughness:smooth"],
        ),
        # Radius, not diameter: D = 25 mm.
        _h_case(
            "hard-geometry-02",
            "Water at 40 °C flows at 0.8 m/s inside a tube with an internal radius of 12.5 mm. "
            "What is the convective heat transfer coefficient?",
            0.8, 0.025, 40, tags=["hard:geometry"],
        ),
        # Nominal pipe size: 2-inch schedule 40 has a 2.067 in bore, not 2 in.
        _dp_case(
            "hard-geometry-03",
            "Water at 15 °C flows at 2 m/s through 50 m of 2-inch schedule 40 commercial steel pipe "
            "(absolute roughness 0.045 mm). What is the pressure drop?",
            2.0, 2.067 * INCH, 50, 15, 0.045e-3, tags=["hard:geometry", "hard:knowledge", "roughness:absolute"],
        ),
        # 1-inch schedule 40: 1.049 in bore.
        _re_case(
            "hard-geometry-04",
            "What is the Reynolds number for water at 60 °C flowing at 0.5 m/s in a 1-inch schedule 40 pipe?",
            0.5, 1.049 * INCH, 60, tags=["hard:geometry", "hard:knowledge"],
        ),
        # Diameter in cm, answer requested in kW/(m^2·K).
        _h_case(
            "hard-geometry-05",
            "Water at 70 °C flows at 1.2 m/s through a tube of 3.2 cm bore. "
            "Give the heat transfer coefficient in kW/(m²·K).",
            1.2, 0.032, 70, tags=["hard:geometry", "hard:output_unit"], requested_unit="kW/(m^2*K)",
        ),
        # Imperial tube: 1 in outside diameter, 0.065 in wall, so a 0.870 in bore.
        _dp_case(
            "hard-geometry-06",
            "Water at 25 °C flows at 1 m/s through 10 m of smooth tubing with a 1 inch outside diameter "
            "and a 0.065 inch wall. What is the pressure drop?",
            1.0, (1 - 2 * 0.065) * INCH, 10, 25, tags=["hard:geometry", "units:imperial", "roughness:smooth"],
        ),
        # --- irrelevant numbers -------------------------------------------------
        _dp_case(
            "hard-distractor-01",
            "A pump delivers water at 20 °C with an inlet gauge pressure of 350 kPa. The water flows at "
            "2.5 m/s through 120 m of 40 mm commercial steel pipe (absolute roughness 0.045 mm) that rises "
            "5 m in elevation. What is the frictional pressure drop in bar?",
            2.5, 0.040, 120, 20, 0.045e-3,
            tags=["hard:distractor", "hard:output_unit", "roughness:absolute"], requested_unit="bar",
        ),
        # The correlation uses the bulk temperature (30 °C), not the wall temperature.
        _h_case(
            "hard-distractor-02",
            "Water with a bulk temperature of 30 °C flows at 1 m/s through a 20 mm copper tube "
            "(thermal conductivity 400 W/(m·K)) whose wall is held at 80 °C. "
            "What is the convective heat transfer coefficient on the water side?",
            1.0, 0.020, 30, tags=["hard:distractor"],
        ),
        _re_case(
            "hard-distractor-03",
            "A 2 kW pump moves water at 10 °C through 40 m of 100 mm pipe at 0.3 m/s, against a back "
            "pressure of 2 bar. What is the Reynolds number?",
            0.3, 0.100, 10, tags=["hard:distractor"],
        ),
        # --- flow given in other forms -------------------------------------------
        # Mass flow: V = m / (rho A).
        _h_case(
            "hard-flow-01",
            "0.5 kg/s of water at 20 °C flows through a 25 mm tube. What is the heat transfer coefficient?",
            0.5 / (w20.density * math.pi * 0.025**2 / 4), 0.025, 20, tags=["hard:flow_form"],
        ),
        _dp_case(
            "hard-flow-02",
            "1.5 m³/h of water at 40 °C flows through 25 m of 32 mm smooth pipe. What is the pressure drop?",
            physics.mean_velocity(1.5 / 3600, 0.032), 0.032, 25, 40, tags=["hard:flow_form", "roughness:smooth"],
        ),
        _re_case(
            "hard-flow-03",
            "200 L/min of water at 20 °C passes through a 50 mm pipe. Is the flow laminar or turbulent, "
            "and what is the Reynolds number?",
            physics.mean_velocity(c(200, "L/min", "m^3/s"), 0.050), 0.050, 20, tags=["hard:flow_form"],
        ),
        # --- temperature given in other forms -------------------------------------
        _dp_case(
            "hard-temperature-01",
            "Water at 323.15 K flows at 1.2 m/s through 30 m of 25 mm smooth pipe. What is the pressure drop?",
            1.2, 0.025, 30, c(323.15, "K", "degC"), tags=["hard:temperature_form", "roughness:smooth"],
        ),
        # Properties at the mean bulk temperature, (20 + 40) / 2 = 30 °C.
        _h_case(
            "hard-temperature-02",
            "Water enters a 15 mm tube at 20 °C and leaves at 40 °C, flowing at 0.6 m/s. Using properties "
            "at the mean bulk temperature, what is the heat transfer coefficient?",
            0.6, 0.015, 30, tags=["hard:temperature_form"],
        ),
        # --- quantities the tools do not return as the headline value ----------------
        _case(
            "hard-derived-01",
            "friction_factor",
            "What is the Darcy friction factor for water at 20 °C flowing at 3 m/s through a 50 mm cast iron "
            "pipe (absolute roughness 0.26 mm)?",
            _flow(3.0, 0.050, 20, 0.26e-3).friction_factor, "turbulent",
            ["hard:derived_quantity", "roughness:absolute"],
        ),
        _case(
            "hard-derived-02",
            "nusselt_number",
            "What is the Nusselt number for fully developed flow of water at 50 °C moving at 1.5 m/s "
            "through a 25 mm smooth tube?",
            _flow(1.5, 0.025, 50).nusselt_number, "turbulent", ["hard:derived_quantity"],
        ),
        _case(
            "hard-derived-03",
            "pressure_gradient",
            "What is the frictional pressure gradient, in Pa per metre, for water at 20 °C flowing at 2 m/s "
            "in a 32 mm smooth pipe?",
            _dp(2.0, 0.032, 1.0, 20), "turbulent", ["hard:derived_quantity"],
        ),
        # Head loss h_f = dp / (rho g).
        _case(
            "hard-derived-04",
            "head_loss",
            "What is the friction head loss, in metres of water, for water at 20 °C flowing at 2 m/s "
            "through 100 m of 50 mm smooth pipe?",
            _dp(2.0, 0.050, 100, 20) / (w20.density * G), "turbulent", ["hard:derived_quantity"],
        ),
        # --- multi-step and inverse problems --------------------------------------
        # Two sections in series carry the same flow: total dp is the sum.
        _case(
            "hard-multistep-01",
            "pressure_drop",
            "2 L/s of water at 20 °C flows through 10 m of 50 mm smooth pipe followed by 20 m of 25 mm smooth "
            "pipe. What is the total frictional pressure drop?",
            _dp(physics.mean_velocity(2e-3, 0.050), 0.050, 10, 20) + _dp(physics.mean_velocity(2e-3, 0.025), 0.025, 20, 20),
            "turbulent", ["hard:multi_step", "hard:flow_form"],
        ),
        _case(
            "hard-inverse-01",
            "velocity",
            "What mean velocity of water at 20 °C gives a pressure drop of 5 kPa over 20 m of 25 mm smooth pipe?",
            _velocity_for_pressure_drop(5000.0, 0.025, 20, 20), "turbulent", ["hard:inverse"],
        ),
        # --- roughness that must be known -----------------------------------------
        _dp_case(
            "hard-knowledge-01",
            "Water at 20 °C flows at 2 m/s through 30 m of 50 mm commercial steel pipe. What is the pressure drop?",
            2.0, 0.050, 30, 20, 0.045e-3, tags=["hard:knowledge", "roughness:material_only"],
        ),
        _dp_case(
            "hard-knowledge-02",
            "Water at 20 °C flows at 1.5 m/s through 50 m of 40 mm PVC pipe. What is the pressure drop?",
            1.5, 0.040, 50, 20, 0.0015e-3, tags=["hard:knowledge", "roughness:material_only"],
        ),
        # --- wrong or conflicting premises ---------------------------------------------
        _case(
            "hard-premise-01",
            "friction_factor",
            "A colleague says the flow is laminar, so f = 64/Re. What is the Darcy friction factor for water at "
            "20 °C flowing at 2 m/s through a 50 mm smooth pipe?",
            _flow(2.0, 0.050, 20).friction_factor, "turbulent", ["hard:wrong_premise"],
        ),
        _dp_case(
            "hard-premise-02",
            "Water at 20 °C flows at 1 m/s through 30 m of 50 mm smooth pipe. The datasheet gives Re ≈ 1800. "
            "What is the pressure drop?",
            1.0, 0.050, 30, 20, tags=["hard:wrong_premise", "roughness:smooth"],
        ),
        # --- outside the agent's scope: should decline ----------------------------------
        _case(
            "hard-scope-01", "pressure_drop",
            "Engine oil at 40 °C flows at 0.5 m/s through 10 m of 25 mm smooth pipe. What is the pressure drop?",
            None, None, ["hard:out_of_scope"],
        ),
        _case(
            "hard-scope-02", "pressure_drop",
            "Air at 20 °C flows at 10 m/s through 20 m of 100 mm smooth duct. What is the pressure drop?",
            None, None, ["hard:out_of_scope"],
        ),
        _case(
            "hard-scope-03", "pressure_drop",
            "A slurry of water with 30 % sand by volume flows at 1 m/s through 10 m of 50 mm smooth pipe. "
            "What is the pressure drop?",
            None, None, ["hard:out_of_scope"],
        ),
        _case(
            "hard-scope-04", "heat_transfer",
            "A 50 % ethylene glycol and water mixture at 20 °C flows at 1 m/s through a 25 mm tube. "
            "What is the heat transfer coefficient?",
            None, None, ["hard:out_of_scope"],
        ),
        # --- missing or vague input, and unusual phrasing -----------------------------------
        _case(
            "hard-missing-01", "pressure_drop",
            "Hot water flows at 1 m/s through 20 m of 25 mm smooth pipe. What is the pressure drop?",
            None, None, ["hard:missing_input"], missing_inputs=["temperature"],
        ),
        _dp_case(
            "hard-phrasing-01",
            "Water at twenty degrees Celsius moves at one and a half metres per second through thirty metres of "
            "fifty-millimetre smooth pipe. What is the pressure drop?",
            1.5, 0.050, 30, 20, tags=["hard:phrasing", "roughness:smooth"],
        ),
    ]
    return Dataset[str, ExpectedAnswer, CaseMetadata](name="pipe_flow_hard", cases=cases)


def main() -> None:
    build_hard_dataset().to_file(HARD_DATASET_PATH)
    print(f"wrote {HARD_DATASET_PATH}")


if __name__ == "__main__":
    main()
