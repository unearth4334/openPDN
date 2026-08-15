import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { fetchGeometry } from "../api/client";
import { boardGeometryFixture, boardReviewFixture, infoFixture } from "../test/fixtures";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {
    readonly status = 0;
  },
  fetchHealth: vi.fn(),
  fetchInfo: vi.fn(async () => infoFixture()),
  fetchBoards: vi.fn(async () => ({ boards: [] })),
  fetchBoard: vi.fn(async () => boardReviewFixture()),
  fetchGeometry: vi.fn(async () => boardGeometryFixture()),
  importBoard: vi.fn(async () => boardReviewFixture()),
  fetchDevFixture: vi.fn(async () => ({ name: "fixture.xml" })),
  importDevFixture: vi.fn(async () => boardReviewFixture()),
}));

/** The board area jsdom would otherwise report as 0x0. */
const STAGE_RECT = {
  x: 0,
  y: 0,
  width: 800,
  height: 600,
  top: 0,
  left: 0,
  right: 800,
  bottom: 600,
};

let restoreRect: (() => void) | null = null;

beforeEach(() => {
  const original = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function rect() {
    return { ...STAGE_RECT, toJSON: () => STAGE_RECT } as DOMRect;
  };
  restoreRect = () => {
    HTMLElement.prototype.getBoundingClientRect = original;
  };
});

afterEach(() => {
  restoreRect?.();
  vi.clearAllMocks();
});

/** Mount the app and import the fixture board, returning the viewport canvas. */
async function openBoard(): Promise<HTMLCanvasElement> {
  render(<App />);
  fireEvent.click(await screen.findByText(/Load local fixture/));
  // The empty-state main is replaced wholesale by the board canvas, so the
  // canvas must be re-queried from the document rather than from a node
  // captured before the import finished.
  const canvas = await waitFor(() => {
    const found = screen.getByLabelText("PCB viewport").querySelector("canvas");
    if (!(found instanceof HTMLCanvasElement)) {
      throw new Error("viewport canvas has not mounted yet");
    }
    return found;
  });

  // Geometry arrives one fetch after the canvas mounts. Wait for that request
  // and let its promise settle, so no test races the scene into existence.
  // Firing events inside waitFor would livelock it: the callback's own DOM
  // mutation retriggers the observer that runs the callback.
  await waitFor(() => expect(vi.mocked(fetchGeometry)).toHaveBeenCalled());
  await act(async () => {
    await Promise.resolve();
  });
  return canvas;
}

/** Count assignments to the canvas backing store, which clear it. */
function countBitmapResets(canvas: HTMLCanvasElement): { resets: () => number; stop: () => void } {
  const prototype = Object.getPrototypeOf(canvas) as HTMLCanvasElement;
  const width = Object.getOwnPropertyDescriptor(prototype, "width");
  const height = Object.getOwnPropertyDescriptor(prototype, "height");
  if (!width?.set || !height?.set || !width.get || !height.get) {
    throw new Error("canvas width/height accessors are unavailable");
  }
  let resets = 0;
  const spy = (get: () => number, set: (value: number) => void) => ({
    configurable: true,
    get,
    set(this: HTMLCanvasElement, value: number) {
      resets += 1;
      set.call(this, value);
    },
  });
  Object.defineProperty(canvas, "width", spy(width.get, width.set));
  Object.defineProperty(canvas, "height", spy(height.get, height.set));
  return {
    resets: () => resets,
    stop: () => {
      delete (canvas as Partial<HTMLCanvasElement>).width;
      delete (canvas as Partial<HTMLCanvasElement>).height;
    },
  };
}

describe("Viewport", () => {
  it("renders a canvas once a board is imported", async () => {
    const canvas = await openBoard();
    expect(canvas).toBeInTheDocument();
  });

  it("does not resize the canvas bitmap while the pointer moves", async () => {
    // Assigning canvas.width/height clears the bitmap even when the value is
    // unchanged; doing it per pointer-move blanked the board every frame and
    // read as flicker. See viewer/canvasSize.ts.
    const canvas = await openBoard();
    const counter = countBitmapResets(canvas);
    const hud = screen.getByText(/^X:/);

    for (let step = 0; step < 12; step += 1) {
      fireEvent.pointerMove(canvas, { clientX: 100 + step * 20, clientY: 200 + step * 5 });
    }

    // Guard against a vacuous pass: the handler must actually have run.
    await waitFor(() => {
      expect(hud.textContent).not.toBe("X: —");
    });
    expect(counter.resets()).toBe(0);
    counter.stop();
  });

  it("reports pointer coordinates without re-rendering on every move", async () => {
    const canvas = await openBoard();
    const hudX = screen.getByText(/^X:/);
    const hudY = screen.getByText(/^Y:/);

    fireEvent.pointerMove(canvas, { clientX: 300, clientY: 300 });
    await waitFor(() => expect(hudX.textContent).toMatch(/mm$/));
    const first = hudX.textContent;

    fireEvent.pointerMove(canvas, { clientX: 500, clientY: 350 });
    await waitFor(() => expect(hudX.textContent).not.toBe(first));
    expect(hudY.textContent).toMatch(/mm$/);

    // The same DOM nodes are updated in place -- proof the tree did not
    // re-render for the readout.
    expect(screen.getByText(/^X:/)).toBe(hudX);

    fireEvent.pointerLeave(canvas);
    await waitFor(() => expect(hudX.textContent).toBe("X: —"));
  });
});
