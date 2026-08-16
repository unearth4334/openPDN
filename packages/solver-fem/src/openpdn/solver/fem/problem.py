"""From board + study + normalised copper to an assembled sheet problem.

This module owns the electrically meaningful bookkeeping between meshing and
linear algebra:

* one `RegionMesh` per normalised copper polygon of the studied nets;
* a global vertex numbering across all regions and layers;
* **equipotential terminal regions**: every mesh vertex inside a terminal's
  pad copper collapses to one degree of freedom, across layers -- a
  through-hole pin's pads are shorted by the pin itself. A pad without an
  outline degrades to its nearest vertex with a
  `numerics.point_source_singularity` diagnostic (see the `fem-solver` skill);
* **lumped via segments**: consecutive connected conductive layers of a via
  are joined by the exact annular-barrel conductance computed from stackup
  z-positions -- no thin-wall approximation;
* the assembled sheet-conductance matrix `G` such that `G V = I`.

Nothing here applies a source voltage or a load current: excitations enter in
`solve.py`, which is what makes "change a load and re-solve" cheap (ADR-0003,
solver-development skill).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import scipy.sparse as sp
import shapely
from shapely.geometry import Polygon as ShapelyPolygon

from openpdn.domain.board import Layer, Pad, Via
from openpdn.domain.materials import COPPER_ANNEALED
from openpdn.domain.results import Diagnostic, DiagnosticSeverity
from openpdn.domain.units import KELVIN, METRE
from openpdn.solver.api import SolverConfigurationError
from openpdn.solver.fem.controls import MeshControls
from openpdn.solver.fem.errors import MeshGenerationError
from openpdn.solver.fem.mesh import RegionMesh, mesh_polygon

if TYPE_CHECKING:
    import numpy.typing as npt

    from openpdn.domain.board import Board, LayerId, NetId, PadId, TerminalId, ViaId
    from openpdn.domain.geometry import Polygon2D
    from openpdn.domain.study import AnalysisStudy, AttachmentGroup, LoadId, SourceId
    from openpdn.geometry.api import ConsolidatedVia, NormalizedGeometry


@dataclass(frozen=True, slots=True)
class RegionRef:
    """Identity and global-vertex range of one meshed copper region."""

    region_id: str
    layer_id: LayerId
    net_id: NetId | None
    node_start: int
    node_count: int
    tri_start: int
    tri_count: int


@dataclass(frozen=True, slots=True)
class TerminalBinding:
    """How one study terminal meets the mesh."""

    terminal_id: TerminalId
    dof: int
    node_count: int
    is_point_contact: bool


@dataclass(frozen=True, slots=True)
class ViaSegment:
    """One lumped barrel segment between two connected conductive layers."""

    via_id: str
    net_id: NetId | None
    upper_layer_id: LayerId
    lower_layer_id: LayerId
    dof_upper: int
    dof_lower: int
    conductance_s: float
    barrel_length_m: float
    position_x_m: float
    position_y_m: float


@dataclass(frozen=True)
class SheetProblem:
    """A fully assembled 2.5-D conduction problem, before excitation.

    `matrix` is the symmetric positive-semidefinite conductance matrix over
    the collapsed degrees of freedom; it becomes non-singular only once a
    Dirichlet condition is applied per connected component in `solve.py`.
    """

    points: npt.NDArray[np.float64]
    triangles: npt.NDArray[np.int32]
    tri_sheet_conductance: npt.NDArray[np.float64]
    tri_thickness_m: npt.NDArray[np.float64]
    tri_region_index: npt.NDArray[np.int32]
    regions: tuple[RegionRef, ...]
    dof_of_node: npt.NDArray[np.int64]
    n_dofs: int
    matrix: sp.csr_matrix
    terminals: dict[TerminalId, TerminalBinding]
    via_segments: tuple[ViaSegment, ...]
    component_of_dof: npt.NDArray[np.int64]
    #: DOF of each source/load's attachment group, after every member
    #: terminal and via has been unioned into one equipotential node.
    source_dofs: dict[SourceId, int]
    load_dofs: dict[LoadId, int]
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)

    @property
    def node_count(self) -> int:
        """Total mesh vertices across all regions."""
        return len(self.points)

    @property
    def element_count(self) -> int:
        """Total triangles across all regions."""
        return len(self.triangles)


def _to_shapely(polygon: Polygon2D) -> ShapelyPolygon:
    """Domain polygon to Shapely, exterior plus holes."""
    return ShapelyPolygon(
        [(p.x_m, p.y_m) for p in polygon.exterior],
        [[(p.x_m, p.y_m) for p in hole] for hole in polygon.holes],
    )


class _UnionFind:
    """Minimal union-find over integer node ids."""

    def __init__(self, size: int) -> None:
        self._parent = np.arange(size, dtype=np.int64)

    def find(self, i: int) -> int:
        """Root of `i` with path compression."""
        parent = self._parent
        root = i
        while parent[root] != root:
            root = int(parent[root])
        while parent[i] != root:
            parent[i], i = root, int(parent[i])
        return root

    def union(self, a: int, b: int) -> None:
        """Merge the sets containing `a` and `b`."""
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra

    def roots(self) -> npt.NDArray[np.int64]:
        """Root of every element, fully compressed."""
        for i in range(len(self._parent)):
            self.find(i)
        return self._parent.copy()


def build_problem(
    board: Board,
    study: AnalysisStudy,
    normalized: NormalizedGeometry,
    controls: MeshControls,
) -> SheetProblem:
    """Mesh the studied nets and assemble the conductance system.

    Raises:
        MeshGenerationError: A region could not be triangulated.
        SolverConfigurationError: Missing thickness/material, or a study
            terminal that touches no studied copper.
    """
    diagnostics: list[Diagnostic] = []
    layers_by_id = {layer.id: layer for layer in board.stackup.layers}
    pads_by_id = {pad.id: pad for pad in board.pads}

    studied_regions = [region for region in normalized.regions if region.net_id in study.net_id_set]
    if not studied_regions:
        raise SolverConfigurationError(
            f"No copper found for nets {sorted(study.net_id_set)!r}; nothing to solve"
        )

    via_params = _via_parameters(board, study, normalized, diagnostics)
    mandatory = _mandatory_points(board, study, normalized, pads_by_id, via_params)

    region_meshes: list[RegionMesh] = []
    region_refs: list[RegionRef] = []
    node_offset = 0
    tri_offset = 0
    shapely_regions: list[ShapelyPolygon] = []
    for region in studied_regions:
        polygon = _to_shapely(region.polygon)
        shapely_regions.append(polygon)
        wanted = _points_in(mandatory.get((region.net_id, region.layer_id)), polygon)
        label = f"{region.id} (layer {region.layer_id}, net {region.net_id or 'unassigned'})"
        try:
            mesh = mesh_polygon(polygon, controls, wanted, region_label=label)
        except MeshGenerationError:
            raise
        except Exception as exc:  # Meshing failures must always name the region.
            raise MeshGenerationError(f"Region {label}: {exc}") from exc
        region_meshes.append(mesh)
        region_refs.append(
            RegionRef(
                region_id=region.id,
                layer_id=region.layer_id,
                net_id=region.net_id,
                node_start=node_offset,
                node_count=len(mesh.points),
                tri_start=tri_offset,
                tri_count=len(mesh.triangles),
            )
        )
        node_offset += len(mesh.points)
        tri_offset += len(mesh.triangles)

    points = np.vstack([mesh.points for mesh in region_meshes])
    triangles = np.vstack(
        [
            mesh.triangles + ref.node_start
            for mesh, ref in zip(region_meshes, region_refs, strict=True)
        ]
    ).astype(np.int32)
    tri_region_index = np.concatenate(
        [np.full(ref.tri_count, index, dtype=np.int32) for index, ref in enumerate(region_refs)]
    )
    _report_mesh_quality(region_meshes, region_refs, diagnostics)

    sheet_conductance, thickness_by_region = _sheet_conductances(
        board, study, region_refs, layers_by_id, diagnostics
    )
    tri_sheet_conductance = sheet_conductance[tri_region_index]
    tri_thickness_m = thickness_by_region[tri_region_index]

    union = _UnionFind(len(points))
    terminal_nodes = _bind_terminal_nodes(
        board, study, region_refs, region_meshes, shapely_regions, pads_by_id, union, diagnostics
    )
    via_contacts = _bind_via_contacts(
        board, study, normalized, region_refs, region_meshes, via_params, union
    )
    via_id_to_consolidated = {
        raw_id: str(via.id) for via in normalized.vias for raw_id in via.via_ids
    }
    source_seeds, load_seeds = _bind_attachment_groups(
        study, terminal_nodes, via_contacts, via_id_to_consolidated, union
    )
    dof_of_node, n_dofs = _compress_dofs(union)

    source_dofs = {source_id: int(dof_of_node[seed]) for source_id, seed in source_seeds.items()}
    load_dofs = {load_id: int(dof_of_node[seed]) for load_id, seed in load_seeds.items()}

    via_segments = _via_segments(board, study, via_contacts, via_params, dof_of_node, diagnostics)

    matrix = _assemble(points, triangles, tri_sheet_conductance, dof_of_node, n_dofs, via_segments)

    component_of_dof = _components(matrix, n_dofs, via_segments)

    terminals = {
        terminal_id: TerminalBinding(
            terminal_id=terminal_id,
            dof=int(dof_of_node[nodes[0]]),
            node_count=len(nodes),
            is_point_contact=is_point,
        )
        for terminal_id, (nodes, is_point) in terminal_nodes.items()
    }

    return SheetProblem(
        points=points,
        triangles=triangles,
        tri_sheet_conductance=tri_sheet_conductance,
        tri_thickness_m=tri_thickness_m,
        tri_region_index=tri_region_index,
        regions=tuple(region_refs),
        dof_of_node=dof_of_node,
        n_dofs=n_dofs,
        matrix=matrix,
        terminals=terminals,
        via_segments=via_segments,
        component_of_dof=component_of_dof,
        source_dofs=source_dofs,
        load_dofs=load_dofs,
        diagnostics=tuple(diagnostics),
    )


# --- mandatory points ----------------------------------------------------------------


#: Ring points injected around each via centre so the contact disc has a
#: represented perimeter at any mesh size.
VIA_RING_POINTS = 8


@dataclass(frozen=True, slots=True)
class _ViaParams:
    """Electrical parameters of one consolidated via, resolved once."""

    contact_radius_m: float
    barrel_area_m2: float
    plating_assumed: bool


def _via_parameters(
    board: Board,
    study: AnalysisStudy,
    normalized: NormalizedGeometry,
    diagnostics: list[Diagnostic],
) -> dict[str, _ViaParams]:
    """Resolve barrel geometry for every studied via, before meshing.

    Raises:
        SolverConfigurationError: A studied via has neither hole geometry nor
            a plating assumption to fall back on.
    """
    vias_by_id = {str(via.id): via for via in board.vias}
    params: dict[str, _ViaParams] = {}
    assumed = 0
    for via in normalized.vias:
        if via.net_id not in study.net_id_set:
            continue
        board_via = _representative_board_via(via, vias_by_id)
        resolved = _barrel_geometry(board_via, study)
        if resolved is None:
            raise SolverConfigurationError(
                f"Via {via.id!r} lacks hole diameter and plating thickness; supply a "
                "plating assumption in the study before solving"
            )
        radius_m, area_m2, plating_assumed = resolved
        if plating_assumed:
            assumed += 1
        params[str(via.id)] = _ViaParams(
            contact_radius_m=radius_m, barrel_area_m2=area_m2, plating_assumed=plating_assumed
        )
    if assumed:
        diagnostics.append(
            Diagnostic(
                code="assumption.via_plating",
                severity=DiagnosticSeverity.WARNING,
                message=(
                    "Via barrel plating thickness is an assumption, not fabrication "
                    "data; via resistances scale inversely with it."
                ),
                context={"count": str(assumed)},
            )
        )
    return params


def _mandatory_points(
    board: Board,
    study: AnalysisStudy,
    normalized: NormalizedGeometry,
    pads_by_id: dict[PadId, Pad],
    via_params: dict[str, _ViaParams],
) -> dict[tuple[NetId | None, LayerId], npt.NDArray[np.float64]]:
    """Points that must become mesh vertices, keyed by (net, layer).

    Each via contributes its centre plus a ring at the barrel's outer radius
    (the equipotential contact disc, see `_bind_via_contacts`); pad outline
    vertices and centroids anchor equipotential terminal regions.
    """
    buckets: dict[tuple[NetId | None, LayerId], list[tuple[float, float]]] = {}

    def add(net_id: NetId | None, layer_id: LayerId, x: float, y: float) -> None:
        buckets.setdefault((net_id, layer_id), []).append((x, y))

    import math as _math

    conductive_in_span = _conductive_layers_by_index(board)
    for via in normalized.vias:
        if via.net_id not in study.net_id_set:
            continue
        params = via_params[str(via.id)]
        for layer in _layers_spanned(board, via, conductive_in_span):
            add(via.net_id, layer.id, via.position.x_m, via.position.y_m)
            for i in range(VIA_RING_POINTS):
                angle = 2.0 * _math.pi * i / VIA_RING_POINTS
                add(
                    via.net_id,
                    layer.id,
                    via.position.x_m + params.contact_radius_m * _math.cos(angle),
                    via.position.y_m + params.contact_radius_m * _math.sin(angle),
                )

    for terminal in board.terminals:
        if terminal.net_id not in study.net_id_set:
            continue
        for pad_id in terminal.pad_ids:
            pad = pads_by_id.get(pad_id)
            if pad is None:
                continue
            add(terminal.net_id, pad.layer_id, pad.position.x_m, pad.position.y_m)
            if pad.outline is not None:
                for point in pad.outline.exterior:
                    add(terminal.net_id, pad.layer_id, point.x_m, point.y_m)

    return {key: np.asarray(values, dtype=np.float64) for key, values in buckets.items()}


def _points_in(
    points: npt.NDArray[np.float64] | None, polygon: ShapelyPolygon
) -> npt.NDArray[np.float64] | None:
    """Subset of `points` inside `polygon`, or None."""
    if points is None or len(points) == 0:
        return None
    keep = shapely.intersects_xy(polygon, points[:, 0], points[:, 1])
    subset = points[keep]
    return subset if len(subset) else None


# --- materials -----------------------------------------------------------------------


def _sheet_conductances(
    board: Board,
    study: AnalysisStudy,
    region_refs: list[RegionRef],
    layers_by_id: dict[LayerId, Layer],
    diagnostics: list[Diagnostic],
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Per-region sheet conductance sigma*t and thickness, in SI units.

    Raises:
        SolverConfigurationError: A studied layer has no usable thickness or
            material -- named, not defaulted.
    """
    temperature_k = (
        study.temperature.require_unit(KELVIN) if study.temperature is not None else None
    )
    values = np.empty(len(region_refs), dtype=np.float64)
    thicknesses = np.empty(len(region_refs), dtype=np.float64)
    assumed_thickness_layers: set[str] = set()
    for index, ref in enumerate(region_refs):
        layer = layers_by_id[ref.layer_id]
        override = study.thickness_override_by_layer.get(ref.layer_id)
        if override is not None:
            thickness_m = override.require_unit(METRE)
        elif layer.thickness is not None:
            thickness_m = layer.thickness.require_unit(METRE)
            if layer.thickness.provenance.value == "assumed":
                assumed_thickness_layers.add(str(ref.layer_id))
        else:
            raise SolverConfigurationError(
                f"Layer {layer.name!r} has no copper thickness; supply one in the study"
            )
        material = study.conductor_material or layer.material
        if material is None:
            raise SolverConfigurationError(f"Layer {layer.name!r} has no conductor material")
        sigma = (
            material.conductivity_at_s_per_m(temperature_k)
            if temperature_k is not None
            else material.conductivity_s_per_m
        )
        values[index] = sigma * thickness_m
        thicknesses[index] = thickness_m
    for layer_name in sorted(assumed_thickness_layers):
        diagnostics.append(
            Diagnostic(
                code="assumption.layer_thickness",
                severity=DiagnosticSeverity.WARNING,
                message="Layer thickness is an assumed value, not fabrication data.",
                context={"layer": layer_name},
            )
        )
    return values, thicknesses


