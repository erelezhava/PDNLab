"""Independent references and deliberately broken results test the gates."""

import numpy as np
import pytest
import scipy.sparse as sp

from pdnlab_dc.checks import balance_checks, resistor_losses, sheet_losses


def unit_resistor():
    return sp.csr_matrix([[1., -1.], [-1., 1.]])


def test_triangle_gradient_loss_and_orientation():
    # v=2x+3y: area=3, |grad v|²=13, g=2, so P=78 W.
    nodes = [[0, 0], [2, 0], [0, 3]]
    for triangle in [[[0, 1, 2]], [[2, 1, 0]]]:
        np.testing.assert_allclose(sheet_losses(nodes, triangle, 2, [0, 4, 9]), [78])
        np.testing.assert_allclose(sheet_losses(nodes, triangle, 2, [10, 14, 19]), [78])


def test_nonuniform_materials_and_constant_voltage():
    nodes = [[0, 0], [1, 0], [1, 1], [0, 1]]
    triangles = [[0, 1, 2], [0, 2, 3]]
    np.testing.assert_allclose(sheet_losses(nodes, triangles, [2, 4], [0, 1, 1, 0]), [1, 2])
    np.testing.assert_allclose(sheet_losses(nodes, triangles, [2, 4], [5, 5, 5, 5]), [0, 0])


def test_resistor_loss_independent_reference():
    # 2 V / 4 ohms -> 1 W; 3 V / 3 ohms -> 3 W.
    np.testing.assert_allclose(resistor_losses([5, 3, 0], [[0, 1], [1, 2]], [4, 3]), [1, 3])


def test_unit_resistor_global_and_power_gates():
    result = balance_checks(unit_resistor(), [1, 0], [0, 1], 1)
    assert result["passed"]
    assert result["current_balance"]["source_a"] == 1
    assert result["current_balance"]["sink_a"] == 1
    assert result["power_balance"]["delivered_w"] == 1
    assert result["via_rims"] == {}
    shifted = balance_checks(unit_resistor(), [11, 10], [0, 1], 1)
    assert shifted["passed"]
    assert shifted["power_balance"]["delivered_w"] == 1


def test_current_sink_power_and_ignored_fixed_node_injection():
    result = balance_checks(unit_resistor(), [2, 1], [0], 1, injected_current=[999, -1])
    assert result["passed"]
    assert result["current_balance"]["source_a"] == 1
    assert result["current_balance"]["sink_a"] == 1
    assert result["power_balance"]["delivered_w"] == 1  # 2 W supplied, 1 W absorbed.


def test_missing_terminal_detected():
    result = balance_checks(unit_resistor(), [1, 0], [0], 1)
    assert not result["current_balance"]["passed"]
    assert not result["passed"]


def test_omitted_barrel_heat_detected_with_current_still_balanced():
    # A 1-ohm sheet plus 1-ohm barrel in series. Each dissipates 0.25 W.
    matrix = sp.csr_matrix([[1., -1., 0.], [-1., 2., -1.], [0., -1., 1.]])
    good = balance_checks(matrix, [1, 0.5, 0], [0, 2], 0.25, 0.25)
    bad = balance_checks(matrix, [1, 0.5, 0], [0, 2], 0.25)
    assert good["passed"]
    assert bad["current_balance"]["passed"]
    assert not bad["power_balance"]["passed"]
    assert not bad["passed"]


def test_each_layer_rim_checked_not_only_global_sum():
    # Wrong signs cancel globally, but each layer's KCL must fail.
    result = balance_checks(unit_resistor(), [1, 0], [0, 1], 1,
                            rim_currents={"top": [-1, -1], "bottom": [1, 1]})
    assert result["current_balance"]["passed"]
    assert result["power_balance"]["passed"]
    assert not result["via_rims"]["top"]["passed"]
    assert not result["via_rims"]["bottom"]["passed"]
    assert not result["passed"]


def test_branched_rim_uses_all_adjacent_segments():
    result = balance_checks(unit_resistor(), [1, 0], [0, 1], 1,
                            rim_currents={"intermediate": [-3, 1, 2]})
    assert result["passed"]
    assert result["via_rims"]["intermediate"]["tolerance_a"] == pytest.approx(1e-9 + 6e-6)


@pytest.mark.parametrize("missing_power,passed", [(0.5e-9, True), (2e-9, False)])
def test_power_absolute_floor_near_zero(missing_power, passed):
    result = balance_checks(unit_resistor(), [0, 0], [0, 1], missing_power)
    assert result["power_balance"]["passed"] is passed


@pytest.mark.parametrize("residual,passed", [(0.5e-9, True), (2e-9, False)])
def test_rim_current_absolute_floor_near_zero(residual, passed):
    result = balance_checks(unit_resistor(), [0, 0], [0, 1], 0,
                            rim_currents={"rim": [residual, 0]})
    assert result["via_rims"]["rim"]["passed"] is passed


def test_iterative_tolerance_adjustment():
    result = balance_checks(unit_resistor(), [1, 0], [0, 1], 0.999,
                            solver_rtol=1e-3)
    assert result["relative_tolerance"] == pytest.approx(0.01)
    assert result["passed"]
    assert not balance_checks(unit_resistor(), [1, 0], [0, 1], 0.999)["passed"]


@pytest.mark.parametrize("loss", [np.nan, np.inf, -1])
def test_invalid_loss_rejected(loss):
    with pytest.raises(ValueError):
        balance_checks(unit_resistor(), [1, 0], [0, 1], loss)


def test_nonfinite_voltage_rejected():
    with pytest.raises(ValueError):
        balance_checks(unit_resistor(), [np.nan, 0], [0, 1], 1)


def test_degenerate_triangle_rejected():
    with pytest.raises(ValueError, match="areas"):
        sheet_losses([[0, 0], [1, 0], [2, 0]], [[0, 1, 2]], 1, [0, 1, 2])
