"""Post-processing: fields, robust statistics and conservation checks.

Units are explicit throughout (units skill, ADR-0004):

* nodal potential `V` [V];
* per-element **sheet** current density `|J_s| = Gs |grad V|` [A/m];
* per-element **volumetric** current density `|J| = |J_s| / t` [A/m^2] --
  this is the user-facing quantity; the two are never mixed implicitly;
* per-element dissipated power `Gs |grad V|^2 * A` [W].

Current-density extrema at terminals and corners are discretisation-sensitive
(fem-solver skill), so robust area-weighted percentiles are reported alongside
the raw peak, and the peak must never be the only number a decision rests on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import numpy.typing as npt

    from openpdn.solver.fem.problem import SheetProblem
    from openpdn.solver.fem.solve import Solution


@dataclass(frozen=True)
class ElementFields:
    """Per-triangle derived fields for one solved excitation."""

    #: (m, 2) electric field -grad V in V/m.
    e_field_v_per_m: npt.NDArray[np.float64]
    #: (m,) magnitude of sheet current density in A/m.
    j_sheet_a_per_m: npt.NDArray[np.float64]
    #: (m,) magnitude of volumetric current density in A/m^2.
    j_vol_a_per_m2: npt.NDArray[np.float64]
    #: (m,) dissipated power per element in W.
    power_w: npt.NDArray[np.float64]
    #: (m,) element areas in m^2.
    area_m2: npt.NDArray[np.float64]


@dataclass(frozen=True)
class CurrentDensityStats:
    """Raw and robust current-density statistics, in A/m^2."""

    peak: float
    p999: float
    p99: float
    area_weighted_mean: float


@dataclass(frozen=True)
class ConservationReport:
    """Numerical health of one solved excitation."""

    residual: float
    source_total_a: float
    load_total_a: float
    imbalance_a: float
    imbalance_fraction: float
    terminal_power_w: float
    dissipated_power_w: float
    power_mismatch_fraction: float


def element_fields(problem: SheetProblem, solution: Solution) -> ElementFields:
    """Derive per-element fields from nodal potentials.

    On a linear triangle the potential gradient is constant:
    `grad V = (1 / 2A) * sum_i V_i * (b_i, c_i)`.
    """
    tri = problem.triangles
    p = problem.points[tri]
    x, y = p[:, :, 0], p[:, :, 1]
    b = np.stack([y[:, 1] - y[:, 2], y[:, 2] - y[:, 0], y[:, 0] - y[:, 1]], axis=1)
    c = np.stack([x[:, 2] - x[:, 1], x[:, 0] - x[:, 2], x[:, 1] - x[:, 0]], axis=1)
    area2 = x[:, 0] * b[:, 0] + x[:, 1] * b[:, 1] + x[:, 2] * b[:, 2]
    area = np.abs(area2) / 2.0

    v = solution.voltage_v[problem.dof_of_node[tri]]
    v = np.nan_to_num(v, nan=0.0)
    inv = 1.0 / np.maximum(area2, 1e-300)
    grad_x = (v * b).sum(axis=1) * inv
    grad_y = (v * c).sum(axis=1) * inv

    e_field = -np.stack([grad_x, grad_y], axis=1)
    grad_mag = np.hypot(grad_x, grad_y)
    j_sheet = problem.tri_sheet_conductance * grad_mag
    j_vol = j_sheet / np.maximum(problem.tri_thickness_m, 1e-300)
    power = problem.tri_sheet_conductance * grad_mag**2 * area

    return ElementFields(
        e_field_v_per_m=e_field,
        j_sheet_a_per_m=j_sheet,
        j_vol_a_per_m2=j_vol,
        power_w=power,
        area_m2=area,
    )


def current_density_stats(fields: ElementFields) -> CurrentDensityStats:
    """Raw peak plus robust area-weighted percentiles of |J|.

    Percentiles are weighted by element *area*, not element count, so a burst
    of tiny refined elements near a singularity cannot dominate the
    statistic -- which is the entire point of reporting percentiles.
    """
    j = fields.j_vol_a_per_m2
    if len(j) == 0:
        return CurrentDensityStats(0.0, 0.0, 0.0, 0.0)
    order = np.argsort(j)
    j_sorted = j[order]
    weights = fields.area_m2[order]
    cumulative = np.cumsum(weights)
    total = cumulative[-1]

    def weighted_percentile(fraction: float) -> float:
        index = int(np.searchsorted(cumulative, fraction * total))
        return float(j_sorted[min(index, len(j_sorted) - 1)])

    mean = float((j * fields.area_m2).sum() / max(total, 1e-300))
    return CurrentDensityStats(
        peak=float(j_sorted[-1]),
        p999=weighted_percentile(0.999),
        p99=weighted_percentile(0.99),
        area_weighted_mean=mean,
    )


def via_currents_a(problem: SheetProblem, solution: Solution) -> dict[str, float]:
    """Barrel current per via, summed over its segments, in amperes.

    Positive means current flowing downward (upper layer to lower layer).
    """
    currents: dict[str, float] = {}
    v = solution.voltage_v
    for segment in problem.via_segments:
        va = v[segment.dof_upper]
        vb = v[segment.dof_lower]
        if np.isnan(va) or np.isnan(vb):
            continue
        current = segment.conductance_s * (va - vb)
        key = segment.via_id
        # A via's barrel carries the same series current through every
        # segment in the simple two-layer case; for multi-layer spans record
        # the largest segment magnitude as "the" via current.
        if key not in currents or abs(current) > abs(currents[key]):
            currents[key] = float(current)
    return currents


def via_power_w(problem: SheetProblem, solution: Solution) -> float:
    """Total resistive dissipation inside via barrels, in watts."""
    v = solution.voltage_v
    total = 0.0
    for segment in problem.via_segments:
        va, vb = v[segment.dof_upper], v[segment.dof_lower]
        if np.isnan(va) or np.isnan(vb):
            continue
        total += segment.conductance_s * float(va - vb) ** 2
    return total


def conservation_report(
    problem: SheetProblem,
    solution: Solution,
    fields: ElementFields,
    load_current_by_dof: dict[int, float],
) -> ConservationReport:
    """Check current balance and power balance for one excitation.

    Current balance: the current entering at sources must equal the current
    leaving at loads (`load_current_by_dof` holds drawn currents, positive).
    Power balance: net electrical power delivered through the terminals,
    `sum(V_s I_s) - sum(V_l I_l)`, must equal the integrated dissipation in
    copper plus via barrels. Both are *results*; a solve that fails them must
    not present as healthy.
    """
    source_total = sum(solution.source_current_a.values())
    load_total = sum(load_current_by_dof.values())
    imbalance = source_total - load_total
    reference = max(abs(source_total), abs(load_total), 1e-30)

    v = solution.voltage_v
    net_input_w = 0.0
    for dof, current in solution.source_current_a.items():
        if not np.isnan(v[dof]):
            net_input_w += float(v[dof]) * current
    for dof, current in load_current_by_dof.items():
        if not np.isnan(v[dof]):
            net_input_w -= float(v[dof]) * current

    dissipated = float(fields.power_w.sum()) + via_power_w(problem, solution)
    power_reference = max(abs(net_input_w), abs(dissipated), 1e-30)

    return ConservationReport(
        residual=solution.residual,
        source_total_a=float(source_total),
        load_total_a=float(load_total),
        imbalance_a=float(imbalance),
        imbalance_fraction=float(abs(imbalance) / reference),
        terminal_power_w=float(net_input_w),
        dissipated_power_w=dissipated,
        power_mismatch_fraction=float(abs(net_input_w - dissipated) / power_reference),
    )
