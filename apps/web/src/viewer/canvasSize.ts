/**
 * Canvas backing-store sizing.
 *
 * Assigning `canvas.width` or `canvas.height` resets the bitmap to transparent
 * black — the HTML specification requires it *even when the assigned value is
 * unchanged*. A viewport that re-asserts its size on every render therefore
 * blanks the board on every render, and with the repaint deferred to the next
 * animation frame the user sees it flicker.
 *
 * So the assignment lives here, behind a name that says the rule out loud, and
 * happens only when the pixel size genuinely changed.
 */

export interface CanvasPixelSize {
  width_px: number;
  height_px: number;
  devicePixelRatio: number;
}

/**
 * Resize `canvas`'s backing store to `size`, if and only if it differs.
 *
 * @returns true when the bitmap was reallocated (and therefore cleared), so
 *   the caller knows it must repaint synchronously rather than waiting.
 */
export function resizeCanvasIfNeeded(canvas: HTMLCanvasElement, size: CanvasPixelSize): boolean {
  const width = Math.max(1, Math.round(size.width_px * size.devicePixelRatio));
  const height = Math.max(1, Math.round(size.height_px * size.devicePixelRatio));
  let resized = false;
  if (canvas.width !== width) {
    canvas.width = width;
    resized = true;
  }
  if (canvas.height !== height) {
    canvas.height = height;
    resized = true;
  }
  return resized;
}
