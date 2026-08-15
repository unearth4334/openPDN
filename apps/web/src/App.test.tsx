import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { infoFixture } from "./test/fixtures";

function mockApi(payload: unknown, ok = true) {
  const fetchMock = vi.fn(async () => ({
    ok,
    status: ok ? 200 : 500,
    json: async () => payload,
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("App", () => {
  it("renders the engineering layout", () => {
    mockApi(infoFixture());
    render(<App />);
    expect(screen.getByLabelText("PCB viewport")).toBeInTheDocument();
    expect(screen.getByLabelText("Layers and nets")).toBeInTheDocument();
    expect(screen.getByLabelText("Inspector")).toBeInTheDocument();
    expect(screen.getByLabelText("Results and warnings")).toBeInTheDocument();
  });

  it("shows the backend version once /api/info answers", async () => {
    mockApi(infoFixture());
    render(<App />);
    // Scoped to the toolbar status: the version also appears in the console.
    const [status] = await screen.findAllByRole("status");
    expect(status).toHaveTextContent("openPDN 0.0.1");
    expect(status).toHaveTextContent("API v0");
  });

  it("reports honest capability statuses rather than assuming features exist", async () => {
    mockApi(infoFixture());
    render(<App />);
    await waitFor(() => expect(screen.getByText("IPC-2581 import")).toBeInTheDocument());
    // Statuses render exactly what /api/info reports; nothing is upgraded.
    expect(screen.getAllByText("planned")).toHaveLength(2);
    expect(screen.getAllByText("implemented")).toHaveLength(3);
  });

  it("warns when only the mock solver is available", async () => {
    mockApi(infoFixture());
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/No physical solver is installed/)).toBeInTheDocument(),
    );
  });

  it("says so plainly when the backend cannot be reached", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    render(<App />);
    await waitFor(() => expect(screen.getByText(/backend unavailable/)).toBeInTheDocument());
  });
});
