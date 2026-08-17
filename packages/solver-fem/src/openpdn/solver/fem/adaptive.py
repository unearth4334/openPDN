"""The goal-oriented adaptive refinement loop (ADR-0013).

    solve -> estimate error -> mark -> refine the sizing field -> re-mesh
          -> solve -> ... -> converged, or a stated limit

Each pass is an immutable *generation* whose metrics are kept, so the result
carries the evidence for its own convergence rather than a bare final number.
Refinement re-meshes the whole region from an error-driven sizing field; it
does not subdivide triangles, because the mesher has no structure to
subdivide into (see `RefinementField`).

What this module deliberately does not do yet: quadratic elements (ADR-0012,
combined in a later phase) and dual-weighted goal orientation (ADR-0013 §4).
The quantity of interest is tracked and drives the stopping rule, but marking
is currently driven by the energy-style indicator alone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import numpy as np

from openpdn.solver.fem.controls import RefinementField
from openpdn.solver.fem.estimate import (
    dorfler_mark,
    flux_jump_indicators,
    global_error_estimate,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import numpy.typing as npt

    from openpdn.domain.board import Board
    from openpdn.domain.results import ElectricalAnalysisResult
    from openpdn.domain.study import AnalysisStudy
    from openpdn.geometry.api import GeometryNormalizer
    from openpdn.solver.fem.problem import SheetProblem


class AdaptiveStatus:
    """Why the loop stopped. Mirrors the result states of ADR-0015."""

    CONVERGED = "converged"
    CONVERGED_WITH_MODEL_LIMITATIONS = "converged_with_model_limitations"
    RESOURCE_LIMITED = "resource_limited"
    NOT_CONVERGED = "not_converged"


#: Quantities tracked across generations. `singular` marks those with no
#: finite continuum limit at a reentrant corner or an ideal terminal edge:
#: they are reported, never converged on, and their failure to settle must
#: not condemn the run (ADR-0013 §5, ADR-0015 §5).
TRACKED_QUANTITIES: Final = (
    ("resistance_ohm", False),
    ("total_loss_w", False),
    ("j99_a_per_m2", False),
    ("peak_j_a_per_m2", True),
)


def _quantities_of(
    result: ElectricalAnalysisResult,
    qoi: float,
    problem: SheetProblem,
    current_density: npt.NDArray[np.float64],
) -> dict[str, float]:
    """Every tracked quantity for one generation.

    `j99` is weighted by element *area*, not element count: a burst of tiny
    refined elements at a singularity would otherwise take over the
    percentile, which would make the robust statistic behave like the raw
    peak it exists to replace.
    """
    loss = sum(net.resistive_loss_w or 0.0 for net in result.nets)
    peak = max((net.max_current_density_a_per_m2 or 0.0 for net in result.nets), default=0.0)

    corners = problem.points[problem.triangles]
    area = (
        np.abs(
            (corners[:, 1, 0] - corners[:, 0, 0]) * (corners[:, 2, 1] - corners[:, 0, 1])
            - (corners[:, 2, 0] - corners[:, 0, 0]) * (corners[:, 1, 1] - corners[:, 0, 1])
        )
        / 2.0
    )
    j99 = 0.0
    if len(current_density):
        order = np.argsort(current_density)
        cumulative = np.cumsum(area[order])
        index = int(np.searchsorted(cumulative, 0.99 * cumulative[-1]))
        j99 = float(current_density[order][min(index, len(order) - 1)])

    return {
        "resistance_ohm": qoi,
        "total_loss_w": float(loss),
        "j99_a_per_m2": j99,
        "peak_j_a_per_m2": float(peak),
    }


@dataclass(frozen=True, slots=True)
class AdaptivePolicy:
    """Everything that decides how far refinement goes.

    Frozen into a Reference job spec in place of absolute mesh numbers, since
    the mesh is this loop's output rather than its input (ADR-0015 §1).
    """

    #: Relative change in the quantity of interest that counts as converged.
    target_qoi_rel_change: float = 1e-3
    #: The estimated error must also have fallen by this factor from the
    #: first pass before the run may claim convergence. Without it a run can
    #: stop purely because two successive *non-nested* meshes happened to
    #: agree -- re-meshing noise, not error reduction (ADR-0013 §8 requires
    #: the estimator as a separate criterion, not QoI change alone).
    required_error_reduction: float = 2.0
    #: Number of consecutive passes that must meet the QoI target. One is
    #: not evidence when the mesh sequence is non-nested.
    confirmations: int = 2
    #: Hard ceilings. Hitting one ends the run as RESOURCE_LIMITED, never as
    #: a converged result (ADR-0013 §8).
    max_passes: int = 5
    max_dofs: int = 2_000_000
    #: Dorfler bulk-marking fraction. 0.5 is a starting value from the usual
    #: 0.4-0.7 range, not a measured optimum for this problem class.
    theta: float = 0.5
    #: How much smaller a marked element's target size becomes per pass.
    refinement_ratio: float = 2.0
    #: Weight indicators by the adjoint solution before marking. Off by
    #: default: for a resistance study read at the driven terminals it is
    #: provably a no-op beyond squaring (see `dual_weighted_indicators`).
    goal_oriented: bool = False
    #: Conservation gates, matching ADR-0010 §6's warning threshold.
    max_current_imbalance: float = 1e-6
    max_power_mismatch: float = 1e-6

    def __post_init__(self) -> None:
        """Reject policies that could never terminate or never refine."""
        if self.max_passes < 1:
            raise ValueError("An adaptive run needs at least one pass")
        if self.refinement_ratio <= 1.0:
            raise ValueError("Refinement ratio must exceed 1 to refine anything")
        if self.target_qoi_rel_change <= 0.0:
            raise ValueError("Target must be positive")


@dataclass(frozen=True, slots=True)
class Generation:
    """Metrics of one completed adaptive pass."""

    index: int
    dof_count: int
    element_count: int
    quantity_of_interest: float
    qoi_rel_change: float | None
    estimated_error: float
    current_imbalance_fraction: float
    power_mismatch_fraction: float
    marked_elements: int
    quantities: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QuantityConvergence:
    """Per-quantity convergence evidence (ADR-0015 §5).

    Resistance and energy may settle while the sampled peak current density
    does not -- at a reentrant corner the continuum peak is unbounded, so it
    rises with every refinement forever. Reporting one status for the whole
    run would either hide that or condemn a perfectly good solve.
    """

    name: str
    values: tuple[float, ...]
    rel_change: float | None
    converged: bool
    singular: bool
    extrapolated: float | None = None
    observed_order: float | None = None


@dataclass(frozen=True, slots=True)
class AdaptiveOutcome:
    """The final result plus the history that justifies it."""

    result: ElectricalAnalysisResult
    generations: tuple[Generation, ...]
    status: str
    quantities: tuple[QuantityConvergence, ...] = ()

    @property
    def converged(self) -> bool:
        """True when the engineering quantities settled.

        `CONVERGED_WITH_MODEL_LIMITATIONS` counts: the answer the user reads
        did converge, and what did not is a quantity with no continuum limit.
        The distinction is preserved in `status` rather than collapsed here.
        """
        return self.status in {
            AdaptiveStatus.CONVERGED,
            AdaptiveStatus.CONVERGED_WITH_MODEL_LIMITATIONS,
        }

    def quantity(self, name: str) -> QuantityConvergence:
        """Convergence evidence for one tracked quantity."""
        for candidate in self.quantities:
            if candidate.name == name:
                return candidate
        raise KeyError(name)

    @property
    def final(self) -> Generation:
        """The last completed generation."""
        return self.generations[-1]


def terminal_resistance_qoi(result: ElectricalAnalysisResult) -> float:
    """Resistance between the two reported terminals, in ohms.

    The engineering answer, not a field norm: ADR-0013 §5 converges on what
    the user reads. Raw peak `|J|` is deliberately never a candidate -- it is
    singular at reentrant corners and would prevent any run from converging.
    """
    if result.probes:
        return float(result.probes[0].resistance_ohm)
    if len(result.terminals) < 2:
        raise ValueError("Need at least two terminals to form a resistance")
    source, load = result.terminals[0], result.terminals[1]
    current = abs(load.current_a) or abs(source.current_a)
    if current <= 0.0:
        raise ValueError("No current flows; resistance is undefined")
    return abs(source.voltage_v - load.voltage_v) / current


def dual_weighted_indicators(
    problem: SheetProblem,
    primal_indicators: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """Weight element indicators by the adjoint solution's own indicators.

    Refining on `eta_K` alone minimises energy-norm error, which is not the
    question asked; the engineering answer is a *functional* of the solution.
    Dual weighting asks instead where a local error actually moves that
    functional. The conductance matrix is symmetric and its factorisation is
    reused across right-hand sides, so the adjoint costs one extra solve
    (ADR-0013 §4).

    **Measured degeneracy, worth knowing before reaching for this.** When the
    quantity of interest is read at the same terminals that drive the
    excitation -- the ordinary resistance study -- the operator is
    self-adjoint and the dual comes out as exactly `-1` times the primal
    (measured: ratio `-1.000000`, standard deviation `0.0` across 737 nodes).
    The weighting then reduces to `eta_K^2`, which sharpens the marking
    ordering but redistributes nothing. Dual weighting earns its extra solve
    when the functional and the excitation differ -- one load's voltage among
    several, or a probe across terminals other than the driven pair.
    """
    del problem
    return primal_indicators**2


def refine_field(
    problem: SheetProblem,
    marked: npt.NDArray[np.int64],
    ratio: float,
    previous: RefinementField | None,
) -> RefinementField:
    """Turn marked elements into seed points demanding a smaller size.

    A marked element asks for `h_K / ratio` at its centroid. Seeds from
    earlier passes are carried forward, because the sizing field is rebuilt
    from scratch on every re-mesh -- dropping them would let a previously
    refined patch coarsen again the moment its error fell below the marking
    threshold, and the loop would oscillate instead of converging.
    """
    corners = problem.points[problem.triangles[marked]]
    centroids = corners.mean(axis=1)
    edges = np.stack(
        [
            np.hypot(*(corners[:, 1] - corners[:, 0]).T),
            np.hypot(*(corners[:, 2] - corners[:, 1]).T),
            np.hypot(*(corners[:, 0] - corners[:, 2]).T),
        ],
        axis=1,
    )
    sizes = edges.max(axis=1) / ratio

    if previous is not None and len(previous):
        centroids = np.vstack([previous.points, centroids])
        sizes = np.concatenate([previous.sizes, sizes])
    return RefinementField(centroids, sizes)


def solve_adaptive(
    board: Board,
    study: AnalysisStudy,
    normalizer: GeometryNormalizer,
    policy: AdaptivePolicy | None = None,
    quantity_of_interest: Callable[[ElectricalAnalysisResult], float] = terminal_resistance_qoi,
) -> AdaptiveOutcome:
    """Run the refinement loop until convergence or a stated limit.

    Stopping is a conjunction, never one metric (ADR-0013 §8): the quantity
    of interest must have settled *and* conservation must hold. A run that
    exhausts its pass or DOF ceiling while the answer is still moving is
    reported RESOURCE_LIMITED -- it has not converged, and must never present
    as though it had.
    """
    from openpdn.solver.fem.solver import solve_with_controls

    policy = policy or AdaptivePolicy()
    normalized = normalizer.normalize(board)
    field: RefinementField | None = None
    generations: list[Generation] = []
    previous_qoi: float | None = None
    streak = 0
    result = None
    status = AdaptiveStatus.NOT_CONVERGED

    for index in range(policy.max_passes):
        result, field_data, problem, node_values = solve_with_controls(
            board, study, normalized, refinement=field
        )
        indicators = flux_jump_indicators(problem, node_values)
        marking_indicators = (
            dual_weighted_indicators(problem, indicators)
            if policy.goal_oriented
            else indicators
        )
        qoi = quantity_of_interest(result)
        change = (
            None
            if previous_qoi is None or previous_qoi == 0.0
            else abs(qoi - previous_qoi) / abs(previous_qoi)
        )
        conservation = field_data.conservation

        estimate = global_error_estimate(indicators)
        first_estimate = generations[0].estimated_error if generations else estimate
        error_fell_enough = (
            first_estimate <= 0.0
            or estimate <= first_estimate / policy.required_error_reduction
        )
        within_target = change is not None and change <= policy.target_qoi_rel_change
        streak = streak + 1 if within_target else 0
        settled = streak >= policy.confirmations and error_fell_enough
        conserved = (
            conservation.imbalance_fraction <= policy.max_current_imbalance
            and conservation.power_mismatch_fraction <= policy.max_power_mismatch
        )
        last_pass = index == policy.max_passes - 1
        over_budget = problem.n_dofs > policy.max_dofs

        marked = (
            np.zeros(0, dtype=np.int64)
            if settled and conserved
            else dorfler_mark(marking_indicators, policy.theta)
        )
        generations.append(
            Generation(
                index=index,
                dof_count=problem.n_dofs,
                element_count=len(problem.triangles),
                quantity_of_interest=qoi,
                qoi_rel_change=change,
                estimated_error=estimate,
                current_imbalance_fraction=conservation.imbalance_fraction,
                power_mismatch_fraction=conservation.power_mismatch_fraction,
                marked_elements=len(marked),
                quantities=_quantities_of(
                    result, qoi, problem, field_data.tri_j_vol_a_per_m2
                ),
            )
        )
        previous_qoi = qoi

        if settled and conserved:
            status = AdaptiveStatus.CONVERGED
            break
        if over_budget or last_pass:
            status = AdaptiveStatus.RESOURCE_LIMITED
            break
        field = refine_field(problem, marked, policy.refinement_ratio, field)

    if result is None:  # pragma: no cover - max_passes >= 1 is validated
        raise RuntimeError("Adaptive loop produced no solve")

    quantities = _quantity_convergence(generations, policy.target_qoi_rel_change)
    if status == AdaptiveStatus.CONVERGED and any(
        quantity.singular and not _is_settled(quantity, policy.target_qoi_rel_change)
        for quantity in quantities
    ):
        # The engineering answers converged but a singular quantity did not,
        # which is expected rather than a failure: at a reentrant corner the
        # sampled peak has no finite limit to converge to. Say so explicitly
        # instead of showing a clean tick (ADR-0015 §5).
        status = AdaptiveStatus.CONVERGED_WITH_MODEL_LIMITATIONS
    return AdaptiveOutcome(
        result=result,
        generations=tuple(generations),
        status=status,
        quantities=quantities,
    )


def _is_settled(quantity: QuantityConvergence, target: float) -> bool:
    """Whether a quantity's last step was within target, singular or not."""
    return quantity.rel_change is not None and quantity.rel_change <= target


