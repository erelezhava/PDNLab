"""Lumped copper-barrel model. SI units throughout.

Radii describe the copper itself: inner_radius is the finished-hole radius;
outer_radius = inner_radius + plating thickness. The conducting sheet's hole
meets the barrel at outer_radius. Length is the layer-centre separation.
"""

import numpy as np


def barrel_resistance(length, inner_radius, outer_radius, resistivity):
    """Axial resistance rho*L / (pi*(r_o**2 - r_i**2)), in ohms.

    This ideal, uniform barrel excludes contact and 3D spreading resistance.
    For a pre-plating drill radius, subtract plating to obtain inner_radius;
    the drill radius itself is outer_radius.
    """
    values = np.asarray([length, inner_radius, outer_radius, resistivity], dtype=float)
    if not np.isfinite(values).all() or np.any(values <= 0) or outer_radius <= inner_radius:
        raise ValueError("require finite positive length, radii and resistivity, with outer_radius > inner_radius")
    # Difference of squares factored to retain precision for thin plating.
    area = np.pi * (outer_radius - inner_radius) * (outer_radius + inner_radius)
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        resistance = resistivity * length / area
    if not np.isfinite(resistance) or resistance <= 0:
        raise ValueError("barrel resistance is not representable")
    return float(resistance)