# --- terminals -----------------------------------------------------------------------


def _bind_terminal_nodes(
    board: Board,
    study: AnalysisStudy,
    region_refs: list[RegionRef],
    region_meshes: list[RegionMesh],
    shapely_regions: list[ShapelyPolygon],
    pads_by_id: dict[PadId, Pad],
    union: _UnionFind,
    diagnostics: list[Diagnostic],
) -> dict[TerminalId, tuple[list[int], bool]]:
    """Collapse each study terminal's pad copper into one equipotential DOF.

    Returns, per terminal, the global node indices merged and whether the
    terminal degraded to a point contact.

    Raises:
        SolverConfigurationError: A terminal that touches no studied copper.
    """
    needed: set[TerminalId] = set()
    for source in study.sources:
        needed.update(source.attachment.terminal_ids)
    for load in study.loads:
        needed.update(load.attachment.terminal_ids)
    for probe in study.probes:
        needed.add(probe.from_terminal_id)
        needed.add(probe.to_terminal_id)

    terminals_by_id = board.terminals_by_id
    result: dict[TerminalId, tuple[list[int], bool]] = {}
    for terminal_id in sorted(needed):
        terminal = terminals_by_id[terminal_id]
        nodes: list[int] = []
        point_contact = False
        for pad_id in terminal.pad_ids:
            pad = pads_by_id.get(pad_id)
            if pad is None:
                continue
            pad_nodes = _nodes_in_pad(pad, terminal.net_id, region_refs, region_meshes)
            if not pad_nodes:
                pad_nodes = _nearest_node_to(
                    pad.position.x_m,
                    pad.position.y_m,
                    terminal.net_id,
                    pad.layer_id,
                    region_refs,
                    region_meshes,
                )
                if pad_nodes:
                    point_contact = True
            nodes.extend(pad_nodes)
        if not nodes:
            raise SolverConfigurationError(
                f"Terminal {terminal.name!r} ({terminal_id}) touches no copper of "
                f"net {terminal.net_id!r} in the meshed geometry; it cannot carry current"
            )
        for node in nodes[1:]:
            union.union(nodes[0], node)
        if point_contact:
            diagnostics.append(
                Diagnostic(
                    code="numerics.point_source_singularity",
                    severity=DiagnosticSeverity.WARNING,
                    message=(
                        "Terminal degraded to a point contact; current density "
                        "adjacent to it is mesh-dependent and not physical."
                    ),
                    context={"terminal": str(terminal_id)},
                )
            )
        result[terminal_id] = (nodes, point_contact)
    return result


