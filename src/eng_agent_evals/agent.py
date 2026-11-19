"""A PydanticAI agent that answers pipe-flow and heat-transfer questions.

The agent does no arithmetic itself: every number comes from a tool, and every
tool is a thin wrapper around the reference functions in physics.py. That makes
two things checkable in the evals: whether the final number is right, and
whether the agent called the right tools with the right inputs (its trajectory).
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.models import Model

from . import physics, units

DEFAULT_MODEL = os.environ.get("AGENT_MODEL", "openai:gpt-5.4-mini")

INSTRUCTIONS = """\
You are an engineering assistant for internal flow of liquid water in circular pipes.

Rules:
- Never do arithmetic in your head. Every number in your answer must come from a tool.
- Convert every input to SI units with `convert_units` before using it
  (m, m/s, Pa, °C for temperature lookups).
- Get water properties from `water_properties`; do not assume them.
- Compute the Reynolds number and report the flow regime.
- If a required input is missing (for example pipe length for a pressure drop),
  do not guess: set `value` to null and list what is missing in `missing_inputs`.
- If the pipe material or roughness is not given, assume a smooth pipe and say so
  in `assumptions`.
- Report the answer in the unit the question asks for, or in SI if it does not say.
"""


class Answer(BaseModel):
    """The agent's final, structured answer."""

    value: float | None = Field(description="The numerical answer, or null if inputs are missing.")
    unit: str = Field(description="Unit of `value`, as a pint-compatible string such as 'Pa' or 'kPa'.")
    regime: Literal["laminar", "transitional", "turbulent"] | None = Field(
        description="Flow regime from the Reynolds number, or null if it could not be computed."
    )
    assumptions: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    explanation: str = Field(description="Short explanation of the method, for an engineer.")


def build_agent(model: Model | str | None = None) -> Agent[None, Answer]:
    """Create the agent. Pass a test model in unit tests to avoid API calls."""
    agent = Agent(
        model or DEFAULT_MODEL,
        output_type=Answer,
        instructions=INSTRUCTIONS,
        name="pipe_flow_agent",
        retries=2,
        defer_model_check=True,
    )

    @agent.tool_plain
    def convert_units(value: float, from_unit: str, to_unit: str) -> float:
        """Convert a value between units, e.g. 50 'mm' to 'm', or 20 'L/min' to 'm^3/s'."""
        try:
            return units.convert(value, from_unit, to_unit)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc

    @agent.tool_plain
    def water_properties(temperature_c: float) -> dict[str, float]:
        """Liquid water properties at a temperature in °C (0-100): density [kg/m^3],
        dynamic viscosity [Pa·s], thermal conductivity [W/(m·K)], Prandtl number."""
        try:
            props = physics.water_properties(temperature_c)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        return {
            "density": props.density,
            "dynamic_viscosity": props.dynamic_viscosity,
            "thermal_conductivity": props.thermal_conductivity,
            "prandtl": props.prandtl,
        }

    @agent.tool_plain
    def reynolds_number(velocity: float, diameter: float, density: float, viscosity: float) -> dict:
        """Reynolds number and flow regime. Inputs in SI: m/s, m, kg/m^3, Pa·s."""
        try:
            re = physics.reynolds_number(velocity, diameter, density, viscosity)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        return {"reynolds_number": re, "regime": physics.flow_regime(re)}

    @agent.tool_plain
    def friction_factor(reynolds_number: float, relative_roughness: float = 0.0) -> float:
        """Darcy friction factor: 64/Re if laminar, Colebrook otherwise.
        relative_roughness is absolute roughness divided by diameter (0 for a smooth pipe)."""
        try:
            return physics.friction_factor(reynolds_number, relative_roughness)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc

    @agent.tool_plain
    def pressure_drop(
        friction_factor: float, length: float, diameter: float, density: float, velocity: float
    ) -> float:
        """Darcy-Weisbach pressure drop in Pa. Inputs in SI: -, m, m, kg/m^3, m/s."""
        try:
            return physics.pressure_drop(friction_factor, length, diameter, density, velocity)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc

    @agent.tool_plain
    def heat_transfer_coefficient(
        reynolds_number: float,
        prandtl: float,
        thermal_conductivity: float,
        diameter: float,
        relative_roughness: float = 0.0,
    ) -> dict[str, float]:
        """Convective heat transfer coefficient h [W/(m^2·K)] for fully developed pipe flow,
        with the Nusselt number (3.66 if laminar, Gnielinski otherwise)."""
        try:
            nu = physics.nusselt_number(reynolds_number, prandtl, relative_roughness)
            h = physics.heat_transfer_coefficient(nu, thermal_conductivity, diameter)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        return {"nusselt_number": nu, "h": h}

    return agent