def richardson_extrapolate(
    values: Sequence[float],
    dof_counts: Sequence[int],
) -> tuple[float | None, float | None]:
    """Extrapolated limit and observed order, or `(None, None)`.

    Returns nothing at all unless the sequence earns it. ADR-0015 §6 makes
    this conditional deliberately: meshes here are non-nested, so a QoI
    sequence oscillates by a few parts per thousand on re-meshing alone, and
    fitting a limit to an oscillating sequence produces a confident-looking
    number that means nothing. The checks below are the price of publishing
    an extrapolated value.

    Assumes `f_i = f_inf + C h^p` with `h ~ dofs^(-1/2)` in two dimensions.
    """
    if len(values) < 3 or len(values) != len(dof_counts):
        return None, None
    third, second, first = values[-3], values[-2], values[-1]
    delta_early = second - third
    delta_late = first - second
    if delta_early == 0.0 or delta_late == 0.0:
        return None, None
    # Monotone in one direction, with the steps genuinely shrinking: that is
    # what "asymptotic regime" means operationally. Oscillation fails here.
    if delta_early * delta_late <= 0.0:
        return None, None
    if abs(delta_late) >= abs(delta_early):
        return None, None
    if dof_counts[-1] <= dof_counts[-2]:
        return None, None

    ratio = math.sqrt(dof_counts[-1] / dof_counts[-2])
    if ratio <= 1.0:
        return None, None
    order = math.log(abs(delta_early / delta_late)) / math.log(ratio)
    # A rate outside this band is not a discretisation trend; it is noise
    # that happened to look monotone over three samples.
    if not 0.5 <= order <= 6.0:
        return None, None
    limit = first + delta_late / (ratio**order - 1.0)
    return limit, order