def _nodes_in_pad(
    pad: Pad,
    net_id: NetId,
    region_refs: list[RegionRef],
    region_meshes: list[RegionMesh],
) -> list[int]:
    """Global node indices inside a pad's outline on its layer and net."""
    if pad.outline is None:
        return []
    outline = _to_shapely(pad.outline)
    # A hair of tolerance keeps vertices lying exactly on the pad edge.
    outline = outline.buffer(1e-9)
    found: list[int] = []
    for ref, mesh in zip(region_refs, region_meshes, strict=True):
        if ref.layer_id != pad.layer_id or ref.net_id != net_id:
            continue
        inside = shapely.contains_xy(outline, mesh.points[:, 0], mesh.points[:, 1])
        found.extend((np.nonzero(inside)[0] + ref.node_start).tolist())
    return found


def _nearest_node_to(
    x_m: float,
    y_m: float,
    net_id: NetId,
    layer_id: LayerId,
    region_refs: list[RegionRef],
    region_meshes: list[RegionMesh],
) -> list[int]:
    """The single nearest node on (net, layer), as a point-contact fallback."""
    best: tuple[float, int] | None = None
    for ref, mesh in zip(region_refs, region_meshes, strict=True):
        if ref.layer_id != layer_id or ref.net_id != net_id:
            continue
        d2 = (mesh.points[:, 0] - x_m) ** 2 + (mesh.points[:, 1] - y_m) ** 2
        local = int(np.argmin(d2))
        if best is None or d2[local] < best[0]:
            best = (float(d2[local]), ref.node_start + local)
    return [] if best is None else [best[1]]


