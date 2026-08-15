/**
 * Viewport camera: the mapping between board coordinates (metres, y up) and
 * screen coordinates (CSS pixels, y down).
 *
 * Pure math, no DOM, so it is unit-testable. The camera never re-renders the
 * React tree -- the viewport owns it and redraws its canvas directly.
 */

export interface Camera {
  /** World point at the viewport centre, metres. */
  centerX_m: number;
  centerY_m: number;
  /** Zoom, CSS pixels per metre. */
  scale_px_per_m: number;
}

export interface WorldBounds {
  min_x_m: number;
  min_y_m: number;
  max_x_m: number;
  max_y_m: number;
}

/** Fraction of the viewport left as margin by `fitBounds`. */
export const FIT_MARGIN_FRACTION = 0.06;

/** Zoom limits: 100 px/mm x 1000 down to a board 1 px wide, roughly. */
const MAX_SCALE_PX_PER_M = 1e8;
const MIN_SCALE_PX_PER_M = 1e-1;

export function worldToScreen(
  camera: Camera,
  width_px: number,
  height_px: number,
  x_m: number,
  y_m: number,
): { x: number; y: number } {
  return {
    x: width_px / 2 + (x_m - camera.centerX_m) * camera.scale_px_per_m,
    y: height_px / 2 - (y_m - camera.centerY_m) * camera.scale_px_per_m,
  };
}

export function screenToWorld(
  camera: Camera,
  width_px: number,
  height_px: number,
  x_px: number,
  y_px: number,
): { x_m: number; y_m: number } {
  return {
    x_m: camera.centerX_m + (x_px - width_px / 2) / camera.scale_px_per_m,
    y_m: camera.centerY_m - (y_px - height_px / 2) / camera.scale_px_per_m,
  };
}

/** Frame `bounds` in a viewport of the given size, with a small margin. */
export function fitBounds(bounds: WorldBounds, width_px: number, height_px: number): Camera {
  const spanX_m = Math.max(bounds.max_x_m - bounds.min_x_m, 1e-6);
  const spanY_m = Math.max(bounds.max_y_m - bounds.min_y_m, 1e-6);
  const usable = 1 - 2 * FIT_MARGIN_FRACTION;
  const scale = Math.min((width_px * usable) / spanX_m, (height_px * usable) / spanY_m);
  return {
    centerX_m: (bounds.min_x_m + bounds.max_x_m) / 2,
    centerY_m: (bounds.min_y_m + bounds.max_y_m) / 2,
    scale_px_per_m: clampScale(scale),
  };
}

/** Pan by a screen-space delta (drag): the world follows the pointer. */
export function pan(camera: Camera, dx_px: number, dy_px: number): Camera {
  return {
    ...camera,
    centerX_m: camera.centerX_m - dx_px / camera.scale_px_per_m,
    centerY_m: camera.centerY_m + dy_px / camera.scale_px_per_m,
  };
}

/** Zoom by `factor`, keeping the world point under the cursor stationary. */
export function zoomAt(
  camera: Camera,
  width_px: number,
  height_px: number,
  x_px: number,
  y_px: number,
  factor: number,
): Camera {
  const scale = clampScale(camera.scale_px_per_m * factor);
  const applied = scale / camera.scale_px_per_m;
  if (applied === 1) {
    return camera;
  }
  const anchor = screenToWorld(camera, width_px, height_px, x_px, y_px);
  // After zooming, the anchor must map to the same screen point.
  return {
    scale_px_per_m: scale,
    centerX_m: anchor.x_m - (anchor.x_m - camera.centerX_m) / applied,
    centerY_m: anchor.y_m - (anchor.y_m - camera.centerY_m) / applied,
  };
}

/** Centre on a point, zooming so `radius_m` around it fills half the view. */
export function focusOn(
  width_px: number,
  height_px: number,
  x_m: number,
  y_m: number,
  radius_m: number,
): Camera {
  const target = Math.min(width_px, height_px) / 2;
  return {
    centerX_m: x_m,
    centerY_m: y_m,
    scale_px_per_m: clampScale(Math.min(target / Math.max(radius_m, 1e-6), MAX_SCALE_PX_PER_M)),
  };
}

function clampScale(scale: number): number {
  return Math.min(MAX_SCALE_PX_PER_M, Math.max(MIN_SCALE_PX_PER_M, scale));
}
