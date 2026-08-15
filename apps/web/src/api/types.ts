/**
 * Wire types mirroring the FastAPI response models.
 *
 * Hand-written on purpose while the API is small: these are the contract the UI
 * codes against, and a mismatch should surface as a type error rather than an
 * `any`. Once the API stabilises, generate them from /api/openapi.json.
 */

export type CapabilityStatus = "implemented" | "experimental" | "in_development" | "planned";

/** Where a physical value came from. Mirrors `openpdn.domain.provenance`. */
export type Provenance = "imported" | "configured" | "assumed" | "derived";

/** Physics a solver actually applied. `mock` means nothing was solved. */
export type ResultFidelity = "mock" | "sheet_2p5d" | "volume_3d";

export interface HealthResponse {
  status: "ok";
  name: string;
  version: string;
  api_version: string;
  environment: string;
}

export interface CapabilityResponse {
  name: string;
  status: CapabilityStatus;
  detail: string | null;
}

export interface SolverResponse {
  name: string;
  version: string;
  summary: string;
  fidelity: ResultFidelity;
  available: boolean;
  unavailable_reason: string | null;
  supports_resistance_probes: boolean;
  supports_current_density: boolean;
}

export interface ImporterResponse {
  name: string;
  version: string;
  summary: string;
  source_format: string;
  file_extensions: string[];
  available: boolean;
  unavailable_reason: string | null;
}

export interface InfoResponse {
  name: string;
  version: string;
  api_version: string;
  environment: string;
  solvers: SolverResponse[];
  importers: ImporterResponse[];
  capabilities: CapabilityResponse[];
}

/* --- Board review (mirrors apps/api/.../board_schemas.py) ------------------ */

/** How usable an imported board is for electrical analysis. */
export type SimulationReadiness = "ready" | "ready_with_assumptions" | "not_ready";

export type DiagnosticSeverity = "info" | "warning" | "error";

export type ViaSpanKind = "through" | "blind" | "buried" | "unknown";

export type GeometryViewName = "normalized" | "imported";

/** A physical value in SI units with its provenance. Display conversion is the UI's job. */
export interface QuantityResponse {
  value: number;
  unit: string;
  provenance: Provenance;
  note: string | null;
}

export interface BoundsResponse {
  min_x_m: number;
  min_y_m: number;
  max_x_m: number;
  max_y_m: number;
}

export interface DiagnosticResponse {
  code: string;
  severity: DiagnosticSeverity;
  message: string;
  context: Record<string, string>;
}

export interface CapabilityItemResponse {
  name: string;
  status: "present" | "partial" | "absent" | "unknown";
  note: string | null;
}

export interface LayerResponse {
  id: string;
  name: string;
  function: string;
  index: number;
  is_conductive: boolean;
  thickness: QuantityResponse | null;
  z_top: QuantityResponse | null;
  z_bottom: QuantityResponse | null;
  material_name: string | null;
}

export interface NetResponse {
  id: string;
  name: string;
  layer_ids: string[];
  region_count: number;
  via_count: number;
  copper_area_m2: number;
  terminal_count: number;
}

export interface ViaResponse {
  id: string;
  net_id: string | null;
  x_m: number;
  y_m: number;
  from_layer_id: string;
  to_layer_id: string;
  span_kind: ViaSpanKind;
  drill_diameter: QuantityResponse | null;
  finished_hole_diameter: QuantityResponse | null;
  plating_thickness: QuantityResponse | null;
  padstack_name: string | null;
}

export interface ViaGroupResponse {
  from_layer_id: string;
  to_layer_id: string;
  span_kind: ViaSpanKind;
  drill_diameter_m: number | null;
  padstack_name: string | null;
  count: number;
  via_ids: string[];
}

export interface ComponentResponse {
  id: string;
  reference_designator: string;
  part_number: string | null;
  terminal_count: number;
}

export interface TerminalResponse {
  id: string;
  name: string;
  net_id: string;
  component_id: string | null;
  pad_ids: string[];
}

export interface LayerStatsResponse {
  layer_id: string;
  source_feature_count: number;
  normalized_region_count: number;
  copper_area_m2: number;
  net_count: number;
  via_count: number;
}

export interface TimingsResponse {
  parse_seconds: number | null;
  extract_seconds: number | null;
  normalize_seconds: number | null;
  source_bytes: number | null;
  element_count: number | null;
  feature_counts: Record<string, number>;
  boolean_operations: number | null;
  repaired_region_count: number | null;
  discarded_degenerate_count: number | null;
}

export interface BoardReviewResponse {
  board_id: string;
  name: string;
  source_name: string;
  source_format: string;
  format_revision: string | null;
  source_digest: string | null;
  stored_at_epoch_s: number;
  readiness: SimulationReadiness;
  capability_items: CapabilityItemResponse[];
  diagnostics: DiagnosticResponse[];
  bounds: BoundsResponse | null;
  total_thickness: QuantityResponse | null;
  layers: LayerResponse[];
  nets: NetResponse[];
  vias: ViaResponse[];
  via_groups: ViaGroupResponse[];
  components: ComponentResponse[];
  terminals: TerminalResponse[];
  layer_stats: LayerStatsResponse[];
  timings: TimingsResponse;
}

export interface BoardListItemResponse {
  board_id: string;
  name: string;
  source_name: string;
  readiness: SimulationReadiness;
  stored_at_epoch_s: number;
}

export interface BoardListResponse {
  boards: BoardListItemResponse[];
}

/** One renderable copper polygon; rings are [x_m, y_m] pairs, unclosed. */
export interface RegionResponse {
  id: string;
  net_id: string | null;
  exterior: [number, number][];
  holes: [number, number][][];
  source_refs: string[];
  source_region_ids: string[];
}

export interface LayerGeometryResponse {
  layer_id: string;
  regions: RegionResponse[];
}

export interface ProfilePolygonResponse {
  exterior: [number, number][];
  holes: [number, number][][];
}

export interface GeometryResponse {
  board_id: string;
  view: GeometryViewName;
  bounds: BoundsResponse | null;
  profile: ProfilePolygonResponse[];
  layers: LayerGeometryResponse[];
}

export interface DevFixtureResponse {
  name: string;
}
