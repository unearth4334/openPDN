/**
 * Canvas 2D renderer for the board scene.
 *
 * One `draw` call repaints the whole viewport from cached `Path2D` batches:
 * layers bottom-to-top, then vias, then highlights. The canvas transform maps
 * world metres to device pixels, so pan/zoom is a transform change, never a
 * geometry rebuild (ADR-0008).
 *
 * Colours come in from CSS custom properties -- nothing here hard-codes a hex
 * value, so dark mode and future themes stay a stylesheet concern.
 */

import type { ViaResponse } from "../api/types";
import type { Camera } from "./camera";
import type { Scene } from "./scene";

export interface LayerPaint {
  layerId: string;
  color: string;
  opacity: number;
  visible: boolean;
}

export interface DrawOptions {
  /** CSS-pixel size of the canvas. */
  width_px: number;
  height_px: number;
  devicePixelRatio: number;
  /** Paint entries in stackup order, top first; drawn bottom-up. */
  layerPaints: LayerPaint[];
  /** Net to emphasise; other copper is dimmed when set. */
  highlightNetId: string | null;
  /** Region to outline as selected. */
  selectedRegionId: string | null;
  hoveredRegionId: string | null;
  vias: ViaResponse[];
  highlightedViaIds: ReadonlySet<string>;
  selectedViaId: string | null;
  colors: {
    profile: string;
    viaRing: string;
    viaHole: string;
    highlight: string;
    selection: string;
  };
}

/** Copper not on the highlighted net keeps this fraction of its alpha. */
const DIM_ALPHA = 0.14;

/** Minimum on-screen via radius so small vias stay clickable when zoomed out. */
const MIN_VIA_RADIUS_PX = 1.5;

export function draw(
  ctx: CanvasRenderingContext2D,
  scene: Scene,
  camera: Camera,
  options: DrawOptions,
): void {
  const { width_px, height_px, devicePixelRatio: dpr } = options;
  const scale = camera.scale_px_per_m * dpr;

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, width_px * dpr, height_px * dpr);

  // World metres -> device pixels, with the y axis flipped to y-up.
  ctx.setTransform(
    scale,
    0,
    0,
    -scale,
    (width_px * dpr) / 2 - camera.centerX_m * scale,
    (height_px * dpr) / 2 + camera.centerY_m * scale,
  );
  const px = (n: number) => (n * dpr) / scale; // n CSS pixels in world units

  // Board profile: a thin outline so copper reads against the board shape.
  if (scene.profile.path) {
    ctx.lineWidth = px(1);
    ctx.strokeStyle = options.colors.profile;
    ctx.stroke(scene.profile.path);
  }

  // Copper, bottom layer first so the top layer reads on top.
  for (let index = options.layerPaints.length - 1; index >= 0; index -= 1) {
    const paint = options.layerPaints[index];
    if (!paint?.visible) {
      continue;
    }
    const layer = scene.layers.find((candidate) => candidate.layerId === paint.layerId);
    if (!layer?.pathsByNet) {
      continue;
    }
    ctx.fillStyle = paint.color;
    for (const [netId, path] of layer.pathsByNet) {
      const dimmed = options.highlightNetId !== null && netId !== options.highlightNetId;
      ctx.globalAlpha = paint.opacity * (dimmed ? DIM_ALPHA : 1);
      ctx.fill(path, "evenodd");
    }
  }
  ctx.globalAlpha = 1;

  drawVias(ctx, options, px);
  drawRegionOutline(ctx, scene, options.hoveredRegionId, options.colors.highlight, px(1));
  drawRegionOutline(ctx, scene, options.selectedRegionId, options.colors.selection, px(1.5));
}

function drawVias(
  ctx: CanvasRenderingContext2D,
  options: DrawOptions,
  px: (n: number) => number,
): void {
  const visibleLayers = new Set(
    options.layerPaints.filter((paint) => paint.visible).map((paint) => paint.layerId),
  );
  for (const via of options.vias) {
    if (!visibleLayers.has(via.from_layer_id) && !visibleLayers.has(via.to_layer_id)) {
      continue;
    }
    const dimmed = options.highlightNetId !== null && via.net_id !== options.highlightNetId;
    const highlighted = options.highlightedViaIds.has(via.id) || via.id === options.selectedViaId;
    const drill_m = via.drill_diameter?.value ?? 0.15e-3;
    const holeRadius = drill_m / 2;
    const ringRadius = Math.max(holeRadius * 1.7, px(MIN_VIA_RADIUS_PX));

    ctx.globalAlpha = dimmed && !highlighted ? DIM_ALPHA : 1;
    ctx.beginPath();
    ctx.arc(via.x_m, via.y_m, ringRadius, 0, Math.PI * 2);
    ctx.fillStyle = highlighted ? options.colors.highlight : options.colors.viaRing;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(via.x_m, via.y_m, Math.max(holeRadius, px(0.6)), 0, Math.PI * 2);
    ctx.fillStyle = options.colors.viaHole;
    ctx.fill();

    if (via.id === options.selectedViaId) {
      ctx.beginPath();
      ctx.arc(via.x_m, via.y_m, ringRadius + px(2), 0, Math.PI * 2);
      ctx.lineWidth = px(1.5);
      ctx.strokeStyle = options.colors.selection;
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
}

function drawRegionOutline(
  ctx: CanvasRenderingContext2D,
  scene: Scene,
  regionId: string | null,
  color: string,
  lineWidth: number,
): void {
  if (regionId === null) {
    return;
  }
  const region = scene.regionById.get(regionId);
  if (!region?.path) {
    return;
  }
  ctx.lineWidth = lineWidth;
  ctx.strokeStyle = color;
  ctx.stroke(region.path);
}
