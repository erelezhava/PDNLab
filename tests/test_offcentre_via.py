"""Off-centre hole in a circular sheet: a tied via rim with current crowding.

Experiments 3 and 4 use concentric rings, where the current density is the
same all the way round the rim. Here the hole sits near the edge of the sheet,
so most of the current leaves the rim on one side. The tied rim node is still
exact for this problem (the rim is an equipotential), and a closed-form
reference exists. Derivation: docs/off-centre-via-reference.md.

Geometry: outer circle of radius b centred on the origin, hole of radius a
centred on (d, 0). Rim at 1 V, outer edge at 0 V. SI units.
"""

import shutil

import numpy as np
import pytest

from pdnlab_dc.checks import balance_checks, sheet_losses
from pdnlab_dc.fem import assemble_sheet, node_currents, resistance_between, tie_nodes
from pdnlab_dc.gmsh_mesh import read_sheet_mesh, run_gmsh, write_annulus_geo

RHO = 1.724e-8

# a, b, d, copper thickness, coarse element size [metres]
CASES = [
    pytest.param(0.175e-3, 1.5e-3, 1.175e-3, 35e-6, 0.05e-3, id="via-0.15mm-from-edge"),
    pytest.param(0.175e-3, 1.5e-3, 0.7e-3, 35e-6, 0.05e-3, id="via-half-way-out"),
    pytest.param(1e-3, 5e-3, 3e-3, 70e-6, 0.25e-3, id="large-hole"),
]


def resistance_acosh(a, b, d, sheet_resistance):
    """R = R_sheet / (2 pi) * acosh((a^2 + b^2 - d^2) / (2 a b))."""
    return sheet_resistance / (2 * np.pi) * np.arccosh((a * a + b * b - d * d) / (2 * a * b))


class BipolarReference:
    """Closed-form field between two non-concentric circles (d > 0).

    tau = ln(distance to the far focus / distance to the near focus) is
    harmonic and constant on each circle, so the voltage is linear in tau.
    """

    def __init__(self, a, b, d):
        self.a, self.b, self.d = a, b, d
        # Distance from the outer centre to the radical axis of the two
        # circles, and half the distance between the foci.
        self.x2 = (b * b - a * a + d * d) / (2 * d)
        self.c = np.sqrt(self.x2**2 - b * b)
        self.tau_inner = np.arcsinh(self.c / a)
        self.tau_outer = np.arcsinh(self.c / b)
        # Focus positions on the x axis of the mesh frame.
        self.near_focus = b * b / (self.x2 + self.c)  # = x2 - c, inside the hole
        self.far_focus = self.x2 + self.c  # outside the sheet

    def resistance(self, sheet_resistance):
        return sheet_resistance * (self.tau_inner - self.tau_outer) / (2 * np.pi)

    def voltage(self, x, y):
        """Voltage for 1 V on the hole rim and 0 V on the outer edge."""
        tau = np.log(np.hypot(x - self.far_focus, y) / np.hypot(x - self.near_focus, y))
        return (tau - self.tau_outer) / (self.tau_inner - self.tau_outer)

    def current_fraction_through_near_half(self):
        """Share of the current that leaves through the outer edge at x > 0.

        The current through an arc is proportional to the change of the
        conjugate coordinate sigma, the angle the two foci subtend at a point.
        """
        r_near = np.hypot(self.near_focus, self.b)  # from (0, b) to each focus
        r_far = np.hypot(self.far_focus, self.b)
        sigma_at_top = np.arccos(2 * self.b**2 / (r_near * r_far))
        return 1 - sigma_at_top / np.pi


@pytest.mark.parametrize("a,b,d", [(0.175e-3, 1.5e-3, 1.175e-3), (0.175e-3, 1.5e-3, 0.7e-3),
                                   (1e-3, 5e-3, 3e-3), (1.0, 3.0, 1e-3)])
def test_reference_forms_agree(a, b, d):
    """Two independent routes to the same closed form, with no mesh involved."""
    ref = BipolarReference(a, b, d)
    assert ref.resistance(1.0) == pytest.approx(resistance_acosh(a, b, d, 1.0), rel=1e-9)
    # The field is 1 V everywhere on the hole rim and 0 V on the outer edge.
    angle = np.linspace(0, 2 * np.pi, 97)
    np.testing.assert_allclose(ref.voltage(d + a * np.cos(angle), a * np.sin(angle)), 1, atol=1e-9)
    np.testing.assert_allclose(ref.voltage(b * np.cos(angle), b * np.sin(angle)), 0, atol=1e-9)
    # The far focus is outside the sheet and the near focus inside the hole.
    assert ref.far_focus > b and abs(ref.near_focus - d) < a
    assert 0.5 < ref.current_fraction_through_near_half() < 1