def _bind_attachment_groups(
    study: AnalysisStudy,
    terminal_nodes: dict[TerminalId, tuple[list[int], bool]],
    via_contacts: dict[str, _ViaContact],
    via_id_to_consolidated: dict[ViaId, str],
    union: _UnionFind,
) -> tuple[dict[SourceId, int], dict[LoadId, int]]:
    """Union every source/load attachment group's members into one seed node.

    A source or load may drive several terminals and vias at once (a BGA
    rail's pins, a pin plus a nearby via); this is where that group becomes
    one equipotential node, on top of the per-terminal and per-via-layer
    collapses `_bind_terminal_nodes`/`_bind_via_contacts` already performed.
    A via member contributes its *topmost connected layer's* node --
    `connections` is ordered top-to-bottom by `_bind_via_contacts` -- so the
    barrel's own interlayer resistance is preserved rather than shorted.

    Returns the pre-compression seed node for each source/load; callers look
    it up through `dof_of_node` once `_compress_dofs` has run.

    Raises:
        SolverConfigurationError: A via member touches no studied copper.
    """

    def _seed(attachment: AttachmentGroup) -> int:
        seeds: list[int] = [
            terminal_nodes[terminal_id][0][0] for terminal_id in attachment.terminal_ids
        ]
        for via_id in attachment.via_ids:
            consolidated_id = via_id_to_consolidated.get(via_id)
            contact = via_contacts.get(consolidated_id) if consolidated_id is not None else None
            if contact is None or not contact.connections:
                raise SolverConfigurationError(
                    f"Via {via_id!r} touches no studied copper in the meshed geometry; "
                    "it cannot carry current"
                )
            seeds.append(contact.connections[0][1])
        for node in seeds[1:]:
            union.union(seeds[0], node)
        return seeds[0]

    source_seeds: dict[SourceId, int] = {
        source.id: _seed(source.attachment) for source in study.sources
    }
    load_seeds: dict[LoadId, int] = {load.id: _seed(load.attachment) for load in study.loads}
    return source_seeds, load_seeds


