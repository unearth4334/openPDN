/**
 * The renderer-facing scene model.
 *
 * Built once per (board, view) from the geometry payload and cached: pan,
 * zoom, layer toggles and net highlighting reuse it without touching the API
 * or re-tessellating anything. Paths are in world metres; the canvas transform
 * does the scaling, so zooming never rebuilds geometry.
 *
 * `Path2D` construction is optional (absent under jsdom); hit-testing works on
 * the raw rings so it stays testable without a canvas.
 */

import type { GeometryResponse } from "../api/types";

export type Ring = [number, number][];

export interface SceneRegion {
  id: string;
  netId: string | null;
  layerId: string;
  exterior: Ring;
  holes: Ring[];
  sourceRefs: string[];
  sourceRegionIds: string[];
  /** World-space fill path (even-odd for holes); null without Path2D support. */
  path: Path2D | null;
  bbox: { minX: number; minY: number; maxX: number; maxY: number };
}

export interface SceneLayer {
  layerId: string;
  regions: SceneRegion[];
  /** One batched path per net for fast fills; null without Path2D support. */
  pathsByNet: Map<string | null, Path2D> | null;
}

export interface Scene {
  view: string;
  /** In stackup order, top first (matching the geometry payload). */
  layers: SceneLayer[];
  profile: { path: Path2D | null; rings: Ring[]; holes: Ring[][] };
  regionById: Map<string, SceneRegion>;
}

export function buildScene(geometry: GeometryResponse): Scene {
  const supportsPath2D = typeof Path2D !== "undefined";
  const regionById = new Map<string, SceneRegion>();

  const layers: SceneLayer[] = geometry.layers.map((layer) => {
    const regions = layer.regions.map((region) => {
      const built: SceneRegion = {
        id: region.id,
        netId: region.net_id,
        layerId: layer.layer_id,
        exterior: region.exterior,
        holes: region.holes,
        sourceRefs: region.source_refs,
        sourceRegionIds: region.source_region_ids,
        path: supportsPath2D ? regionPath(region.exterior, region.holes) : null,
        bbox: ringBbox(region.exterior),
      };
      regionById.set(built.id, built);
      return built;
    });

    let pathsByNet: Map<string | null, Path2D> | null = null;
    if (supportsPath2D) {
      pathsByNet = new Map();
      for (const region of regions) {
        let batch = pathsByNet.get(region.netId);
        if (!batch) {
          batch = new Path2D();
          pathsByNet.set(region.netId, batch);
        }
        appendRing(batch, region.exterior);
        for (const hole of region.holes) {
          appendRing(batch, hole);
        }
      }
    }
    return { layerId: layer.layer_id, regions, pathsByNet };
  });

  let profilePath: Path2D | null = null;
  if (supportsPath2D) {
    profilePath = new Path2D();
    for (const polygon of geometry.profile) {
      appendRing(profilePath, polygon.exterior);
      for (const hole of polygon.holes) {
        appendRing(profilePath, hole);
      }
    }
  }

  return {
    view: geometry.view,
    layers,
    profile: {
      path: profilePath,
      rings: geometry.profile.map((polygon) => polygon.exterior),
      holes: geometry.profile.map((polygon) => polygon.holes),
    },
    regionById,
  };
}

function regionPath(exterior: Ring, holes: Ring[]): Path2D {
  const path = new Path2D();
  appendRing(path, exterior);
  for (const hole of holes) {
    appendRing(path, hole);
  }
  return path;
}

function appendRing(path: Path2D, ring: Ring): void {
  if (ring.length === 0) {
    return;
  }
  const first = ring[0];
  if (!first) {
    return;
  }
  path.moveTo(first[0], first[1]);
  for (let index = 1; index < ring.length; index += 1) {
    const point = ring[index];
    if (point) {
      path.lineTo(point[0], point[1]);
    }
  }
  path.closePath();
}

function ringBbox(ring: Ring): SceneRegion["bbox"] {
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const [x, y] of ring) {
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  return { minX, minY, maxX, maxY };
}

/** Even-odd point-in-ring test (ray casting). */
export function pointInRing(ring: Ring, x: number, y: number): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const a = ring[i];
    const b = ring[j];
    if (!a || !b) {
      continue;
    }
    const [xi, yi] = a;
    const [xj, yj] = b;
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

/** True when the point is in the region's copper (outside its holes). */
export function pointInRegion(region: SceneRegion, x: number, y: number): boolean {
  const { bbox } = region;
  if (x < bbox.minX || x > bbox.maxX || y < bbox.minY || y > bbox.maxY) {
    return false;
  }
  if (!pointInRing(region.exterior, x, y)) {
    return false;
  }
  return !region.holes.some((hole) => pointInRing(hole, x, y));
}

/**
 * Topmost visible copper under a world point.
 *
 * Layers are scanned in the given order (pass them top-first); within a layer
 * the last-drawn region wins, matching what the eye sees.
 */
export function hitTest(
  scene: Scene,
  visibleLayerIdsTopFirst: string[],
  x_m: number,
  y_m: number,
): SceneRegion | null {
  for (const layerId of visibleLayerIdsTopFirst) {
    const layer = scene.layers.find((candidate) => candidate.layerId === layerId);
    if (!layer) {
      continue;
    }
    for (let index = layer.regions.length - 1; index >= 0; index -= 1) {
      const region = layer.regions[index];
      if (region && pointInRegion(region, x_m, y_m)) {
        return region;
      }
    }
  }
  return null;
}
