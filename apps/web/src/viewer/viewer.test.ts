import { describe, expect, it } from "vitest";
import type { GeometryResponse } from "../api/types";
import { fitBounds, pan, screenToWorld, worldToScreen, zoomAt } from "./camera";
import { resizeCanvasIfNeeded } from "./canvasSize";
import { buildScene, hitTest, pointInRegion } from "./scene";

describe("camera", () => {
  const bounds = { min_x_m: 0, min_y_m: 0, max_x_m: 0.05, max_y_m: 0.03 };

  it("fitBounds centres the board and fits the limiting axis", () => {
    const camera = fitBounds(bounds, 1000, 800);
    expect(camera.centerX_m).toBeCloseTo(0.025, 9);
    expect(camera.centerY_m).toBeCloseTo(0.015, 9);
    // 50 mm across 1000 px with margins: the x axis limits the scale.
    expect(camera.scale_px_per_m * 0.05).toBeLessThanOrEqual(1000);
  });

  it("screen and world transforms are inverses, with y flipped", () => {
    const camera = fitBounds(bounds, 1000, 800);
    const screen = worldToScreen(camera, 1000, 800, 0.01, 0.02);
    const world = screenToWorld(camera, 1000, 800, screen.x, screen.y);
    expect(world.x_m).toBeCloseTo(0.01, 12);
    expect(world.y_m).toBeCloseTo(0.02, 12);
    // Board y up: a larger world y is a smaller screen y.
    const higher = worldToScreen(camera, 1000, 800, 0.01, 0.025);
    expect(higher.y).toBeLessThan(screen.y);
  });

  it("zoomAt keeps the world point under the cursor stationary", () => {
    const camera = fitBounds(bounds, 1000, 800);
    const cursor = { x: 200, y: 300 };
    const before = screenToWorld(camera, 1000, 800, cursor.x, cursor.y);
    const zoomed = zoomAt(camera, 1000, 800, cursor.x, cursor.y, 2);
    const after = screenToWorld(zoomed, 1000, 800, cursor.x, cursor.y);
    expect(after.x_m).toBeCloseTo(before.x_m, 12);
    expect(after.y_m).toBeCloseTo(before.y_m, 12);
  });

  it("pan moves the view opposite to the drag so the world follows the pointer", () => {
    const camera = fitBounds(bounds, 1000, 800);
    const panned = pan(camera, 100, 0);
    expect(panned.centerX_m).toBeLessThan(camera.centerX_m);
  });
});

describe("scene hit testing", () => {
  const geometry: GeometryResponse = {
    board_id: "b",
    view: "normalized",
    bounds: null,
    profile: [],
    layers: [
      {
        layer_id: "top",
        regions: [
          {
            id: "r1",
            net_id: "n1",
            exterior: [
              [0, 0],
              [10, 0],
              [10, 10],
              [0, 10],
            ],
            holes: [
              [
                [4, 4],
                [6, 4],
                [6, 6],
                [4, 6],
              ],
            ],
            source_refs: ["src"],
            source_region_ids: ["a"],
          },
        ],
      },
      {
        layer_id: "bottom",
        regions: [
          {
            id: "r2",
            net_id: "n2",
            exterior: [
              [0, 0],
              [20, 0],
              [20, 20],
              [0, 20],
            ],
            holes: [],
            source_refs: [],
            source_region_ids: ["b"],
          },
        ],
      },
    ],
  };
  const scene = buildScene(geometry);

  it("finds copper but not holes", () => {
    const region = scene.regionById.get("r1");
    expect(region).toBeDefined();
    if (!region) {
      return;
    }
    expect(pointInRegion(region, 1, 1)).toBe(true);
    expect(pointInRegion(region, 5, 5)).toBe(false); // inside the void
    expect(pointInRegion(region, 15, 15)).toBe(false); // outside
  });

  it("prefers the topmost visible layer", () => {
    expect(hitTest(scene, ["top", "bottom"], 1, 1)?.id).toBe("r1");
    // With the top layer hidden the bottom plane is what the user sees.
    expect(hitTest(scene, ["bottom"], 1, 1)?.id).toBe("r2");
    // Through the top layer's void the bottom copper shows.
    expect(hitTest(scene, ["top", "bottom"], 5, 5)?.id).toBe("r2");
  });
});

describe("canvas backing-store sizing", () => {
  function canvasOf(width: number, height: number): HTMLCanvasElement {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    return canvas;
  }

  it("resizes when the pixel size changes", () => {
    const canvas = canvasOf(10, 10);
    const resized = resizeCanvasIfNeeded(canvas, {
      width_px: 400,
      height_px: 300,
      devicePixelRatio: 2,
    });
    expect(resized).toBe(true);
    expect(canvas.width).toBe(800);
    expect(canvas.height).toBe(600);
  });

  it("does not touch the bitmap when the size is unchanged", () => {
    // Assigning width/height clears the canvas even to the same value, so an
    // unconditional assignment blanks the board on every call. This test is
    // the guard against reintroducing that flicker.
    const canvas = canvasOf(800, 600);
    let assignments = 0;
    for (const property of ["width", "height"] as const) {
      const descriptor = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, property);
      if (!descriptor?.get || !descriptor.set) {
        throw new Error(`canvas ${property} accessor is unavailable`);
      }
      const { get, set } = descriptor;
      Object.defineProperty(canvas, property, {
        configurable: true,
        get,
        set(value: number) {
          assignments += 1;
          set.call(this, value);
        },
      });
    }

    const resized = resizeCanvasIfNeeded(canvas, {
      width_px: 400,
      height_px: 300,
      devicePixelRatio: 2,
    });

    expect(resized).toBe(false);
    expect(assignments).toBe(0);
  });

  it("never collapses to a zero-sized bitmap", () => {
    const canvas = canvasOf(800, 600);
    resizeCanvasIfNeeded(canvas, { width_px: 0, height_px: 0, devicePixelRatio: 1 });
    expect(canvas.width).toBe(1);
    expect(canvas.height).toBe(1);
  });
});