def _compress_dofs(union: _UnionFind) -> tuple[npt.NDArray[np.int64], int]:
    """Number the union-find roots densely."""
    roots = union.roots()
    unique_roots, dof_of_node = np.unique(roots, return_inverse=True)
    return dof_of_node.astype(np.int64), len(unique_roots)


# --- vias ----------------------------------------------------------------------------


def _conductive_layers_by_index(board: Board) -> dict[int, Layer]:
    """Conductive layers keyed by stackup index."""
    return {layer.index: layer for layer in board.stackup.layers if layer.function.is_conductive}


def _layers_spanned(
    board: Board, via: ConsolidatedVia, conductive: dict[int, Layer]
) -> list[Layer]:
    """Conductive layers within the via's span, in stackup order."""
    layers_by_id = {layer.id: layer for layer in board.stackup.layers}
    top = layers_by_id[via.from_layer_id].index
    bottom = layers_by_id[via.to_layer_id].index
    low, high = min(top, bottom), max(top, bottom)
    return [conductive[i] for i in sorted(conductive) if low <= i <= high]


def _layer_z_midplanes(board: Board) -> dict[LayerId, float]:
    """Z of each layer's midplane, measured downward from the board top.

    Raises:
        SolverConfigurationError: A layer in the stackup has no thickness, so
            barrel lengths cannot be computed.
    """
    z = 0.0
    result: dict[LayerId, float] = {}
    for layer in sorted(board.stackup.layers, key=lambda item: item.index):
        if layer.thickness is None:
            raise SolverConfigurationError(
                f"Layer {layer.name!r} has no thickness; via barrel lengths need the "
                "full stackup. Supply thicknesses in the study."
            )
        t = layer.thickness.require_unit(METRE)
        result[layer.id] = z + t / 2.0
        z += t
    return result


