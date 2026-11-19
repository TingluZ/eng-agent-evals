"""Checks that the reference functions match textbook values and independent formulas.

These tests matter because physics.py is the ground truth for the evals: if it
is wrong, every eval score is wrong too.
"""

import math

import pytest

from eng_agent_evals import physics


# --- water properties -------------------------------------------------------

def test_water_properties_at_table_point():
    p = physics.water_properties(20.0)
    assert p.density == pytest.approx(998.2)
    assert p.dynamic_viscosity == pytest.approx(1.002e-3)
    assert p.prandtl == pytest.approx(7.01)


def test_water_properties_interpolates_between_points():
    p = physics.water_properties(25.0)
    assert p.density == pytest.approx((998.2 + 995.7) / 2)
    assert p.dynamic_viscosity == pytest.approx((1.002e-3 + 0.798e-3) / 2)


def test_water_properties_at_upper_edge():
    assert physics.water_properties(100.0).density == pytest.approx(958.4)


def test_prandtl_is_consistent_with_other_properties():
    # Pr = mu * cp / k with cp ~ 4182 J/(kg K) at 20 °C: a check on the table itself.
    p = physics.water_properties(20.0)
    assert p.dynamic_viscosity * 4182 / p.thermal_conductivity == pytest.approx(p.prandtl, rel=0.01)


@pytest.mark.parametrize("t", [-5.0, 120.0])
def test_water_properties_rejects_out_of_range(t):
    with pytest.raises(ValueError, match="outside the table range"):
        physics.water_properties(t)


# --- Reynolds number and regime ---------------------------------------------

def test_reynolds_number():
    assert physics.reynolds_number(2.0, 0.05, 998.2, 1.002e-3) == pytest.approx(99_621, rel=1e-4)


@pytest.mark.parametrize(
    "re, regime",
    [(1000, "laminar"), (2299, "laminar"), (2300, "transitional"), (3999, "transitional"), (4000, "turbulent")],
)
def test_flow_regime_boundaries(re, regime):
    assert physics.flow_regime(re) == regime


def test_reynolds_rejects_non_positive_inputs():
    with pytest.raises(ValueError, match="diameter"):
        physics.reynolds_number(1.0, 0.0, 998.2, 1e-3)


# --- friction factor ---------------------------------------------------------

def test_laminar_friction_factor_is_64_over_re():
    assert physics.friction_factor(1000) == pytest.approx(0.064)


def test_colebrook_satisfies_its_own_equation():
    re, rr = 1e5, 1e-3
    f = physics.friction_factor(re, rr)
    residual = 1 / math.sqrt(f) + 2 * math.log10(rr / 3.7 + 2.51 / (re * math.sqrt(f)))
    assert abs(residual) < 1e-8


def test_smooth_pipe_matches_blasius():
    # Blasius, 0.316 Re^-0.25, is within ~3 % of Colebrook for smooth pipes at Re ~ 1e4-1e5.
    re = 5e4
    assert physics.friction_factor(re, 0.0) == pytest.approx(0.316 * re**-0.25, rel=0.03)


def test_rough_pipe_matches_haaland():
    re, rr = 2e5, 5e-3
    haaland = (-1.8 * math.log10((rr / 3.7) ** 1.11 + 6.9 / re)) ** -2
    assert physics.friction_factor(re, rr) == pytest.approx(haaland, rel=0.02)


def test_moody_chart_value():
    # Moody chart: Re = 1e5, e/D = 1e-4 gives f ~ 0.0185.
    assert physics.friction_factor(1e5, 1e-4) == pytest.approx(0.0185, abs=0.0005)


def test_friction_factor_rejects_negative_roughness():
    with pytest.raises(ValueError):
        physics.friction_factor(1e5, -0.1)


# --- pressure drop -----------------------------------------------------------

def test_pressure_drop_formula():
    # f L/D rho V^2 / 2 = 0.02 * 600 * 1000 * 4 / 2
    assert physics.pressure_drop(0.02, 30.0, 0.05, 1000.0, 2.0) == pytest.approx(24_000)


def test_worked_example_commercial_steel_pipe():
    # Water at 20 °C, 2 m/s, 30 m of 50 mm commercial steel (e = 0.045 mm).
    # Hand calculation: Re ~ 9.96e4, e/D = 9e-4, Haaland gives f ~ 0.0216,
    # Moody chart ~ 0.022; dp = f * 600 * 998.2 * 4 / 2 ~ 26.2 kPa.
    w = physics.water_properties(20.0)
    re = physics.reynolds_number(2.0, 0.05, w.density, w.dynamic_viscosity)
    f = physics.friction_factor(re, 0.045e-3 / 0.05)
    dp = physics.pressure_drop(f, 30.0, 0.05, w.density, 2.0)
    assert f == pytest.approx(0.0218, abs=0.0005)
    assert dp == pytest.approx(26_200, rel=0.03)


# --- heat transfer -----------------------------------------------------------

def test_laminar_nusselt_is_constant():
    assert physics.nusselt_number(1500, 7.0) == 3.66


def test_gnielinski_close_to_dittus_boelter_in_fully_turbulent_flow():
    # The two correlations agree within ~15 % at moderate Re and Pr.
    re, pr = 5e4, 7.0
    dittus_boelter = 0.023 * re**0.8 * pr**0.4
    assert physics.nusselt_number(re, pr) == pytest.approx(dittus_boelter, rel=0.15)


def test_heat_transfer_coefficient():
    assert physics.heat_transfer_coefficient(100.0, 0.6, 0.025) == pytest.approx(2400.0)
