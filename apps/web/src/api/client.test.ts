import { afterEach, describe, expect, it, vi } from "vitest";
import { infoFixture } from "../test/fixtures";
import { ApiError, fetchHealth, fetchInfo } from "./client";

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
});