@dataclass(frozen=True, slots=True)
class _ViaContact:
    """Where one via's barrel meets the mesh."""

    net_id: NetId | None
    position_x_m: float
    position_y_m: float
    #: (layer id, representative global node) per connected conductive layer.
    connections: tuple[tuple[LayerId, int], ...]


def _bind_via_contacts(
    board: Board,
    study: AnalysisStudy,
    normalized: NormalizedGeometry,
    region_refs: list[RegionRef],
    region_meshes: list[RegionMesh],
    via_params: dict[str, _ViaParams],
    union: _UnionFind,
) -> dict[str, _ViaContact]:
    """Collapse each via's contact disc into one DOF per connected layer.

    A single-node coupling would add a mesh-dependent, logarithmically
    growing spreading resistance under refinement. The barrel-plus-pad copper
    is locally near-equipotential, so all mesh nodes within the barrel's
    outer radius of the centre merge into one contact DOF (fem-solver skill).
    """
    conductive = _conductive_layers_by_index(board)
    contacts: dict[str, _ViaContact] = {}
    for via in normalized.vias:
        if via.net_id not in study.net_id_set:
            continue
        params = via_params[str(via.id)]
        connections: list[tuple[LayerId, int]] = []
        for layer in _layers_spanned(board, via, conductive):
            nodes = _nodes_within(
                via.position.x_m,
                via.position.y_m,
                params.contact_radius_m * (1.0 + 1e-9) + 1e-12,
                via.net_id,
                layer.id,
                region_refs,
                region_meshes,
            )
            if not nodes:
                continue
            for node in nodes[1:]:
                union.union(nodes[0], node)
            connections.append((layer.id, nodes[0]))
        contacts[str(via.id)] = _ViaContact(
            net_id=via.net_id,
            position_x_m=via.position.x_m,
            position_y_m=via.position.y_m,
            connections=tuple(connections),
        )
    return contacts


