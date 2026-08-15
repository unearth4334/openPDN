import type { BoardReviewResponse, GeometryResponse, InfoResponse } from "../api/types";

/** A deployment description matching what the current backend returns. */
export function infoFixture(overrides: Partial<InfoResponse> = {}): InfoResponse {
  return {
    name: "openPDN",
    version: "0.0.1",
    api_version: "v0",
    environment: "development",
    solvers: [
      {
        name: "mock",
        version: "0.0.1",
        summary: "Pipeline test double: returns boundary conditions, solves nothing",
        fidelity: "mock",
        available: true,
        unavailable_reason: null,
        supports_resistance_probes: false,
        supports_current_density: false,
      },
    ],
    importers: [
      {
        name: "canonical-json",
        version: "0.0.1",
        summary: "openPDN canonical board JSON",
        source_format: "openPDN canonical JSON",
        file_extensions: [".json"],
        available: true,
        unavailable_reason: null,
      },
    ],
    capabilities: [
      { name: "IPC-2581 import", status: "implemented", detail: null },
      { name: "Geometry normalisation", status: "implemented", detail: null },
      { name: "ODB++ import", status: "planned", detail: null },
      { name: "Canonical board model", status: "implemented", detail: null },
      { name: "IR-drop analysis", status: "planned", detail: null },
    ],
    ...overrides,
  };
}

/** A tiny imported board: one copper layer, one net, one via. */
export function boardReviewFixture(
  overrides: Partial<BoardReviewResponse> = {},
): BoardReviewResponse {
  const thickness = {
    value: 35e-6,
    unit: "m",
    provenance: "imported" as const,
    note: null,
  };
  return {
    board_id: "board-1",
    name: "fixture board",
    source_name: "fixture.xml",
    source_format: "IPC-2581",
    format_revision: "IPC-2581B",
    source_digest: "abc123",
    stored_at_epoch_s: 0,
    readiness: "ready_with_assumptions",
    capability_items: [{ name: "Copper geometry", status: "present", note: null }],
    diagnostics: [],
    bounds: { min_x_m: 0, min_y_m: 0, max_x_m: 0.02, max_y_m: 0.01 },
    total_thickness: { ...thickness, provenance: "derived" },
    layers: [
      {
        id: "layer-top",
        name: "Top",
        function: "signal",
        index: 0,
        is_conductive: true,
        thickness,
        z_top: { ...thickness, value: 0, provenance: "derived" },
        z_bottom: { ...thickness, provenance: "derived" },
        material_name: "Copper",
      },
    ],
    nets: [
      {
        id: "net-gnd",
        name: "GND",
        layer_ids: ["layer-top"],
        region_count: 1,
        via_count: 1,
        copper_area_m2: 1e-4,
        terminal_count: 0,
      },
    ],
    vias: [
      {
        id: "via-1",
        net_id: "net-gnd",
        x_m: 0.01,
        y_m: 0.005,
        from_layer_id: "layer-top",
        to_layer_id: "layer-top",
        span_kind: "through",
        drill_diameter: { ...thickness, value: 3e-4 },
        finished_hole_diameter: null,
        plating_thickness: null,
        padstack_name: "V300",
      },
    ],
    via_groups: [],
    components: [],
    terminals: [],
    layer_stats: [
      {
        layer_id: "layer-top",
        source_feature_count: 1,
        normalized_region_count: 1,
        copper_area_m2: 1e-4,
        net_count: 1,
        via_count: 1,
      },
    ],
    timings: {
      parse_seconds: 0.01,
      extract_seconds: 0.02,
      normalize_seconds: 0.03,
      source_bytes: 1024,
      element_count: 100,
      feature_counts: { contours: 1 },
      boolean_operations: 1,
      repaired_region_count: 0,
      discarded_degenerate_count: 0,
    },
    ...overrides,
  };
}

/** Renderable geometry matching `boardReviewFixture`: one square of copper. */
export function boardGeometryFixture(): GeometryResponse {
  return {
    board_id: "board-1",
    view: "normalized",
    bounds: { min_x_m: 0, min_y_m: 0, max_x_m: 0.02, max_y_m: 0.01 },
    profile: [
      {
        exterior: [
          [0, 0],
          [0.02, 0],
          [0.02, 0.01],
          [0, 0.01],
        ],
        holes: [],
      },
    ],
    layers: [
      {
        layer_id: "layer-top",
        regions: [
          {
            id: "region-1",
            net_id: "net-gnd",
            exterior: [
              [0.002, 0.002],
              [0.018, 0.002],
              [0.018, 0.008],
              [0.002, 0.008],
            ],
            holes: [],
            source_refs: ["Top/Set[0]/Features[0]/Contour[0]"],
            source_region_ids: ["r00001"],
          },
        ],
      },
    ],
  };
}
