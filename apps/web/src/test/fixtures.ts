import type { InfoResponse } from "../api/types";

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