def _nodes_within(
    x_m: float,
    y_m: float,
    radius_m: float,
    net_id: NetId | None,
    layer_id: LayerId,
    region_refs: list[RegionRef],
    region_meshes: list[RegionMesh],
) -> list[int]:
    """Global node indices within a disc on one (net, layer)."""
    found: list[int] = []
    r2 = radius_m**2
    for ref, mesh in zip(region_refs, region_meshes, strict=True):
        if ref.layer_id != layer_id or ref.net_id != net_id:
            continue
        d2 = (mesh.points[:, 0] - x_m) ** 2 + (mesh.points[:, 1] - y_m) ** 2
        found.extend((np.nonzero(d2 <= r2)[0] + ref.node_start).tolist())
    return found


def _via_segments(
    board: Board,
    study: AnalysisStudy,
    via_contacts: dict[str, _ViaContact],
    via_params: dict[str, _ViaParams],
    dof_of_node: npt.NDArray[np.int64],
    diagnostics: list[Diagnostic],
) -> tuple[ViaSegment, ...]:
    """Build lumped conductance segments between consecutive contact layers."""
    if not via_contacts:
        return ()
    layers_by_id = {layer.id: layer for layer in board.stackup.layers}

    material = study.conductor_material or COPPER_ANNEALED
    temperature_k = (
        study.temperature.require_unit(KELVIN) if study.temperature is not None else None
    )
    sigma = (
        material.conductivity_at_s_per_m(temperature_k)
        if temperature_k is not None
        else material.conductivity_s_per_m
    )

    z_midplanes: dict[LayerId, float] | None = None
    segments: list[ViaSegment] = []
    dangling = 0
    for via_id, contact in via_contacts.items():
        if len(contact.connections) < 2:
            dangling += 1
            continue
        if z_midplanes is None:
            z_midplanes = _layer_z_midplanes(board)
        params = via_params[via_id]
        ordered = sorted(contact.connections, key=lambda item: layers_by_id[item[0]].index)
        for (upper_id, node_u), (lower_id, node_l) in itertools.pairwise(ordered):
            length_m = z_midplanes[lower_id] - z_midplanes[upper_id]
            if length_m <= 0.0:
                continue
            segments.append(
                ViaSegment(
                    via_id=via_id,
                    net_id=contact.net_id,
                    upper_layer_id=upper_id,
                    lower_layer_id=lower_id,
                    dof_upper=int(dof_of_node[node_u]),
                    dof_lower=int(dof_of_node[node_l]),
                    conductance_s=sigma * params.barrel_area_m2 / length_m,
                    barrel_length_m=length_m,
                    position_x_m=contact.position_x_m,
                    position_y_m=contact.position_y_m,
                )
            )
    if dangling:
        diagnostics.append(
            Diagnostic(
                code="via.dangling",
                severity=DiagnosticSeverity.INFO,
                message=(
                    "Vias connecting fewer than two studied copper layers carry no "
                    "current and were left out of the network."
                ),
                context={"count": str(dangling)},
            )
        )
    return tuple(segments)


def _representative_board_via(via: ConsolidatedVia, vias_by_id: dict[str, Via]) -> Via | None:
    """The first member board via carrying physical parameters."""
    for via_id in via.via_ids:
        board_via = vias_by_id.get(str(via_id))
        if board_via is not None:
            return board_via
    return None


def _barrel_geometry(via: Via | None, study: AnalysisStudy) -> tuple[float, float, bool] | None:
    """Resolve (outer radius m, annular area m^2, plating assumed) for a via.

    The exact annulus `pi[(r+t)^2 - r^2]` is used, never the thin-wall
    approximation `2 pi r t` (fem-solver skill). A missing finished hole
    diameter is derived from the drill: plating deposits inward, so
    `finished = drill - 2 * plating`.
    """
    if via is None:
        return None
    import math

    plating = via.plating_thickness
    plating_assumed = False
    if plating is None:
        plating = study.via_plating_thickness
        plating_assumed = plating is not None
    if plating is None:
        return None
    plating_m = plating.require_unit(METRE)

    if via.finished_hole_diameter is not None:
        inner_m = via.finished_hole_diameter.require_unit(METRE) / 2.0
    elif via.drill_diameter is not None:
        inner_m = via.drill_diameter.require_unit(METRE) / 2.0 - plating_m
        if inner_m <= 0.0:
            # Fully filled barrel: conduct through the whole drill cylinder.
            radius_m = via.drill_diameter.require_unit(METRE) / 2.0
            return radius_m, math.pi * radius_m**2, plating_assumed
    else:
        return None
    outer_m = inner_m + plating_m
    return outer_m, math.pi * (outer_m**2 - inner_m**2), plating_assumed


# --- assembly ------------------------------------------------------------------------


