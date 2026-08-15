import type { Provenance } from "../api/types";
import { ProvenanceBadge } from "./ProvenanceBadge";

export interface QuantityValueProps {
  value: number;
  /** Display unit, e.g. `mV`, `mΩ`, `A/mm²`. Never omit it. */
  unit: string;
  provenance?: Provenance;
  note?: string | null;
  /** Significant digits shown. Engineering work rarely wants more than four. */
  precision?: number;
}

/**
 * Renders one physical value with its unit and provenance.
 *
 * Conversion from SI happens at this boundary: the backend speaks metres,
 * amperes and ohms, the engineer reads mm, A and mΩ.
 */
export function QuantityValue({
  value,
  unit,
  provenance,
  note,
  precision = 4,
}: QuantityValueProps) {
  return (
    <span>
      <span className="numeric">{formatEngineering(value, precision)}</span>
      <span className="unit">{unit}</span>
      {provenance ? (
        <>
          {" "}
          <ProvenanceBadge provenance={provenance} note={note ?? null} />
        </>
      ) : null}
    </span>
  );
}

/**
 * Formats a number for engineering reading: fixed significant digits, no
 * thousands separators (they break copy-paste into spreadsheets), and
 * exponential notation only when the magnitude demands it.
 */
export function formatEngineering(value: number, precision = 4): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  if (value === 0) {
    return "0";
  }
  const magnitude = Math.abs(value);
  if (magnitude >= 1e6 || magnitude < 1e-4) {
    return value.toExponential(precision - 1);
  }
  return Number(value.toPrecision(precision)).toString();
}
