"""Adapter: the FEM plan primitives behind the `SimulationPlanner` port."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from openpdn.application.simulation_models import (
    ComputeClass,
    ConnectivityIssue,
    SimulationEstimate,
)
from openpdn.solver.fem.controls import MeshControls
from openpdn.solver.fem.plan import count_mesh_points, find_disconnection

if TYPE_CHECKING:
    from collections.abc import Sequence

    from openpdn.application.simulation_models import ResolvedMeshSpec
    from openpdn.domain.board import Board
    from openpdn.geometry.api import NormalizedGeometry

#: Bytes of working memory per degree of freedom for the SuperLU direct path.
#:
#: Not a constant in reality: a 2-D factorisation fills in superlinearly, so
#: the true figure *rises* with problem size. Measured on
#: `plane_neck_plane_board` (L+U non-zeros against DOF count, 12 bytes per
#: stored entry):
#:
#:     DOFs        fill ratio     bytes/DOF
#:     287             2.6x            189
#:     2,465           7.1x            578
#:     35,976         16.1x          1,342
#:     143,213        22.5x          1,880
#:
#: A single number therefore over-estimates small problems and *under*-
#: estimates large ones, which is the dangerous direction: a budget refusal
#: exists to stop a job that will not fit. This value is raised to cover the
#: top of the measured range with headroom rather than sitting mid-curve, so
#: the estimate errs towards refusing a job that would have fit rather than
#: admitting one that will not.
#:
#: The iterative backend (ADR-0014) has no factorisation and its memory is
#: roughly flat in DOFs, so this over-states it considerably -- which is
#: safe, and is why `AUTO` switching to iterative buys headroom.
BYTES_PER_DOF: Final = 2400

#: A planar Delaunay triangulation of n points has about 2n triangles.
TRIANGLES_PER_POINT: Final = 2.0

#: Compute-class thresholds by DOF count for the direct solver. Calibrated
#: against the accuracy-profile ladder (accuracy.py): on the reference board,
#: Preview/Standard/High/Verification land at roughly 17k/48k/134k/342k DOFs,
#: so each profile gets its own class rather than three of four reading
#: "very high".
CLASS_THRESHOLDS: Final = (
    (25_000, ComputeClass.LOW),
    (120_000, ComputeClass.MODERATE),
    (300_000, ComputeClass.HIGH),
)


class FemSimulationPlanner:
    """`SimulationPlanner` implementation for the fem-2p5d pipeline."""

    def estimate(
        self,
        board: Board,
        normalized: NormalizedGeometry,
        net_id: str,
        mesh: ResolvedMeshSpec,
        budget_dofs: int,
    ) -> SimulationEstimate:
        """Run the mesher's own point generation and count the result."""
        del board
        controls = MeshControls(
            max_size_m=mesh.max_element_m,
            min_size_m=mesh.min_element_m,
            elements_across_feature=mesh.elements_across_feature,
            growth_rate=mesh.growth_rate,
            refine_terminals=True,
        )
        points, warnings = count_mesh_points(normalized, net_id, controls)
        dofs = points  # Terminal collapsing removes a negligible few.
        compute = ComputeClass.VERY_HIGH
        for threshold, label in CLASS_THRESHOLDS:
            if dofs < threshold:
                compute = label
                break
        return SimulationEstimate(
            mesh_points=points,
            triangles=int(points * TRIANGLES_PER_POINT),
            dofs=dofs,
            estimated_memory_bytes=int(dofs * BYTES_PER_DOF),
            compute_class=compute,
            over_budget=dofs > budget_dofs,
            budget_dofs=budget_dofs,
            warnings=tuple(warnings),
        )

    def check_connectivity(
        self,
        board: Board,
        normalized: NormalizedGeometry,
        net_id: str,
        terminal_ids: Sequence[str],
        via_ids: Sequence[str] = (),
    ) -> ConnectivityIssue | None:
        """Region-graph reachability between the study's terminals and vias."""
        found = find_disconnection(board, normalized, net_id, terminal_ids, via_ids)
        if found is None:
            return None
        message, terminal_a, terminal_b = found
        return ConnectivityIssue(message=message, terminal_a=terminal_a, terminal_b=terminal_b)
