"""Reference engineering calculations for internal pipe flow of water.

These plain functions are the single source of truth. The agent's tools call
them, and the eval dataset uses them to compute the expected answers, so
"correct" is defined in code rather than by hand labelling.

All functions take and return SI units.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from typing import Literal

Regime = Literal["laminar", "transitional", "turbulent"]

LAMINAR_LIMIT = 2300.0
TURBULENT_LIMIT = 4000.0

# Saturated liquid water at 1 atm, approximate textbook values.
# Columns: temperature [°C], density [kg/m^3], dynamic viscosity [Pa·s],
# thermal conductivity [W/(m·K)], Prandtl number [-]
_WATER_TABLE: list[tuple[float, float, float, float, float]] = [
    (0.0, 999.8, 1.792e-3, 0.561, 13.4),
    (10.0, 999.7, 1.307e-3, 0.580, 9.45),
    (20.0, 998.2, 1.002e-3, 0.598, 7.01),
    (30.0, 995.7, 0.798e-3, 0.615, 5.42),
    (40.0, 992.2, 0.653e-3, 0.631, 4.32),
    (50.0, 988.0, 0.547e-3, 0.644, 3.55),
    (60.0, 983.2, 0.467e-3, 0.654, 2.99),
    (70.0, 977.8, 0.404e-3, 0.663, 2.55),
    (80.0, 971.8, 0.355e-3, 0.670, 2.22),
    (90.0, 965.3, 0.315e-3, 0.675, 1.96),
    (100.0, 958.4, 0.282e-3, 0.679, 1.75),
]


@dataclass(frozen=True)
class WaterProperties:
    temperature_c: float
    density: float  # kg/m^3
    dynamic_viscosity: float  # Pa·s
    thermal_conductivity: float  # W/(m·K)
    prandtl: float  # -


def water_properties(temperature_c: float) -> WaterProperties:
    """Linearly interpolate liquid water properties between 0 and 100 °C."""
    temps = [row[0] for row in _WATER_TABLE]
    if not temps[0] <= temperature_c <= temps[-1]:
        raise ValueError(
            f"Temperature {temperature_c} °C is outside the table range 0-100 °C."
        )
    i = min(bisect_right(temps, temperature_c), len(temps) - 1)
    lo, hi = _WATER_TABLE[i - 1], _WATER_TABLE[i]
    w = (temperature_c - lo[0]) / (hi[0] - lo[0])
    rho, mu, k, pr = (lo[j] + w * (hi[j] - lo[j]) for j in range(1, 5))
    return WaterProperties(temperature_c, rho, mu, k, pr)


def mean_velocity(flow_rate: float, diameter: float) -> float:
    """Mean velocity from volumetric flow rate: V = Q / (pi D^2 / 4), in m/s."""
    _require_positive(flow_rate=flow_rate, diameter=diameter)
    return flow_rate / (math.pi * diameter**2 / 4.0)


def reynolds_number(velocity: float, diameter: float, density: float, viscosity: float) -> float:
    """Re = rho * V * D / mu."""
    _require_positive(velocity=velocity, diameter=diameter, density=density, viscosity=viscosity)
    return density * velocity * diameter / viscosity


def flow_regime(re: float) -> Regime:
    if re < LAMINAR_LIMIT:
        return "laminar"
    if re < TURBULENT_LIMIT:
        return "transitional"
    return "turbulent"


def relative_roughness(roughness: float, diameter: float) -> float:
    """Relative roughness eps / D from absolute roughness and diameter, both in m."""
    _require_positive(diameter=diameter)
    if roughness < 0:
        raise ValueError(f"roughness must be >= 0, got {roughness}")
    return roughness / diameter


def friction_factor(re: float, relative_roughness: float = 0.0) -> float:
    """Darcy friction factor.

    Laminar (Re < 2300): f = 64 / Re.
    Otherwise: Colebrook-White, solved by fixed-point iteration. In the
    transitional range this is a conservative engineering choice, not an exact
    answer.
    """
    _require_positive(re=re)
    if relative_roughness < 0:
        raise ValueError("relative_roughness must be >= 0")
    if re < LAMINAR_LIMIT:
        return 64.0 / re
    # Start from the explicit Haaland approximation, then iterate Colebrook.
    f = (-1.8 * math.log10((relative_roughness / 3.7) ** 1.11 + 6.9 / re)) ** -2
    for _ in range(50):
        rhs = -2.0 * math.log10(relative_roughness / 3.7 + 2.51 / (re * math.sqrt(f)))
        f_new = rhs**-2
        if abs(f_new - f) < 1e-12:
            return f_new
        f = f_new
    return f


def pressure_drop(
    friction_factor: float, length: float, diameter: float, density: float, velocity: float
) -> float:
    """Darcy-Weisbach: dp = f * (L / D) * rho * V^2 / 2, in Pa."""
    _require_positive(
        friction_factor=friction_factor, length=length, diameter=diameter, density=density
    )
    return friction_factor * (length / diameter) * density * velocity**2 / 2.0


def nusselt_number(re: float, prandtl: float, relative_roughness: float = 0.0) -> float:
    """Fully developed Nusselt number for flow in a circular pipe.

    Laminar (Re < 2300): Nu = 3.66 (constant wall temperature).
    Otherwise: Gnielinski, using the Darcy friction factor from Colebrook.
    """
    _require_positive(re=re, prandtl=prandtl)
    if re < LAMINAR_LIMIT:
        return 3.66
    f = friction_factor(re, relative_roughness)
    return (f / 8.0) * (re - 1000.0) * prandtl / (
        1.0 + 12.7 * math.sqrt(f / 8.0) * (prandtl ** (2.0 / 3.0) - 1.0)
    )


def heat_transfer_coefficient(nusselt: float, thermal_conductivity: float, diameter: float) -> float:
    """h = Nu * k / D, in W/(m^2·K)."""
    _require_positive(nusselt=nusselt, thermal_conductivity=thermal_conductivity, diameter=diameter)
    return nusselt * thermal_conductivity / diameter


@dataclass(frozen=True)
class PipeFlow:
    """Flow state of water in a pipe, computed from the question's inputs."""

    water: WaterProperties
    reynolds_number: float
    regime: Regime
    friction_factor: float  # Darcy
    nusselt_number: float
    heat_transfer_coefficient: float  # W/(m^2·K)


def pipe_flow(
    velocity: float, diameter: float, temperature_c: float, relative_roughness: float = 0.0
) -> PipeFlow:
    """Chain the reference functions: properties, Re, regime, f, Nu and h.

    The agent's calculation tools and the evaluation dataset both use this, so
    intermediate values never have to be copied from one step to the next.
    """
    w = water_properties(temperature_c)
    re = reynolds_number(velocity, diameter, w.density, w.dynamic_viscosity)
    f = friction_factor(re, relative_roughness)
    nu = nusselt_number(re, w.prandtl, relative_roughness)
    h = heat_transfer_coefficient(nu, w.thermal_conductivity, diameter)
    return PipeFlow(w, re, flow_regime(re), f, nu, h)


def _require_positive(**values: float) -> None:
    for name, value in values.items():
        if not value > 0:
            raise ValueError(f"{name} must be > 0, got {value}")
