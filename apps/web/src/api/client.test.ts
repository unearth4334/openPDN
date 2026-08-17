import { afterEach, describe, expect, it, vi } from "vitest";
import { infoFixture } from "../test/fixtures";
import { ApiError, fetchHealth, fetchInfo, queueSimulation } from "./client";
import type { SimulationDraftRequest } from "./types";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("requests /api/info", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => infoFixture(),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const info = await fetchInfo();

    expect(fetchMock).toHaveBeenCalledWith("/api/info", expect.anything());
    expect(info.version).toBe("0.0.1");
  });

  it("requests /api/health", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        status: "ok",
        name: "openPDN",
        version: "0.0.1",
        api_version: "v0",
        environment: "development",
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchHealth()).resolves.toMatchObject({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith("/api/health", expect.anything());
  });

  it("turns an HTTP failure into an ApiError carrying the status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}) })),
    );
    await expect(fetchInfo()).rejects.toBeInstanceOf(ApiError);
    await expect(fetchInfo()).rejects.toMatchObject({ status: 503 });
  });

  it("reports an unreachable backend as a network error, not a TypeError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    await expect(fetchInfo()).rejects.toMatchObject({ status: 0 });
  });

  it("serialises conductor material and thickness overrides in the queue request body", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ job: {} }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const draft: SimulationDraftRequest = {
      kind: "resistance",
      net_id: "net-1",
      source_terminal_ids: ["t-a"],
      to_terminal_ids: ["t-b"],
      accuracy: "standard",
      conductor_material: "custom",
      conductor_conductivity_s_per_m: 4.5e7,
      thickness_overrides: [{ layer_id: "L1", thickness_um: 35 }],
    };

    await queueSimulation("board-1", draft);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/boards/board-1/simulations",
      expect.objectContaining({ body: JSON.stringify(draft) }),
    );
  });
});
