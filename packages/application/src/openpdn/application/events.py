"""Structured log event names.

Log lines are keyed by these constants, never by ad-hoc prose, so that events
stay greppable and dashboards keep working when a message is reworded. The
infrastructure logging adapter renders them; this module holds no logging
configuration itself.

Context keys expected alongside these events: `board_id`, `study_id`, `net`,
`solver`, `importer`, `duration_seconds`, `mesh_nodes`, `mesh_elements`.
Never log credentials, and never log full PCB geometry.
"""

from typing import Final

PCB_IMPORT_STARTED: Final = "pcb.import.started"
PCB_IMPORT_FINISHED: Final = "pcb.import.finished"
PCB_IMPORT_FAILED: Final = "pcb.import.failed"

GEOMETRY_NORMALIZATION_STARTED: Final = "geometry.normalization.started"
GEOMETRY_NORMALIZATION_FINISHED: Final = "geometry.normalization.finished"

MESH_GENERATED: Final = "solver.mesh.generated"
MATRIX_ASSEMBLED: Final = "solver.matrix.assembled"

SOLVER_STARTED: Final = "solver.started"
SOLVER_CONVERGED: Final = "solver.converged"
SOLVER_FAILED: Final = "solver.failed"
RESULTS_GENERATED: Final = "solver.results.generated"

CACHE_HIT: Final = "cache.hit"
CACHE_MISS: Final = "cache.miss"
