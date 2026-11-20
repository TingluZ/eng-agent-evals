"""Unit conversion with pint, shared by the agent tool and the evaluators."""

from __future__ import annotations

import re

import pint

ureg = pint.UnitRegistry()

# Common engineering spellings that pint does not parse. pint's "gallon" is the US gallon.
_ALIASES = [
    (re.compile(r"\bUS\s*(?:liquid\s*)?gal(?:lon)?s?\b", re.IGNORECASE), "gallon"),
    (re.compile(r"\b(?:imperial|imp\.?|UK)\s*gal(?:lon)?s?\b", re.IGNORECASE), "imperial_gallon"),
    (re.compile(r"\bgpm\b", re.IGNORECASE), "gallon/minute"),
]


def _normalise(unit: str) -> str:
    for pattern, replacement in _ALIASES:
        unit = pattern.sub(replacement, unit)
    return unit


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert value from one unit to another, for example 50 'mm' -> 'm'.

    Raises ValueError with a readable message if the units are unknown or
    incompatible, so the agent can see the mistake and retry.
    """
    try:
        return ureg.Quantity(value, _normalise(from_unit)).to(_normalise(to_unit)).magnitude
    except (pint.errors.UndefinedUnitError, pint.errors.DimensionalityError) as exc:
        raise ValueError(str(exc)) from exc
