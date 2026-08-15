/**
 * FEM result overlay: colour-mapped triangle batches for the Canvas renderer.
 *
 * The same Path2D-batching trick as the copper scene (ADR-0008): triangles are
 * bucketed into a fixed number of colour bins by their scalar value, one
 * world-coordinate Path2D per bin, so a repaint is ~48 fills regardless of
 * mesh size and pan/zoom stays a transform change. Batches are rebuilt only
 * when the result, field, layer or scale changes — never per frame.
 *
 * Units are explicit at this boundary: field arrays arrive in SI (V, A/m²,
 * W per element) and display conversions (mV, A/mm², W/mm²) happen in the
 * formatting helpers, not silently in the data.
 */

import type { ResultLayerFields } from "../api/types";
import type { ResultFieldKind } from "../state/boardState";

/** Number of colour bins. Enough for smooth-looking gradients; few enough
 * that a repaint is a handful of Path2D fills. */
export const COLOR_BINS = 48;

/** Auto-scale |J| and power at this area percentile: isolated singular peaks
 * at pad corners would otherwise flatten the whole map (fem-solver skill). */
export const CLIP_PERCENTILE = 0.995;

/** Viridis-like perceptually uniform colormap stops (dark→bright). */
const STOPS: [number, number, number][] = [
  [68, 1, 84],
  [71, 44, 122],
  [59, 81, 139],
  [44, 113, 142],
  [33, 144, 141],
  [39, 173, 129],
  [92, 200, 99],
  [170, 220, 50],
  [253, 231, 37],
];

/** Map t in [0,1] to a CSS colour on the viridis ramp. */
export function colorFor(t: number): string {
  const clamped = Math.min(1, Math.max(0, t));
  const scaled = clamped * (STOPS.length - 1);
  const index = Math.min(STOPS.length - 2, Math.floor(scaled));
  const f = scaled - index;
  const a = STOPS[index] ?? [0, 0, 0];
  const b = STOPS[index + 1] ?? a;
  const r = Math.round(a[0] + (b[0] - a[0]) * f);
  const g = Math.round(a[1] + (b[1] - a[1]) * f);
  const bl = Math.round(a[2] + (b[2] - a[2]) * f);
  return `rgb(${r},${g},${bl})`;
}

/** Per-triangle scalar values for a display field, in SI units. */
export function triangleScalars(
  fields: ResultLayerFields,
  kind: ResultFieldKind,
  referenceVoltageV: number,
): Float64Array {
  const triCount = fields.triangles.length / 3;
  const values = new Float64Array(triCount);
  if (kind === "j") {
    for (let i = 0; i < triCount; i += 1) {
      values[i] = fields.j_a_per_m2[i] ?? 0;
    }
    return values;
  }
  if (kind === "power") {
    // Stored per-element dissipated power [W]; display as areal density
    // [W/m²] so the value is mesh-independent.
    for (let i = 0; i < triCount; i += 1) {
      const area = triangleArea(fields, i);
      values[i] = area > 0 ? (fields.power_w[i] ?? 0) / area : 0;
    }
    return values;
  }
  // Voltage fields: mean of the three vertices (P1 elements are linear, and
  // 48 display bins make sub-element shading invisible anyway).
  const tris = fields.triangles;
  const v = fields.voltage_v;
  for (let i = 0; i < triCount; i += 1) {
    const mean =
      ((v[tris[i * 3] ?? 0] ?? 0) +
        (v[tris[i * 3 + 1] ?? 0] ?? 0) +
        (v[tris[i * 3 + 2] ?? 0] ?? 0)) /
      3;
    values[i] = kind === "voltage_drop" ? referenceVoltageV - mean : mean;
  }
  return values;
}

function triangleArea(fields: ResultLayerFields, index: number): number {
  const t = fields.triangles;
  const p = fields.points;
  const a = t[index * 3] ?? 0;
  const b = t[index * 3 + 1] ?? 0;
  const c = t[index * 3 + 2] ?? 0;
  const ax = p[a * 2] ?? 0;
  const ay = p[a * 2 + 1] ?? 0;
  return (
    Math.abs(
      ((p[b * 2] ?? 0) - ax) * ((p[c * 2 + 1] ?? 0) - ay) -
        ((p[c * 2] ?? 0) - ax) * ((p[b * 2 + 1] ?? 0) - ay),
    ) / 2
  );
}

/** Highest nodal voltage — the reference the drop field is measured from. */
export function referenceVoltage(fields: ResultLayerFields): number {
  let max = Number.NEGATIVE_INFINITY;
  for (const value of fields.voltage_v) {
    if (Number.isFinite(value) && value > max) {
      max = value;
    }
  }
  return Number.isFinite(max) ? max : 0;
}

