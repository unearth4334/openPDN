/**
 * SI-to-display conversion at the component boundary.
 *
 * The wire speaks metres and square metres; the engineer reads mm, um and
 * mm^2. Nothing here rounds a value that gets computed with later -- this is
 * formatting only.
 */

import { formatEngineering } from "../components/QuantityValue";

/** Metres to millimetres. */
export function mToMm(value_m: number): number {
  return value_m * 1e3;
}

/** Metres to micrometres. */
export function mToUm(value_m: number): number {
  return value_m * 1e6;
}

/** Square metres to square millimetres. */
export function m2ToMm2(value_m2: number): number {
  return value_m2 * 1e6;
}

/** `12.345 mm` with millimetre precision suited to board coordinates. */
export function formatMm(value_m: number, precision = 5): string {
  return `${formatEngineering(mToMm(value_m), precision)} mm`;
}

/** `35 µm` for thicknesses and platings. */
export function formatUm(value_m: number, precision = 4): string {
  return `${formatEngineering(mToUm(value_m), precision)} µm`;
}

/** `96.2 mm²` for copper areas. */
export function formatMm2(value_m2: number, precision = 4): string {
  return `${formatEngineering(m2ToMm2(value_m2), precision)} mm²`;
}

/** Bytes rendered at a human scale. */
export function formatBytes(bytes: number): string {
  if (bytes >= 1 << 20) {
    return `${(bytes / (1 << 20)).toFixed(1)} MiB`;
  }
  if (bytes >= 1 << 10) {
    return `${(bytes / (1 << 10)).toFixed(1)} KiB`;
  }
  return `${bytes} B`;
}

/** Seconds as milliseconds for pipeline timings. */
export function formatMs(seconds: number): string {
  return `${(seconds * 1e3).toFixed(1)} ms`;
}
