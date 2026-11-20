"""Generate the evaluation dataset.

Cases are drawn from seeded parameter sweeps, and every expected answer is
computed with the reference functions in physics.py. The generated file,
data/cases.yaml, is committed so the cases can be reviewed without running code.

    uv run python -m eng_agent_evals.dataset
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_evals import Case, Dataset

from . import physics, units

SEED = 20261120
DATASET_PATH = Path(__file__).resolve().parents[2] / "data" / "cases.yaml"

Quantity = Literal["pressure_drop", "heat_transfer", "reynolds_number"]
CaseType = Literal["pressure_drop", "heat_transfer", "reynolds_regime", "trap"]

SI_UNIT: dict[Quantity, str] = {
    "pressure_drop": "Pa",
    "heat_transfer": "W/(m^2*K)",
    "reynolds_number": "dimensionless",
}

DIAMETERS_MM = [10, 15, 20, 25, 32, 40, 50, 65, 80, 100]
MATERIALS_MM = {  # absolute roughness
    "commercial steel": 0.045,
    "galvanised iron": 0.15,
    "cast iron": 0.26,
    "drawn copper": 0.0015,
}
# Reynolds number bands for regular cases, kept clear of the transitional band.
LAMINAR_BAND = (300.0, 1800.0)
TURBULENT_BAND = (8000.0, 3.0e5)


class ExpectedAnswer(BaseModel):
    """Reference answer for one case. `value` is in the SI unit `unit`."""

    quantity: Quantity
    value: float | None
    unit: str
    regime: physics.Regime | None
    missing_inputs: list[str] = []
    requested_unit: str | None = None


class CaseMetadata(BaseModel):
    case_type: CaseType
    expected_trajectory: list[str] | None
    """Expected tool names in order, or None where no single path is defined."""
    tags: list[str]


@dataclass
class _Flow:
    """Inputs of one question, in SI units, plus how many unit conversions it needs."""

    temperature_c: float
    diameter: float | None
    velocity: float | None = None
    length: float | None = None
    roughness: float = 0.0
    """Absolute roughness [m], when the question gives it."""
    relative_roughness: float | None = None
    """eps/D, when the question gives it directly."""
    flow_rate: float | None = None
    conversions: int = 0
    tags: list[str] = field(default_factory=list)


def _reference(quantity: Quantity, flow: _Flow) -> tuple[float, physics.Regime]:
    w = physics.water_properties(flow.temperature_c)
    velocity = flow.velocity
    if velocity is None:
        velocity = physics.mean_velocity(flow.flow_rate, flow.diameter)
    re = physics.reynolds_number(velocity, flow.diameter, w.density, w.dynamic_viscosity)
    regime = physics.flow_regime(re)
    rel_roughness = flow.relative_roughness
    if rel_roughness is None:
        rel_roughness = physics.relative_roughness(flow.roughness, flow.diameter)
    if quantity == "reynolds_number":
        return re, regime
    if quantity == "pressure_drop":
        f = physics.friction_factor(re, rel_roughness)
        return physics.pressure_drop(f, flow.length, flow.diameter, w.density, velocity), regime
    nu = physics.nusselt_number(re, w.prandtl, rel_roughness)
    return physics.heat_transfer_coefficient(nu, w.thermal_conductivity, flow.diameter), regime


def _trajectory(quantity: Quantity, flow: _Flow, output_conversion: bool = False) -> list[str]:
    steps = ["convert_units"] * flow.conversions + ["water_properties"]
    if flow.velocity is None:
        steps.append("mean_velocity")
    steps.append("reynolds_number")
    if flow.roughness > 0:
        steps.append("relative_roughness")
    if quantity == "pressure_drop":
        steps += ["friction_factor", "pressure_drop"]
    elif quantity == "heat_transfer":
        steps.append("heat_transfer_coefficient")
    if output_conversion:
        steps.append("convert_units")
    return steps


def _case(
    name: str,
    case_type: CaseType,
    quantity: Quantity,
    question: str,
    flow: _Flow,
    *,
    requested_unit: str | None = None,
) -> Case[str, ExpectedAnswer, CaseMetadata]:
    value, regime = _reference(quantity, flow)
    return Case(
        name=name,
        inputs=question,
        expected_output=ExpectedAnswer(
            quantity=quantity,
            value=value,
            unit=SI_UNIT[quantity],
            regime=regime,
            requested_unit=requested_unit,
        ),
        metadata=CaseMetadata(
            case_type=case_type,
            expected_trajectory=_trajectory(quantity, flow, output_conversion=requested_unit is not None),
            tags=flow.tags + [f"regime:{regime}"],
        ),
    )


def _unanswerable_case(
    name: str,
    quantity: Quantity,
    question: str,
    *,
    regime: physics.Regime | None,
    missing_inputs: list[str],
    tags: list[str],
) -> Case[str, ExpectedAnswer, CaseMetadata]:
    return Case(
        name=name,
        inputs=question,
        expected_output=ExpectedAnswer(
            quantity=quantity,
            value=None,
            unit=SI_UNIT[quantity],
            regime=regime,
            missing_inputs=missing_inputs,
        ),
        metadata=CaseMetadata(case_type="trap", expected_trajectory=None, tags=tags),
    )


def _round_sig(x: float, digits: int) -> float:
    return float(f"{x:.{digits}g}")


def _sample_flow(rng: random.Random, band: tuple[float, float]) -> tuple[int, float, int]:
    """Temperature [°C], velocity [m/s] and diameter [mm] with Re inside `band`."""
    lo, hi = band
    while True:
        t = rng.randint(5, 95)
        d_mm = rng.choice(DIAMETERS_MM)
        re_target = math.exp(rng.uniform(math.log(lo), math.log(hi)))
        w = physics.water_properties(t)
        v = _round_sig(re_target * w.dynamic_viscosity / (w.density * d_mm / 1000), 2)
        re = physics.reynolds_number(v, d_mm / 1000, w.density, w.dynamic_viscosity)
        if 0.02 <= v <= 3.0 and lo <= re <= hi:
            return t, v, d_mm


# --- regular cases -----------------------------------------------------------

_PRESSURE_DROP_TEMPLATES = [
    "Water at {t} °C flows at {v} m/s through {l} m of {d} mm {pipe}. What is the pressure drop?",
    "What is the pressure drop over {l} m of {pipe} with an internal diameter of {d} mm, "
    "carrying water at {t} °C with a mean velocity of {v} m/s?",
    "A {d} mm bore {pipe}, {l} m long, carries water at {t} °C at {v} m/s. "
    "Calculate the frictional pressure drop.",
]
_HEAT_TRANSFER_TEMPLATES = [
    "Water at {t} °C flows at {v} m/s through a {d} mm diameter tube. "
    "What is the convective heat transfer coefficient?",
    "Estimate the heat transfer coefficient for fully developed flow of water at {t} °C "
    "moving at {v} m/s in a pipe of {d} mm inner diameter.",
    "What is h for water at {t} °C flowing at {v} m/s inside a {d} mm tube?",
]
_REYNOLDS_TEMPLATES = [
    "What is the Reynolds number for water at {t} °C flowing at {v} m/s in a {d} pipe, "
    "and is the flow laminar or turbulent?",
    "Water at {t} °C moves at {v} m/s through a pipe of {d} internal diameter. "
    "Find the Reynolds number and the flow regime.",
]


def _pressure_drop_cases(rng: random.Random) -> list[Case]:
    cases = []
    bands = [LAMINAR_BAND] * 4 + [TURBULENT_BAND] * 16
    for i, band in enumerate(bands, start=1):
        t, v, d_mm = _sample_flow(rng, band)
        length = rng.randint(1, 200)
        flow = _Flow(t, d_mm / 1000, velocity=v, length=length, conversions=1, tags=["units:metric"])
        # Half of the turbulent cases state a material and its roughness: as absolute
        # roughness in mm (the agent needs eps/D), or every fourth case as eps/D directly.
        if band is TURBULENT_BAND and i % 2 == 0:
            material = rng.choice(sorted(MATERIALS_MM))
            eps_mm = MATERIALS_MM[material]
            if i % 4 == 0:
                rel = _round_sig(eps_mm / d_mm, 2)
                pipe = f"{material} pipe (relative roughness ε/D = {rel:g})"
                flow.relative_roughness = rel
                flow.tags.append("roughness:relative")
            else:
                pipe = f"{material} pipe (absolute roughness {eps_mm:g} mm)"
                flow.roughness = eps_mm / 1000
                flow.conversions += 1
                flow.tags.append("roughness:absolute")
        else:
            pipe = rng.choice(["pipe", "smooth pipe", "smooth tube"])
            flow.tags.append("roughness:smooth" if "smooth" in pipe else "roughness:unspecified")
        question = rng.choice(_PRESSURE_DROP_TEMPLATES).format(t=t, v=f"{v:g}", l=length, d=d_mm, pipe=pipe)
        cases.append(_case(f"pressure_drop-{i:02d}", "pressure_drop", "pressure_drop", question, flow))
    return cases


def _heat_transfer_cases(rng: random.Random) -> list[Case]:
    cases = []
    bands = [LAMINAR_BAND] * 5 + [TURBULENT_BAND] * 10
    for i, band in enumerate(bands, start=1):
        t, v, d_mm = _sample_flow(rng, band)
        flow = _Flow(t, d_mm / 1000, velocity=v, conversions=1, tags=["units:metric", "roughness:unspecified"])
        question = rng.choice(_HEAT_TRANSFER_TEMPLATES).format(t=t, v=f"{v:g}", d=d_mm)
        cases.append(_case(f"heat_transfer-{i:02d}", "heat_transfer", "heat_transfer", question, flow))
    return cases


def _reynolds_cases(rng: random.Random) -> list[Case]:
    cases = []
    bands = [LAMINAR_BAND] * 4 + [TURBULENT_BAND] * 6
    for i, band in enumerate(bands, start=1):
        t, v, d_mm = _sample_flow(rng, band)
        # Alternate between diameters in mm (one conversion) and in m (none).
        if i % 2:
            d_text, flow = f"{d_mm} mm", _Flow(t, d_mm / 1000, velocity=v, conversions=1, tags=["units:metric"])
        else:
            d_text, flow = f"{d_mm / 1000:g} m", _Flow(t, d_mm / 1000, velocity=v, tags=["units:si"])
        question = rng.choice(_REYNOLDS_TEMPLATES).format(t=t, v=f"{v:g}", d=d_text)
        cases.append(_case(f"reynolds_regime-{i:02d}", "reynolds_regime", "reynolds_number", question, flow))
    return cases


# --- traps -------------------------------------------------------------------

def _trap_cases() -> list[Case]:
    c = units.convert
    cases = [
        # Near or inside the transitional band (2300 <= Re < 4000).
        _case(
            "trap-transition-01", "trap", "reynolds_number",
            "Water at 20 °C flows at 0.085 m/s in a 25 mm pipe. "
            "What is the Reynolds number and is the flow laminar, transitional or turbulent?",
            _Flow(20, 0.025, velocity=0.085, conversions=1, tags=["units:metric", "trap:transition"]),
        ),
        _case(
            "trap-transition-02", "trap", "reynolds_number",
            "Find the Reynolds number and flow regime for water at 20 °C moving at 0.15 m/s "
            "through a 20 mm tube.",
            _Flow(20, 0.020, velocity=0.15, conversions=1, tags=["units:metric", "trap:transition"]),
        ),
        _case(
            "trap-transition-03", "trap", "pressure_drop",
            "Water at 40 °C flows at 0.12 m/s through 10 m of 15 mm smooth tube. What is the pressure drop?",
            _Flow(40, 0.015, velocity=0.12, length=10, conversions=1,
                  tags=["units:metric", "roughness:smooth", "trap:transition"]),
        ),
        _case(
            "trap-transition-04", "trap", "heat_transfer",
            "What is the convective heat transfer coefficient for water at 30 °C flowing at 0.35 m/s "
            "in a 10 mm tube?",
            _Flow(30, 0.010, velocity=0.35, conversions=1,
                  tags=["units:metric", "roughness:unspecified", "trap:transition"]),
        ),
        # Imperial or mixed units, and flow rate instead of velocity.
        _case(
            "trap-units-01", "trap", "pressure_drop",
            "Water at 68 °F flows at 6 ft/s through 50 ft of 2 inch smooth pipe. What is the pressure drop?",
            _Flow(c(68, "degF", "degC"), c(2, "inch", "m"), velocity=c(6, "ft/s", "m/s"),
                  length=c(50, "ft", "m"), conversions=4,
                  tags=["units:imperial", "roughness:smooth", "trap:units"]),
        ),
        _case(
            "trap-units-02", "trap", "heat_transfer",
            "Water at 25 °C flows at 20 L/min through a 25 mm tube. "
            "What is the heat transfer coefficient?",
            _Flow(25, 0.025, flow_rate=c(20, "L/min", "m^3/s"), conversions=2,
                  tags=["units:flow_rate", "roughness:unspecified", "trap:units"]),
        ),
        _case(
            "trap-units-03", "trap", "pressure_drop",
            "30 US gal/min of water at 15 °C flows through 100 ft of 2 inch commercial steel pipe "
            "(absolute roughness 0.045 mm). What is the pressure drop?",
            _Flow(15, c(2, "inch", "m"), flow_rate=c(30, "gal/min", "m^3/s"), length=c(100, "ft", "m"),
                  roughness=0.045e-3, conversions=4,
                  tags=["units:imperial", "units:flow_rate", "roughness:absolute", "trap:units"]),
        ),
        _case(
            "trap-units-04", "trap", "reynolds_number",
            "Water at 140 °F flows at 3 ft/s through a 1 inch tube. "
            "What is the Reynolds number, and what is the flow regime?",
            _Flow(c(140, "degF", "degC"), c(1, "inch", "m"), velocity=c(3, "ft/s", "m/s"), conversions=3,
                  tags=["units:imperial", "trap:units"]),
        ),
        # A required input is missing: the agent should not guess.
        _unanswerable_case(
            "trap-missing-01", "pressure_drop",
            "Water at 20 °C flows at 1 m/s through a 2 inch pipe. What is the pressure drop?",
            regime="turbulent", missing_inputs=["length"],
            tags=["units:imperial", "roughness:unspecified", "trap:missing_input"],
        ),
        _unanswerable_case(
            "trap-missing-02", "pressure_drop",
            "What is the pressure drop over 25 m of 40 mm commercial steel pipe "
            "(absolute roughness 0.045 mm) carrying water at 50 °C?",
            regime=None, missing_inputs=["velocity"],
            tags=["units:metric", "roughness:absolute", "trap:missing_input"],
        ),
        _unanswerable_case(
            "trap-missing-03", "heat_transfer",
            "Water at 60 °C flows at 0.8 m/s through a tube. What is the heat transfer coefficient?",
            regime=None, missing_inputs=["diameter"],
            tags=["units:metric", "roughness:unspecified", "trap:missing_input"],
        ),
        # Temperature outside the supported 0-100 °C range: the agent should decline.
        _unanswerable_case(
            "trap-out_of_range-01", "pressure_drop",
            "Water at 120 °C flows at 1.5 m/s through 20 m of 50 mm smooth pipe. What is the pressure drop?",
            regime=None, missing_inputs=[],
            tags=["units:metric", "roughness:smooth", "trap:out_of_range"],
        ),
        _unanswerable_case(
            "trap-out_of_range-02", "heat_transfer",
            "Water at 250 °F flows at 0.5 m/s through a 25 mm tube. What is the heat transfer coefficient?",
            regime=None, missing_inputs=[],
            tags=["units:mixed", "roughness:unspecified", "trap:out_of_range"],
        ),
        # The question names the output unit.
        _case(
            "trap-output_unit-01", "trap", "pressure_drop",
            "Water at 20 °C flows at 1.2 m/s through 15 m of 25 mm smooth pipe. "
            "What is the pressure drop in kPa?",
            _Flow(20, 0.025, velocity=1.2, length=15, conversions=1,
                  tags=["units:metric", "roughness:smooth", "trap:output_unit"]),
            requested_unit="kPa",
        ),
        _case(
            "trap-output_unit-02", "trap", "pressure_drop",
            "Water at 10 °C flows at 2.5 m/s through 60 m of 40 mm cast iron pipe "
            "(absolute roughness 0.26 mm). Give the pressure drop in psi.",
            _Flow(10, 0.040, velocity=2.5, length=60, roughness=0.26e-3, conversions=2,
                  tags=["units:metric", "roughness:absolute", "trap:output_unit"]),
            requested_unit="psi",
        ),
    ]
    return cases


def build_dataset(seed: int = SEED) -> Dataset[str, ExpectedAnswer, CaseMetadata]:
    rng = random.Random(seed)
    cases = _pressure_drop_cases(rng) + _heat_transfer_cases(rng) + _reynolds_cases(rng) + _trap_cases()
    return Dataset[str, ExpectedAnswer, CaseMetadata](name="pipe_flow", cases=cases)


def load_dataset(path: Path = DATASET_PATH) -> Dataset[str, ExpectedAnswer, CaseMetadata]:
    return Dataset[str, ExpectedAnswer, CaseMetadata].from_file(path)


def main() -> None:
    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    build_dataset().to_file(DATASET_PATH)
    print(f"wrote {DATASET_PATH}")


if __name__ == "__main__":
    main()
