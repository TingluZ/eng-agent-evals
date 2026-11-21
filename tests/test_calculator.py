"""Tests for the arithmetic evaluator behind the `calculate` tool."""

from __future__ import annotations

import math

import pytest

from eng_agent_evals.calculator import evaluate


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("0.060 - 2 * 0.005", 0.05),  # inside diameter from OD and wall
        ("2156.605398146291 + 118877.36770673544", 121033.97310488173),  # pipes in series
        ("0.5 / 998.2", 0.5 / 998.2),  # mass flow to volumetric flow
        ("pi * 0.025**2 / 4", math.pi * 0.025**2 / 4),
        ("0.025^2", 0.025**2),  # caret read as a power
        ("-(3 - 5)", 2.0),
        ("1.5e-3 * 2", 3e-3),
        ("(20 + 40) / 2", 30.0),  # mean bulk temperature
    ],
)
def test_evaluates_arithmetic(expression, expected):
    assert evaluate(expression) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('ls')",
        "sqrt(2)",
        "x + 1",
        "60 mm - 10 mm",
        "[1, 2]",
        "'a' * 3",
        "True + 1",
        "1 if 1 else 2",
        "",
    ],
)
def test_rejects_anything_but_arithmetic(expression):
    with pytest.raises(ValueError):
        evaluate(expression)


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("1 / 0", "Division by zero"),
        ("10 ** 11", "Exponents are limited"),
        ("(-8) ** 0.5", "not a finite real number"),
        ("1e308 * 10", "not a finite real number"),
        ("1" + " + 1" * 100, "longer than"),
    ],
)
def test_errors_are_readable(expression, message):
    with pytest.raises(ValueError, match=message):
        evaluate(expression)
