# Off-centre via: analytic reference

Reference for `tests/test_offcentre_via.py`. All quantities are SI.

## Why this case

Experiments 3 and 4 use concentric rings. There the current density is the same
all the way round the via rim, so they cannot show an error that only appears
when current crowds onto one side of the rim. A hole placed off-centre in a
circular sheet has strong crowding and still has an exact solution.

## Problem

A sheet of sheet resistance R_s = ρ/t fills the region between two circles:

- outer edge: radius b, centred on the origin, held at 0 V
- via rim: radius a, centred on (d, 0), held at V₀, with 0 < d < b − a

The rim is one electrical node (all its mesh nodes are tied), which is exactly
the boundary condition V = V₀ on the whole rim.

## Bipolar coordinates

Take two points on the x axis, the foci, at x_n and x_f with x_n < x_f. For a
point P define

```math
\tau = \ln\frac{|P - F_f|}{|P - F_n|}, \qquad \sigma = \text{angle subtended at } P \text{ by the two foci}
```

τ + iσ is, up to sign conventions, the complex logarithm of
(z − x_f)/(z − x_n), so τ and σ are both harmonic and the map from (x, y) to
(τ, σ) is conformal. Each curve τ = const is a circle (a circle of Apollonius).
With x_m the midpoint of the foci and c half their separation:

```math
\text{centre } x = x_m - c\,\coth\tau, \qquad \text{radius } = \frac{c}{\sinh\tau}
```

Fit two of these circles to the geometry, τ₂ for the outer edge and τ₁ for the
rim, with τ₁ > τ₂ > 0:

```math
b = \frac{c}{\sinh\tau_2}, \quad x_m = c\,\coth\tau_2, \qquad a = \frac{c}{\sinh\tau_1}, \quad x_m - d = c\,\coth\tau_1
```

Using coth² − 1/sinh² = 1 on each pair gives x_m² − b² = c² and
(x_m − d)² − a² = c². Subtracting:

```math
x_m = \frac{b^2 - a^2 + d^2}{2d}, \qquad c = \sqrt{x_m^2 - b^2}, \qquad \tau_1 = \operatorname{asinh}\frac{c}{a}, \quad \tau_2 = \operatorname{asinh}\frac{c}{b}
```

The foci are at x_n = x_m − c, inside the hole, and x_f = x_m + c, outside the
sheet. In the test code these are `x2`, `c`, `tau_inner`, `tau_outer`,
`near_focus` and `far_focus`.

## Voltage and resistance

Both boundaries are curves of constant τ, and τ is harmonic, so the voltage is
linear in τ:

```math
V = V_0\,\frac{\tau - \tau_2}{\tau_1 - \tau_2}
```

In the (τ, σ) plane the sheet is a rectangle: τ runs from τ₂ to τ₁ and σ runs
once round, over 2π. A conformal map leaves the sheet resistance of a 2D
conductor unchanged, so the resistance is that of a rectangle of length
τ₁ − τ₂ and width 2π:

```math
R = \frac{R_s}{2\pi}\,(\tau_1 - \tau_2)
```

To remove the foci from the result, expand cosh(τ₁ − τ₂) and insert the fit:

```math
\frac{a^2 + b^2 - d^2}{2ab}
= \frac{c^2\left[\dfrac{1}{\sinh^2\tau_1} + \dfrac{1}{\sinh^2\tau_2} - (\coth\tau_2 - \coth\tau_1)^2\right]}{\dfrac{2c^2}{\sinh\tau_1\,\sinh\tau_2}}
= \cosh\tau_1\cosh\tau_2 - \sinh\tau_1\sinh\tau_2
```

where the bracket reduces to 2(coth τ₁ coth τ₂ − 1). Therefore

```math
R = \frac{\rho}{2\pi t}\,\operatorname{acosh}\frac{a^2 + b^2 - d^2}{2ab}
```

Checks on the formula:

- d = 0: acosh((a² + b²)/(2ab)) = ln(b/a), the concentric ring of experiment 3.
- d → b − a (hole touching the edge): the argument tends to 1 and R tends to 0.
- This is the same expression as the capacitance per unit length of an
  eccentric coaxial line, C = 2πε / acosh((a² + b² − d²)/(2ab)), with
  R·C = ρε/t.

## Current crowding

The current through any piece of a τ = const boundary is proportional to the
change of σ along it, because the current density is uniform in the (τ, σ)
rectangle. Take the half of the outer edge nearest the hole, x > 0. Its end
points are (0, ±b). At (0, b) the vectors to the two foci have dot product
x_n x_f + b² = 2b², so

```math
\cos\sigma_0 = \frac{2b^2}{\sqrt{x_n^2 + b^2}\,\sqrt{x_f^2 + b^2}}
```

σ rises to π at (b, 0), which lies between the foci, and the arc is symmetric,
so it spans 2(π − σ₀) of the full 2π:

```math
\frac{I_{x>0}}{I} = 1 - \frac{\sigma_0}{\pi}
```

For a centred hole this is 1/2. The test cases:

| Case | a (mm) | b (mm) | d (mm) | Gap to edge (mm) | R / R of centred hole | Current through near half |
| --- | --- | --- | --- | --- | --- | --- |
| Via near edge | 0.175 | 1.5 | 1.175 | 0.15 | 0.528 | 93.5 % |
| Via half-way out | 0.175 | 1.5 | 0.7 | 0.625 | 0.883 | 78.2 % |
| Large hole | 1 | 5 | 3 | 1 | 0.698 | 86.3 % |

## What the test asserts

Without a mesh: the two routes to R (asinh difference and acosh form) agree,
the closed-form field is V₀ on the rim and 0 on the edge, and d = 0 gives the
concentric result.

With Gmsh, at element size h and h/2:

- resistance within 0.5 % of the closed form, and closer at h/2
- every node voltage within 5 mV of the closed-form field for V₀ = 1 V
- share of the current leaving through the near half within 0.002 of the
  closed form
- rim nodes all at one voltage; current and power balance checks pass

## What it does not show

The reference is exact for the 2D sheet with an equipotential rim. It says
nothing about how well that boundary condition represents a real plated
barrel, which is a 3D question for the full-3D coupon and for measurement.
