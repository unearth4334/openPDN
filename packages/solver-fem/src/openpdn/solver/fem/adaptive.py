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

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from openpdn.solver.fem.controls import RefinementField
from openpdn.solver.fem.estimate import (
    dorfler_mark,
    flux_jump_indicators,
    global_error_estimate,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy.typing as npt

    from openpdn.domain.board import Board
    from openpdn.domain.results import ElectricalAnalysisResult
    from openpdn.domain.study import AnalysisStudy
    from openpdn.geometry.api import GeometryNormalizer
    from openpdn.solver.fem.problem import SheetProblem


class AdaptiveStatus:
    """Why the loop stopped. Mirrors the result states of ADR-0015."""

    CONVERGED = "converged"
    RESOURCE_LIMITED = "resource_limited"
    NOT_CONVERGED = "not_converged"


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


@dataclass(frozen=True, slots=True)
class AdaptiveOutcome:
    """The final result plus the history that justifies it."""

    result: ElectricalAnalysisResult
    generations: tuple[Generation, ...]
    status: str

    @property
    def converged(self) -> bool:
        """True only when every stopping criterion was met."""
        return self.status == AdaptiveStatus.CONVERGED

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
        result, field_data, problem = solve_with_controls(
            board, study, normalized, refinement=field
        )
        indicators = flux_jump_indicators(problem, field_data.node_voltage_v)
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
            else dorfler_mark(indicators, policy.theta)
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
    return AdaptiveOutcome(result=result, generations=tuple(generations), status=status)


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
