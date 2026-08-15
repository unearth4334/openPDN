import type { Provenance } from "../api/types";

const LABELS: Record<Provenance, string> = {
  imported: "imported",
  configured: "configured",
  assumed: "assumed",
  derived: "derived",
};

const TITLES: Record<Provenance, string> = {
  imported: "Read from the fabrication data",
  configured: "Entered for this study",
  assumed: "Default standing in for an unknown value",
  derived: "Computed from other quantities",
};

export interface ProvenanceBadgeProps {
  provenance: Provenance;
  /** Why an assumption was made. Shown on hover; required reading for `assumed`. */
  note?: string | null;
}

/**
 * Marks where a displayed value came from.
 *
 * Every physical quantity in the UI carries one of these. An IR-drop figure
 * computed from an assumed copper thickness has to *look* different from one
 * computed from imported data -- that visual difference is the whole point.
 */
export function ProvenanceBadge({ provenance, note }: ProvenanceBadgeProps) {
  const title = note ? `${TITLES[provenance]}: ${note}` : TITLES[provenance];
  return (
    <span className={`badge badge--${provenance}`} title={title} data-testid="provenance-badge">
      {LABELS[provenance]}
    </span>
  );
}
