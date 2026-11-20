"""A PydanticAI agent that answers pipe-flow and heat-transfer questions.

The agent does no arithmetic itself: every number comes from a tool, and every
tool is a thin wrapper around the reference functions in physics.py. The
calculation tools take the question's inputs (velocity, diameter, temperature,
...) and look up properties and intermediate results themselves, so the model
never has to copy one tool's output into another tool's input.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from . import physics, units

DEFAULT_MODEL = os.environ.get("AGENT_MODEL", "openai:gpt-5.4-mini")

INSTRUCTIONS = """\
You are an engineering assistant for internal flow of liquid water in circular pipes.

Rules:
- Never do arithmetic in your head. Every number in your answer must come from a tool.
- The tools work in these units only: m, m/s, m^3/s, Pa, kg/m^3, Pa·s, W/(m^2·K),
  and °C for temperature. Any value given in another unit (for example mm, cm, inch,
  ft, L/min, gal/min, °F, K, kPa, psi) must be converted with `convert_units` before
  use. Values already in one of these units are used as given.
- Use the calculation tools directly with the converted inputs: they look up water
  properties and compute the Reynolds number themselves.
- Report the flow regime.
- If a required input is missing (for example pipe length for a pressure drop),
  do not guess: set `value` to null and list what is missing in `missing_inputs`.
- If the pipe material or roughness is not given, assume a smooth pipe and say so
  in `assumptions`.
- Report the value in the unit of the tool that produced it. If the question asks
  for another unit, convert the value with `convert_units` and report that result
  with the requested unit.
"""

# One tool call per model response: parallel calls were issued with placeholder
# arguments before the results they depended on existed.
MODEL_SETTINGS = ModelSettings(parallel_tool_calls=False)


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
        model_settings=MODEL_SETTINGS,
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
        dynamic viscosity [Pa·s], thermal conductivity [W/(m·K)], Prandtl number.
        Not needed for the calculation tools, which look properties up themselves."""
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
    def mean_velocity(flow_rate: float, diameter: float) -> float:
        """Mean flow velocity [m/s] from volumetric flow rate. Inputs in SI: m^3/s, m.
        Use only when the question gives a volumetric flow rate instead of a velocity."""
        try:
            return physics.mean_velocity(flow_rate, diameter)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc

    @agent.tool_plain
    def relative_roughness(roughness: float, diameter: float) -> float:
        """Relative roughness eps/D from absolute roughness and pipe diameter, both in m.
        Not needed if the question already gives eps/D."""
        try:
            return physics.relative_roughness(roughness, diameter)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc

    @agent.tool_plain
    def reynolds_number(velocity: float, diameter: float, temperature_c: float) -> dict:
        """Reynolds number and flow regime for water. Inputs: m/s, m, °C (0-100)."""
        try:
            flow = physics.pipe_flow(velocity, diameter, temperature_c)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        return {"reynolds_number": flow.reynolds_number, "regime": flow.regime}

    @agent.tool_plain
    def pressure_drop(
        velocity: float, diameter: float, length: float, temperature_c: float, relative_roughness: float = 0.0
    ) -> dict:
        """Darcy-Weisbach pressure drop [Pa] for water in a pipe, with the Reynolds number,
        regime and Darcy friction factor (64/Re if laminar, Colebrook otherwise).
        Inputs: m/s, m, m, °C (0-100); relative_roughness is eps/D (0 for a smooth pipe)."""
        try:
            flow = physics.pipe_flow(velocity, diameter, temperature_c, relative_roughness)
            dp = physics.pressure_drop(flow.friction_factor, length, diameter, flow.water.density, velocity)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        return {
            "pressure_drop": dp,
            "reynolds_number": flow.reynolds_number,
            "regime": flow.regime,
            "friction_factor": flow.friction_factor,
        }

    @agent.tool_plain
    def heat_transfer_coefficient(
        velocity: float, diameter: float, temperature_c: float, relative_roughness: float = 0.0
    ) -> dict:
        """Convective heat transfer coefficient h [W/(m^2·K)] for fully developed flow of
        water in a pipe, with the Nusselt number (3.66 if laminar, Gnielinski otherwise),
        Reynolds number and regime. Inputs: m/s, m, °C (0-100); relative_roughness is eps/D."""
        try:
            flow = physics.pipe_flow(velocity, diameter, temperature_c, relative_roughness)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        return {
            "h": flow.heat_transfer_coefficient,
            "nusselt_number": flow.nusselt_number,
            "reynolds_number": flow.reynolds_number,
            "regime": flow.regime,
        }

    return agent
