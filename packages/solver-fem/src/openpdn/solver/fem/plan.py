"""Pre-solve planning primitives: point-count estimation and connectivity.

Pure solver-side functions with no application-layer types, wrapped into the
application's `SimulationPlanner` port by the infrastructure adapter. They
live beside the mesher because an estimate is only honest if it runs the same
sizing logic the mesher will run -- these call the *actual* boundary sampling
and interior-lattice generation, skipping only triangulation and filtering.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

import numpy as np
import shapely
from shapely.geometry.polygon import orient

from openpdn.solver.fem.mesh import _interior_lattice, _sample_boundaries
from openpdn.solver.fem.problem import _to_shapely

if TYPE_CHECKING:
    from collections.abc import Sequence

    from openpdn.domain.board import Board
    from openpdn.geometry.api import NormalizedGeometry
    from openpdn.solver.fem.controls import MeshControls


def count_mesh_points(
    normalized: NormalizedGeometry, net_id: str, controls: MeshControls
) -> tuple[int, list[str]]:
    """Count the mesh points the sizing field would generate for one net.

    Returns the point count and any per-region estimation warnings.
    """
    total = 0
    warnings: list[str] = []
    for region in normalized.regions:
        if str(region.net_id or "") != net_id:
            continue
        # Same orientation normalisation as mesh_polygon: sizing rays point
        # inward only when the exterior winds CCW and holes CW.
        polygon = orient(_to_shapely(region.polygon), sign=1.0)
        try:
            boundary, sizes = _sample_boundaries(polygon, controls, region.id)
            interior = _interior_lattice(polygon, boundary, sizes, controls)
        except Exception as exc:  # An unestimable region is a warning, not a crash.
            warnings.append(f"Region {region.id}: estimation failed ({exc})")
            continue
        total += len(boundary) + len(interior)
    return total, warnings


def find_disconnection(
    board: Board,
    normalized: NormalizedGeometry,
    net_id: str,
    terminal_ids: Sequence[str],
    via_ids: Sequence[str] = (),
) -> tuple[str, str, str] | None:
    """Region-graph reachability between study terminals and vias.

    Regions are nodes; a via joins every studied region containing its
    position on a spanned conductive layer; terminals attach through their
    pads. `via_ids` names vias that are themselves attachment points (a
    source or load driving a via directly, with no terminal pad) and must
    also land in the same component as everything else. Returns `(message,
    endpoint_a, endpoint_b)` for the first failure, or None when every
    terminal and via shares one component. This is a cheap pre-check; the
    solver verifies connectivity exactly on the real mesh.
    """
    regions = [region for region in normalized.regions if str(region.net_id or "") == net_id]
    terminals_by_id = {str(t.id): t for t in board.terminals}
    if not regions:
        first = terminal_ids[0] if terminal_ids else (via_ids[0] if via_ids else "")
        return (f"Net {net_id!r} has no copper geometry", first, "")
    polygons = [_to_shapely(region.polygon) for region in regions]
    layer_index = {layer.id: layer.index for layer in board.stackup.layers}

    parent = list(range(len(regions)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    wanted_vias = set(via_ids)
    component_of_via: dict[str, int] = {}
    for via in normalized.vias:
        if str(via.net_id or "") != net_id:
            continue
        low = min(layer_index[via.from_layer_id], layer_index[via.to_layer_id])
        high = max(layer_index[via.from_layer_id], layer_index[via.to_layer_id])
        touched: list[int] = []
        for index, region in enumerate(regions):
            if not low <= layer_index[region.layer_id] <= high:
                continue
            if shapely.intersects_xy(
                polygons[index], np.float64(via.position.x_m), np.float64(via.position.y_m)
            ):
                touched.append(index)
        for a, b in itertools.pairwise(touched):
            union(a, b)
        if touched:
            for raw_id in wanted_vias.intersection(str(member) for member in via.via_ids):
                component_of_via.setdefault(raw_id, touched[0])

    pads_by_id = {pad.id: pad for pad in board.pads}
    component_of_terminal: dict[str, int] = {}
    for terminal_id in terminal_ids:
        terminal = terminals_by_id.get(terminal_id)
        if terminal is None:
            continue
        for pad_id in terminal.pad_ids:
            pad = pads_by_id.get(pad_id)
            if pad is None:
                continue
            for index, region in enumerate(regions):
                if region.layer_id != pad.layer_id:
                    continue
                if shapely.intersects_xy(
                    polygons[index],
                    np.float64(pad.position.x_m),
                    np.float64(pad.position.y_m),
                ):
                    if terminal_id in component_of_terminal:
                        union(component_of_terminal[terminal_id], index)
                    else:
                        component_of_terminal[terminal_id] = index
                    break

    for terminal_id in terminal_ids:
        if terminal_id not in component_of_terminal:
            terminal = terminals_by_id.get(terminal_id)
            name = terminal.name if terminal else terminal_id
            return (
                f"Terminal {name!r} touches no copper of net {net_id!r}",
                terminal_id,
                "",
            )
    for via_id in via_ids:
        if via_id not in component_of_via:
            return (
                f"Via {via_id!r} touches no copper of net {net_id!r}",
                via_id,
                "",
            )

    endpoints: list[tuple[str, int, str]] = [
        (terminal_id, component_of_terminal[terminal_id], terminals_by_id[terminal_id].name)
        for terminal_id in terminal_ids
    ] + [(via_id, component_of_via[via_id], f"via {via_id}") for via_id in via_ids]
    if not endpoints:
        return None
    first_id, first_component, first_name = endpoints[0]
    for other_id, other_component, other_name in endpoints[1:]:
        if find(other_component) != find(first_component):
            return (
                f"{first_name} and {other_name} lie on electrically disconnected copper "
                f"islands of net {net_id!r}",
                first_id,
                other_id,
            )
    return None
