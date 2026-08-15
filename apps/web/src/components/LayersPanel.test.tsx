import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { boardGeometryFixture, boardReviewFixture, infoFixture } from "../test/fixtures";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {
    readonly status = 0;
  },
  fetchHealth: vi.fn(),
  fetchInfo: vi.fn(async () => infoFixture()),
  fetchBoards: vi.fn(async () => ({ boards: [] })),
  fetchBoard: vi.fn(async () => reviewWithTwoLayerVia()),
  fetchGeometry: vi.fn(async () => boardGeometryFixture()),
  importBoard: vi.fn(async () => reviewWithTwoLayerVia()),
  fetchDevFixture: vi.fn(async () => ({ name: "fixture.xml" })),
  importDevFixture: vi.fn(async () => reviewWithTwoLayerVia()),
}));

/** Two conductive layers with one through-via spanning both. */
function reviewWithTwoLayerVia() {
  const review = boardReviewFixture();
  const bottomLayer = { ...review.layers[0], id: "layer-bottom", name: "Bottom", index: 1 };
  return {
    ...review,
    layers: [...review.layers, bottomLayer],
    vias: [{ ...review.vias[0], from_layer_id: "layer-top", to_layer_id: "layer-bottom" }],
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

async function openBoard(): Promise<void> {
  render(<App />);
  fireEvent.click(await screen.findByText(/Load local fixture/));
  await waitFor(() => {
    expect(screen.getByLabelText("Show vias Top to Bottom")).toBeInTheDocument();
  });
}

describe("LayersPanel via stack columns", () => {
  it("renders one toggle per distinct via span, and a cell per layer row it touches", async () => {
    await openBoard();
    const toggle = screen.getByLabelText("Show vias Top to Bottom");
    expect(toggle).toBeInTheDocument();
    const cells = document.querySelectorAll(".via-stack__cell--filled");
    expect(cells).toHaveLength(2); // one for each of the two conductive layers
  });

  it("toggling a via span off then on again restores its checked state", async () => {
    await openBoard();
    const toggle = screen.getByLabelText<HTMLInputElement>("Show vias Top to Bottom");
    expect(toggle.checked).toBe(true);
    fireEvent.click(toggle);
    expect(toggle.checked).toBe(false);
    fireEvent.click(toggle);
    expect(toggle.checked).toBe(true);
  });

  it("All vias restores every hidden span", async () => {
    await openBoard();
    const toggle = screen.getByLabelText<HTMLInputElement>("Show vias Top to Bottom");
    fireEvent.click(toggle);
    expect(toggle.checked).toBe(false);
    fireEvent.click(screen.getByText("All vias"));
    expect(toggle.checked).toBe(true);
  });

  it("layer rows are unaffected by via-span visibility state", async () => {
    await openBoard();
    fireEvent.click(screen.getByLabelText<HTMLInputElement>("Show vias Top to Bottom"));
    const topLayerToggle = screen.getByLabelText<HTMLInputElement>("Show Top");
    expect(topLayerToggle.checked).toBe(true);
  });
});
