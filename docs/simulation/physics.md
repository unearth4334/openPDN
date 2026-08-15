# The physics openPDN solves

> None of this is implemented yet. This page states the model the solver
> contract was designed around, so that the first backend is written against a
> decided formulation rather than an improvised one.

## Governing equations

Steady-state current conduction in a conductor of conductivity $\sigma$:

$$\nabla \cdot (\sigma \nabla V) = 0$$

$$\vec{J} = -\sigma \nabla V$$

Resistive loss density:

$$p = \vec{J} \cdot \vec{E} = \sigma \, |\nabla V|^2$$

No time dependence and no displacement current: this is DC. Skin effect,
proximity effect and dielectric loss are out of scope, and a "DC" tool must not
pretend otherwise when someone asks about a switching edge.

## Boundary conditions

| Study object | Condition | Meaning |
| --- | --- | --- |
| `VoltageSource` | Dirichlet, $V = V_0$ | A terminal held at a potential |
| `CurrentLoad` | Neumann, prescribed total current | A terminal drawing current |
| everything else | homogeneous Neumann | No current leaves copper |

At least one Dirichlet condition is required, otherwise the potential is
determined only up to a constant and the system is singular. `AnalysisStudy`
enforces this at construction.

## The 2.5-D reduction

PCB copper is thin — 35 µm against tens of millimetres of lateral extent — so
the potential is effectively constant through the thickness of a layer.
Integrating through it gives a sheet problem per layer:

$$\nabla_{xy} \cdot (\sigma t \, \nabla_{xy} V) = 0$$

where $\sigma t$ is the sheet conductance. Its reciprocal is the sheet
resistance in ohms per square:

$$R_\square = \frac{1}{\sigma t}$$

For 1 oz copper (34.8 µm) at 100 % IACS this is 0.495 mΩ/□ — the familiar
"half a milliohm per square".

Layers couple through vias. In the lumped model each via is a conductance
between the two sheet domains it connects:

$$G_\text{via} = \frac{\sigma A_\text{barrel}}{L},
\qquad
A_\text{barrel} = \pi\left[(r_\text{hole} + t_\text{plate})^2 - r_\text{hole}^2\right]$$

Only the plating conducts; the hole is empty or filled with a non-conductor.
For a 1.6 mm board, 0.3 mm finished hole and 25 µm plating this is ≈ 1.08 mΩ.

### When the reduction fails

* thick copper or heavy plating, where through-thickness variation matters;
* current crowding through a via barrel, which the lumped conductance averages;
* in-plane fields close to a pad, within roughly a copper thickness;
* any electrothermal problem, where the temperature field is 3-D.

Those cases are why ElmerFEM is planned behind the same contract — and why a
2.5-D result reports `ResultFidelity.SHEET_2P5D` rather than claiming
generality.

## Quantities openPDN reports

| Quantity | From | Displayed as |
| --- | --- | --- |
| Potential $V$ | Solution | mV, V |
| IR drop | $V_\text{max} - V_\text{min}$ per net | mV |
| Current density $\vec{J}$ | $-\sigma \nabla V$ | A/mm² |
| Via current | $G_\text{via}\,\Delta V$ | A |
| Loss density | $\sigma|\nabla V|^2$ | W/mm² |
| Terminal-to-terminal resistance | $\Delta V / I$ from a unit-current solve | mΩ |
| Resistance contribution | Loss share along a path | % |

## Temperature

Conductivity falls with temperature; near 20 °C copper follows

$$\sigma(T) = \frac{\sigma_{20}}{1 + \alpha (T - T_{20})}, \qquad \alpha \approx 3.93\times10^{-3}\ \text{K}^{-1}$$

An isothermal study takes a single temperature. True electrothermal coupling —
where $I^2R$ heating raises the temperature, which raises the resistance —
requires solving the thermal problem alongside, and is a planned ElmerFEM
capability, not a correction factor to bolt on.

## Numerical caveats that must reach the user

**Point-source singularity.** Current density diverges at a mathematical point
source; the value a mesh reports there is a function of element size, not
physics. It must never be shown as a hot spot, must not set a colour-map
maximum, and must not fail a current-density check. Apply terminals over their
real pad footprint where geometry allows.

**Discretisation error.** A single number proves nothing. A result should be
accompanied by mesh statistics, and a solver claiming physical fidelity should
demonstrate error decreasing under refinement.

**Ill-conditioning has diagnosable causes** — a floating sub-net with no path
to a Dirichlet condition, a zero-conductance region from a missing thickness, a
degenerate element. Report the cause, not just "singular matrix".

## References

* IEC 60028 — International Annealed Copper Standard (conductivity, TCR).
* IPC-2152 — current-carrying capacity of conductors.
* IPC-6012 — qualification for rigid boards, including plating thickness classes.
