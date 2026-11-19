import pytest

from eng_agent_evals.units import convert


@pytest.mark.parametrize(
    "value, src, dst, expected",
    [
        (50, "mm", "m", 0.05),
        (2, "inch", "m", 0.0508),
        (60, "L/min", "m^3/s", 0.001),
        (25.5, "kPa", "Pa", 25_500),
        (1, "psi", "Pa", 6894.757),
    ],
)
def test_convert(value, src, dst, expected):
    assert convert(value, src, dst) == pytest.approx(expected, rel=1e-6)


def test_temperature_conversion_is_absolute_not_a_difference():
    assert convert(68, "degF", "degC") == pytest.approx(20.0)


def test_incompatible_units_raise_value_error():
    with pytest.raises(ValueError):
        convert(1, "m", "Pa")


def test_unknown_unit_raises_value_error():
    with pytest.raises(ValueError):
        convert(1, "furlongz", "m")
