"""Unit conversion with pint, shared by the agent tool and the evaluators."""

from __future__ import annotations

import pint

ureg = pint.UnitRegistry()


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert value from one unit to another, for example 50 'mm' -> 'm'.

    Raises ValueError with a readable message if the units are unknown or
    incompatible, so the agent can see the mistake and retry.
    """
    try:
        return ureg.Quantity(value, from_unit).to(to_unit).magnitude
    except (pint.errors.UndefinedUnitError, pint.errors.DimensionalityError) as exc:
        raise ValueError(str(exc)) from exc
