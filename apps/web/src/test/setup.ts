import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom implements neither ResizeObserver nor a canvas 2D context. The viewport
// needs the first to exist at all; it already tolerates the second being
// absent (it simply does not paint), which is what lets its sizing and
// interaction logic be tested without a real renderer.
if (!("ResizeObserver" in globalThis)) {
  class TestResizeObserver implements ResizeObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  globalThis.ResizeObserver = TestResizeObserver;
}

// jsdom logs a "not implemented" error for every getContext call. Returning
// null is the honest answer and is exactly what the viewport already handles.
HTMLCanvasElement.prototype.getContext = () => null;

afterEach(() => {
  cleanup();
});
