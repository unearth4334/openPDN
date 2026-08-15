# Validation plan

How openPDN will earn the right to say it computes IR drop.

## The rule

A solver is advertised as physical only after its numbers are checked against
closed-form references. `tests/validation/test_solver_validation_gate.py`
enforces this: any registered solver with a physical `ResultFidelity` that is
not listed in `VALIDATED_SOLVERS` fails the build.

## Reference cases

Implemented in `tests/validation/analytical.py`, with the references themselves
pinned in `test_analytical_references.py`.

| Case | Reference | Checks |
| --- | --- | --- |
| Straight trace | $R = L/(\sigma w t)$ | Material handling, sheet conductance, uniform flow |
| Square of copper | $R = R_\square = 1/(\sigma t)$ | Geometry independence of sheet resistance |
| Series segments | $R = \sum R_i$ | Mesh partitioning must not change the answer |
| Parallel traces | $R = (\sum R_i^{-1})^{-1}$ | Current sharing between domains |
| Voltage divider | $V = V_0 R_2/(R_1+R_2)$ | Potential distribution, not just endpoints |
| Via barrel | $R = L/(\sigma A_\text{annulus})$ | Lumped via conductance |
| Two stitching vias | $R/2$ | Interlayer current sharing |
| $V=IR$, $P=I^2R$, $J=I/A$ | direct | Derived quantities and units |

## Tolerances

Every comparison states a tolerance and the reason for it:

| Tolerance | Applies to |
| --- | --- |
| `rel=1e-12` | Algebraic identities — should agree to floating point |
| `rel=1e-3` | Hand-computed engineering values quoted to four figures |
| Looser | Only with a named physical cause: discretisation error, lumped-via averaging, spreading resistance the closed form ignores |

**Widening a tolerance to make a test pass is prohibited.** If a solver drifts,
either it regressed or the model changed for a reason worth writing down.

## Convergence

A single agreeing number can be luck. A backend claiming physical fidelity
should show error falling as the mesh refines, and report mesh statistics with
every result so a suspicious answer can be re-run finer.

## Excluded from validation

Terminal-node current density: the value at a point source is a function of
element size, not physics. Validate current density in the uniform-flow region
of a trace, away from terminals, bends and constrictions.

## Cross-checks worth adding later

* Against a second solver (2.5-D versus Elmer on the same board) — agreement
  between backends is evidence, not proof.
* Against measurements on a test coupon, once one exists. That is the only
  check that tests the *model*, rather than the implementation of the model.