def test_reference_reduces_to_concentric_ring():
    a, b = 0.175e-3, 1.5e-3
    assert resistance_acosh(a, b, 0.0, 1.0) == pytest.approx(np.log(b / a) / (2 * np.pi), rel=1e-12)
    # A nearly centred hole shares the current almost evenly between halves.
    assert BipolarReference(a, b, 1e-9).current_fraction_through_near_half() == pytest.approx(0.5, abs=1e-5)


@pytest.mark.skipif(shutil.which("gmsh") is None, reason="external Gmsh not installed")
@pytest.mark.parametrize("a,b,d,t,h", CASES)
def test_offcentre_via_matches_closed_form(tmp_path, a, b, d, t, h):
    ref = BipolarReference(a, b, d)
    reference = ref.resistance(RHO / t)
    concentric = RHO / t * np.log(b / a) / (2 * np.pi)
    # The offset must matter far more than the tolerance, or this test would
    # pass with the hole in the wrong place.
    assert reference < 0.9 * concentric

    errors, field_errors, share_errors = [], [], []
    for level, mesh_size in enumerate([h, h / 2]):
        geo = tmp_path / f"offcentre{level}.geo"
        msh = geo.with_suffix(".msh")
        write_annulus_geo(geo, a, b, mesh_size, inner_offset=d)
        run_gmsh(geo, msh)
        mesh = read_sheet_mesh(msh, boundaries=("inner", "outer"))
        inner, outer = mesh.boundaries["inner"], mesh.boundaries["outer"]
        x, y = mesh.nodes.T
        # Independent check that the tags picked the shifted hole and the edge.
        np.testing.assert_array_equal(inner, np.flatnonzero(np.isclose(np.hypot(x - d, y), a, atol=1e-12, rtol=0)))
        np.testing.assert_array_equal(outer, np.flatnonzero(np.isclose(np.hypot(x, y), b, atol=1e-12, rtol=0)))

        g = t / RHO
        matrix = assemble_sheet(mesh.nodes, mesh.triangles, g)
        reduced, mapping = tie_nodes(matrix, [inner])
        rim = np.unique(mapping[inner])
        assert len(rim) == 1
        resistance, reduced_v = resistance_between(reduced, rim, mapping[outer])
        v = reduced_v[mapping]
        assert np.ptp(v[inner]) == 0

        loss = float(sheet_losses(mesh.nodes, mesh.triangles, g, v).sum())
        checks = balance_checks(reduced, reduced_v, np.r_[rim, mapping[outer]], loss)
        assert checks["passed"]

        # Current crowding: share of the current leaving through the half of
        # the outer edge nearest the hole. Edge nodes on x = 0 count half.
        leaving = -node_currents(matrix, v)[outer]
        assert np.all(leaving > 0)
        weight = np.where(np.isclose(x[outer], 0, atol=1e-12, rtol=0), 0.5, x[outer] > 0)
        share = float(np.sum(weight * leaving) / np.sum(leaving))

        errors.append(abs(resistance - reference) / reference)
        field_errors.append(float(np.max(np.abs(v - ref.voltage(x, y)))))
        share_errors.append(abs(share - ref.current_fraction_through_near_half()))

    assert max(errors) <= 0.005  # same limit as experiment 3
    assert errors[1] < errors[0]
    assert max(field_errors) < 0.005  # volts, for a 1 V excitation
    assert max(share_errors) < 0.002
    assert ref.current_fraction_through_near_half() > 0.6


@pytest.mark.parametrize("offset", [-1e-3, 1.0, 0.9, np.nan, np.inf])
def test_invalid_inner_offset(tmp_path, offset):
    with pytest.raises(ValueError, match="inner_offset"):
        write_annulus_geo(tmp_path / "bad.geo", 0.1, 1.0, 0.05, inner_offset=offset)


def test_zero_offset_keeps_the_concentric_geometry(tmp_path):
    write_annulus_geo(tmp_path / "default.geo", 0.1, 1.0, 0.05)
    write_annulus_geo(tmp_path / "zero.geo", 0.1, 1.0, 0.05, inner_offset=0.0)
    text = (tmp_path / "default.geo").read_text()
    assert text == (tmp_path / "zero.geo").read_text()
    assert "Point(10)" not in text