def _assemble(
    points: npt.NDArray[np.float64],
    triangles: npt.NDArray[np.int32],
    tri_sheet_conductance: npt.NDArray[np.float64],
    dof_of_node: npt.NDArray[np.int64],
    n_dofs: int,
    via_segments: tuple[ViaSegment, ...],
) -> sp.csr_matrix:
    """Assemble the global conductance matrix, in double precision.

    Per linear (P1) triangle with vertices `p1 p2 p3`, area `A` and sheet
    conductance `Gs = sigma * t`:

        K_e = Gs / (4A) * (b b^T + c c^T)

    with `b_i = y_j - y_k`, `c_i = x_k - x_j` (cyclic). This is the standard
    stiffness of `div(Gs grad V) = 0`; via segments add `G` on the diagonal
    and `-G` off-diagonal for their DOF pair.
    """
    p = points[triangles]  # (m, 3, 2)
    x = p[:, :, 0]
    y = p[:, :, 1]
    b = np.stack([y[:, 1] - y[:, 2], y[:, 2] - y[:, 0], y[:, 0] - y[:, 1]], axis=1)
    c = np.stack([x[:, 2] - x[:, 1], x[:, 0] - x[:, 2], x[:, 1] - x[:, 0]], axis=1)
    area2 = x[:, 0] * b[:, 0] + x[:, 1] * b[:, 1] + x[:, 2] * b[:, 2]
    area = np.abs(area2) / 2.0
    scale = tri_sheet_conductance / (4.0 * np.maximum(area, 1e-300))

    local = (b[:, :, None] * b[:, None, :] + c[:, :, None] * c[:, None, :]) * scale[:, None, None]

    dofs = dof_of_node[triangles]  # (m, 3)
    rows = np.repeat(dofs, 3, axis=1).ravel()
    cols = np.tile(dofs, (1, 3)).ravel()
    data = local.reshape(len(triangles), 9).ravel()

    via_rows: list[int] = []
    via_cols: list[int] = []
    via_data: list[float] = []
    for segment in via_segments:
        a, bb, g = segment.dof_upper, segment.dof_lower, segment.conductance_s
        via_rows.extend((a, bb, a, bb))
        via_cols.extend((a, bb, bb, a))
        via_data.extend((g, g, -g, -g))

    all_rows = np.concatenate([rows, np.asarray(via_rows, dtype=np.int64)])
    all_cols = np.concatenate([cols, np.asarray(via_cols, dtype=np.int64)])
    all_data = np.concatenate([data, np.asarray(via_data, dtype=np.float64)])

    matrix = sp.coo_matrix(
        (all_data, (all_rows, all_cols)), shape=(n_dofs, n_dofs), dtype=np.float64
    ).tocsr()
    matrix.sum_duplicates()
    return matrix


def _components(
    matrix: sp.csr_matrix, n_dofs: int, via_segments: tuple[ViaSegment, ...]
) -> npt.NDArray[np.int64]:
    """Connected-component label per DOF, from the matrix sparsity pattern."""
    del via_segments  # already stamped into the matrix pattern
    n_components, labels = sp.csgraph.connected_components(
        matrix, directed=False, return_labels=True
    )
    del n_components
    return labels.astype(np.int64)


def _report_mesh_quality(
    region_meshes: list[RegionMesh],
    region_refs: list[RegionRef],
    diagnostics: list[Diagnostic],
) -> None:
    """Aggregate coverage/angle statistics into result diagnostics."""
    worst_coverage = min(mesh.quality.coverage_ratio for mesh in region_meshes)
    slivers = sum(mesh.quality.sliver_count for mesh in region_meshes)
    if worst_coverage < 0.985:
        worst_ref = min(
            zip(region_refs, region_meshes, strict=True),
            key=lambda item: item[1].quality.coverage_ratio,
        )[0]
        diagnostics.append(
            Diagnostic(
                code="mesh.low_coverage",
                severity=DiagnosticSeverity.WARNING,
                message=(
                    "The mesh covers less of the copper area than expected; narrow "
                    "features may be under-resolved. Refine the mesh settings."
                ),
                context={
                    "worst_region": worst_ref.region_id,
                    "coverage": f"{worst_coverage:.4f}",
                },
            )
        )
    if slivers:
        diagnostics.append(
            Diagnostic(
                code="mesh.sliver_elements",
                severity=DiagnosticSeverity.INFO,
                message="Some triangles have interior angles below 5 degrees.",
                context={"count": str(slivers)},
            )
        )