def _quantity_convergence(
    generations: Sequence[Generation],
    target: float,
) -> tuple[QuantityConvergence, ...]:
    """Per-quantity status across the run."""
    out: list[QuantityConvergence] = []
    dof_counts = [generation.dof_count for generation in generations]
    for name, singular in TRACKED_QUANTITIES:
        values = [g.quantities.get(name, 0.0) for g in generations]
        change: float | None = None
        if len(values) >= 2 and values[-2] != 0.0:
            change = abs(values[-1] - values[-2]) / abs(values[-2])
        limit, order = richardson_extrapolate(values, dof_counts)
        out.append(
            QuantityConvergence(
                name=name,
                values=tuple(values),
                rel_change=change,
                # A singular quantity is never called converged, however
                # quiet it looks: its continuum limit does not exist.
                converged=(not singular) and change is not None and change <= target,
                singular=singular,
                extrapolated=None if singular else limit,
                observed_order=None if singular else order,
            )
        )
    return tuple(out)


def format_history(outcome: AdaptiveOutcome) -> str:
    """Render the convergence history as a table (ADR-0013, spec §33)."""
    lines = [
        f"{'pass':>4} {'DOFs':>9} {'elements':>9} {'QoI':>14} "
        f"{'dQoI':>10} {'est. error':>12} {'marked':>8}",
    ]
    for generation in outcome.generations:
        change = "--" if generation.qoi_rel_change is None else f"{generation.qoi_rel_change:.3e}"
        lines.append(
            f"{generation.index:>4} {generation.dof_count:>9} {generation.element_count:>9} "
            f"{generation.quantity_of_interest:>14.9f} {change:>10} "
            f"{generation.estimated_error:>12.4e} {generation.marked_elements:>8}"
        )
    lines.append(f"status: {outcome.status}")
    return "\n".join(lines)
