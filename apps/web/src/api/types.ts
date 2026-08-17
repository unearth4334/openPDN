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

export interface TerminalPadResponse {
  id: string;
  layer_id: string;
  x_m: number;
  y_m: number;
}

export interface TerminalResponse {
  id: string;
  name: string;
  net_id: string;
  component_id: string | null;
  pad_ids: string[];
  pads: TerminalPadResponse[];
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

// --- Simulation -----------------------------------------------------------------

export type SimulationKind = "ir_drop" | "resistance";
export type AccuracyProfile = "preview" | "standard" | "high" | "verification" | "reference";

/**
 * Adaptive policy for a Reference run. Required by the `reference` profile
 * and rejected by the others: a fixed-mesh profile has nothing to adapt.
 * Server-side ceilings are stricter than these bounds and are not visible
 * here, so a request within them can still be refused.
 */
export type ReferenceTier = "low" | "medium" | "high";

export interface ReferencePolicyRequest {
  /** Measured preset seeding the fields below; explicit fields override. */
  tier?: ReferenceTier | null;
  target_qoi_rel_change?: number;
  max_passes?: number;
  max_dofs?: number;
  theta?: number;
  refinement_ratio?: number;
  element_order?: "p1" | "p2";
  goal_oriented?: boolean;
  linear_backend?: "auto" | "direct" | "iterative";
  linear_tolerance_fraction?: number;
}

/** What a finished Reference run is claiming. Anything but `converged`
 * means the answer must not be presented as a clean result. */
export type ReferenceQuality =
  | "converged"
  | "converged_with_model_limitations"
  | "resource_limited"
  | "not_converged"
  | "numerical_failure";
export type JobState =
  | "queued"
  | "claimed"
  | "running"
  | "completed"
  | "completed_with_warnings"
  | "failed"
  | "cancelling"
  | "cancelled";

export interface SimulationLoadRequest {
  terminal_ids?: string[];
  via_ids?: string[];
  current_a: number;
}

export interface ThicknessOverrideRequest {
  layer_id: string;
  thickness_um: number;
}

export interface SimulationDraftRequest {
  kind: SimulationKind;
  net_id: string;
  source_terminal_ids?: string[];
  source_via_ids?: string[];
  accuracy: AccuracyProfile;
  name?: string;
  source_voltage_v?: number;
  loads?: SimulationLoadRequest[];
  to_terminal_ids?: string[];
  to_via_ids?: string[];
  via_plating_um?: number | null;
  conductor_material?: "copper_annealed" | "custom" | null;
  conductor_conductivity_s_per_m?: number | null;
  thickness_overrides?: ThicknessOverrideRequest[];
  reference?: ReferencePolicyRequest | null;
}

export interface EstimateResponse {
  mesh_points: number;
  triangles: number;
  dofs: number;
  estimated_memory_bytes: number;
  compute_class: "low" | "moderate" | "high" | "very_high";
  over_budget: boolean;
  budget_dofs: number;
  connectivity_ok: boolean;
  connectivity_message: string | null;
  warnings: string[];
  assumptions: string[];
  duplicate_result_job_id: string | null;
}

export interface JobResponse {
  job_id: string;
  name: string;
  kind: SimulationKind;
  state: JobState;
  stage: string;
  message: string;
  accuracy: AccuracyProfile;
  net_id: string;
  net_name: string;
  board_id: string;
  created_at_epoch_s: number;
  finished_at_epoch_s: number | null;
  result_summary: Record<string, unknown> | null;
}

export interface QueueResponse {
  job: JobResponse;
  duplicate_of: string | null;
}

/** One layer of a result's field data, decoded from the binary payload. */
export interface ResultLayerFields {
  points: Float32Array;
  triangles: Uint32Array;
  voltage_v: Float32Array;
  j_a_per_m2: Float32Array;
  power_w: Float32Array;
}

export interface ResultMetrics {
  kind: SimulationKind;
  terminals: {
    terminal_id: string;
    voltage_v: number;
    current_a: number;
    is_source: boolean;
    // Absent on result artifacts published before multi-attachment groups.
    member_terminal_ids?: string[];
    member_via_ids?: string[];
  }[];
  vias: {
    via_id: string;
    upper_layer: string;
    lower_layer: string;
    x_m: number;
    y_m: number;
    conductance_s: number;
    voltage_upper_v: number;
    voltage_lower_v: number;
    current_a: number | null;
    power_w: number | null;
  }[];
  probes: { probe_id: string; resistance_ohm: number }[];
  nets: {
    net_id: string;
    max_voltage_v: number;
    min_voltage_v: number;
    ir_drop_v: number;
    max_j_a_per_m2: number | null;
    loss_w: number | null;
  }[];
  conservation: {
    residual: number;
    current_imbalance_fraction: number;
    power_mismatch_fraction: number;
    source_total_a: number;
    load_total_a: number;
    net_input_power_w: number;
    dissipated_power_w: number;
  };
  quality: {
    mesh_nodes: number | null;
    mesh_elements: number | null;
    matrix_nonzeros: number;
    residual: number | null;
    accuracy: AccuracyProfile;
  };
  engineering_quantities: Record<string, number>;
  convergence: {
    coarse_elements: number;
    fine_elements: number;
    target_fraction: number;
    worst_relative_change: number;
    converged: boolean;
    quantities: Record<string, { coarse: number; fine: number; relative_change: number }>;
  } | null;
  /**
   * Adaptive (Reference) history. A different shape from `convergence`
   * above and published under its own key on purpose: writing it into
   * `convergence` crashed every consumer that renders the Verification
   * comparison shape. Absent on fixed-mesh results.
   */
  reference?: {
    status: string;
    converged: boolean;
    generations: {
      index: number;
      dofs: number;
      elements: number;
      quantity_of_interest: number;
      qoi_rel_change: number | null;
      estimated_error: number;
      marked_elements: number;
      quantities: Record<string, number>;
      floor_clamped_seeds?: number;
    }[];
    quantities: {
      name: string;
      converged: boolean;
      singular: boolean;
      rel_change: number | null;
      extrapolated: number | null;
      observed_order: number | null;
    }[];
  } | null;
  diagnostics: {
    code: string;
    severity: "info" | "warning" | "error";
    message: string;
    context: Record<string, string>;
  }[];
  layer_files: { layer_id: string; file: string; points: number; triangles: number }[];
  timings_s: Record<string, number>;
  board_name: string;
}