/** Auto value range with optional area-weighted percentile clipping. */
export function autoRange(
  fields: ResultLayerFields,
  scalars: Float64Array,
  kind: ResultFieldKind,
  clip: boolean,
): { min: number; max: number } {
  if (scalars.length === 0) {
    return { min: 0, max: 1 };
  }
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const value of scalars) {
    if (value < min) min = value;
    if (value > max) max = value;
  }
  const wantsClip = clip && (kind === "j" || kind === "power");
  if (wantsClip) {
    const order = Array.from(scalars.keys()).sort((a, b) => (scalars[a] ?? 0) - (scalars[b] ?? 0));
    let total = 0;
    const areas = order.map((index) => {
      const area = triangleArea(fields, index);
      total += area;
      return area;
    });
    let cumulative = 0;
    for (let i = 0; i < order.length; i += 1) {
      cumulative += areas[i] ?? 0;
      if (cumulative >= CLIP_PERCENTILE * total) {
        max = scalars[order[i] ?? 0] ?? max;
        break;
      }
    }
  }
  if (!(max > min)) {
    max = min + 1e-30;
  }
  return { min, max };
}

export interface OverlayBatches {
  paths: Path2D[];
  colors: string[];
  min: number;
  max: number;
}

/** Build colour-bin Path2D batches in world (board-metre) coordinates. */
export function buildOverlayBatches(
  fields: ResultLayerFields,
  scalars: Float64Array,
  min: number,
  max: number,
): OverlayBatches {
  const paths: Path2D[] = [];
  const colors: string[] = [];
  for (let bin = 0; bin < COLOR_BINS; bin += 1) {
    paths.push(new Path2D());
    colors.push(colorFor((bin + 0.5) / COLOR_BINS));
  }
  const span = max - min || 1e-30;
  const tris = fields.triangles;
  const p = fields.points;
  const triCount = tris.length / 3;
  for (let i = 0; i < triCount; i += 1) {
    const t = ((scalars[i] ?? 0) - min) / span;
    const bin = Math.min(COLOR_BINS - 1, Math.max(0, Math.floor(t * COLOR_BINS)));
    const path = paths[bin];
    if (!path) continue;
    const a = tris[i * 3] ?? 0;
    const b = tris[i * 3 + 1] ?? 0;
    const c = tris[i * 3 + 2] ?? 0;
    path.moveTo(p[a * 2] ?? 0, p[a * 2 + 1] ?? 0);
    path.lineTo(p[b * 2] ?? 0, p[b * 2 + 1] ?? 0);
    path.lineTo(p[c * 2] ?? 0, p[c * 2 + 1] ?? 0);
    path.closePath();
  }
  return { paths, colors, min, max };
}

export interface ProbeSample {
  voltage_v: number;
  j_a_per_m2: number;
  power_w_per_m2: number;
}

/** Interpolate the fields at a board position, or null outside the mesh.
 *
 * Linear scan with a cheap bounding test first: ~100k triangles probe in
 * about a millisecond, well under a pointer-move budget.
 */
export function probeFields(fields: ResultLayerFields, x: number, y: number): ProbeSample | null {
  const tris = fields.triangles;
  const p = fields.points;
  const triCount = tris.length / 3;
  for (let i = 0; i < triCount; i += 1) {
    const a = tris[i * 3] ?? 0;
    const b = tris[i * 3 + 1] ?? 0;
    const c = tris[i * 3 + 2] ?? 0;
    const ax = p[a * 2] ?? 0;
    const ay = p[a * 2 + 1] ?? 0;
    const bx = p[b * 2] ?? 0;
    const by = p[b * 2 + 1] ?? 0;
    const cx = p[c * 2] ?? 0;
    const cy = p[c * 2 + 1] ?? 0;
    // Bounding box reject before the barycentric test.
    if (
      x < Math.min(ax, bx, cx) ||
      x > Math.max(ax, bx, cx) ||
      y < Math.min(ay, by, cy) ||
      y > Math.max(ay, by, cy)
    ) {
      continue;
    }
    const d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
    if (d === 0) continue;
    const l1 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / d;
    const l2 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / d;
    const l3 = 1 - l1 - l2;
    if (l1 < -1e-9 || l2 < -1e-9 || l3 < -1e-9) {
      continue;
    }
    const voltage =
      l1 * (fields.voltage_v[a] ?? 0) +
      l2 * (fields.voltage_v[b] ?? 0) +
      l3 * (fields.voltage_v[c] ?? 0);
    const area = triangleArea(fields, i);
    return {
      voltage_v: voltage,
      j_a_per_m2: fields.j_a_per_m2[i] ?? 0,
      power_w_per_m2: area > 0 ? (fields.power_w[i] ?? 0) / area : 0,
    };
  }
  return null;
}

/** Display formatting: value in the field's natural engineering unit. */
export function formatFieldValue(kind: ResultFieldKind, value: number): string {
  switch (kind) {
    case "voltage":
      return `${value.toFixed(5)} V`;
    case "voltage_drop":
      return `${(value * 1e3).toFixed(3)} mV`;
    case "j":
      // A/m² -> A/mm²
      return `${(value / 1e6).toFixed(2)} A/mm²`;
    case "power":
      // W/m² -> mW/mm²
      return `${(value / 1e3).toFixed(3)} mW/mm²`;
  }
}

export const FIELD_LABELS: Record<ResultFieldKind, string> = {
  voltage: "Voltage",
  voltage_drop: "Voltage drop",
  j: "Current density |J|",
  power: "Power density",
};
