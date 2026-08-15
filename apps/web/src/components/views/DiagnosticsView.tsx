/**
 * Import diagnostics and simulation readiness.
 *
 * Diagnostics are structured data (code, severity, context), not log strings;
 * the capability checklist is the importer's own account of what it obtained,
 * so a board that looks fine but cannot be solved says so here.
 */

import { useBoardState } from "../../state/boardState";

const STATUS_SYMBOL: Record<string, string> = {
  present: "✓",
  partial: "◐",
  absent: "!",
  unknown: "?",
};

export function DiagnosticsView() {
  const { state } = useBoardState();
  const review = state.review;
  if (review === null) {
    return null;
  }

  return (
    <div className="tab-panel--split">
      <div>
        <div className="panel__header">Simulation readiness</div>
        <div className="capability-grid">
          {review.capability_items.map((item) => (
            <div key={item.name} style={{ display: "contents" }}>
              <span
                className={
                  item.status === "present" ? "diag-severity--info" : "diag-severity--warning"
                }
                title={item.status}
              >
                {STATUS_SYMBOL[item.status] ?? "?"}
              </span>
              <span title={item.note ?? undefined}>
                {item.name}
                {item.note ? ` — ${item.note}` : ""}
              </span>
            </div>
          ))}
        </div>
        <p>
          <strong>{review.readiness.replace(/_/g, " ")}</strong>
        </p>
      </div>

      <table className="property-table" aria-label="Diagnostics">
        <thead>
          <tr>
            <th>Severity</th>
            <th>Code</th>
            <th>Message</th>
            <th>Context</th>
          </tr>
        </thead>
        <tbody>
          {review.diagnostics.map((diagnostic) => (
            <tr key={`${diagnostic.code}:${Object.values(diagnostic.context).join(",")}`}>
              <td className={`diag-severity--${diagnostic.severity}`}>
                {diagnostic.severity.toUpperCase()}
              </td>
              <td className="numeric">{diagnostic.code}</td>
              <td style={{ whiteSpace: "normal" }}>{diagnostic.message}</td>
              <td className="numeric">
                {Object.entries(diagnostic.context)
                  .map(([key, value]) => `${key}=${value}`)
                  .join(" ")}
              </td>
            </tr>
          ))}
          {review.diagnostics.length === 0 ? (
            <tr>
              <td colSpan={4} className="empty-state">
                No diagnostics: the import was clean.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}
