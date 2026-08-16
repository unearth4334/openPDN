/**
 * Numerical metrics of the active result: headline quantities, the quality
 * section (mesh, residual, conservation, convergence), terminal table, and
 * assumption diagnostics.
 *
 * The quality section exists so a result explains why it should be trusted —
 * a residual is linear-algebra health, the convergence comparison is
 * discretisation evidence, and the two are never conflated (fem-solver skill).
 */

import type { ResultMetrics } from "../api/types";
import { useBoardState } from "../state/boardState";

export function ResultMetricsPanel({ metrics }: { metrics: ResultMetrics }) {
  const { dispatch } = useBoardState();
  const probe = metrics.probes[0] ?? null;
  const conservation = metrics.conservation;
  const convergence = metrics.convergence;
  const warnings = metrics.diagnostics.filter((d) => d.severity !== "info");

  return (
    <div className="result-metrics">
      <div className="panel__header">Result</div>

      {probe ? (
        <div className="result-headline">
          <span className="result-headline__label">Effective resistance</span>
          <span className="result-headline__value numeric">
            {formatResistance(probe.resistance_ohm)}
          </span>
        </div>
      ) : null}
      {metrics.kind === "ir_drop" && metrics.engineering_quantities.worst_drop_v !== undefined ? (
        <div className="result-headline">
          <span className="result-headline__label">Worst terminal drop</span>
          <span className="result-headline__value numeric">
            {(metrics.engineering_quantities.worst_drop_v * 1e3).toFixed(3)} mV
          </span>
        </div>
      ) : null}

      <table className="property-table" aria-label="Result quality">
        <tbody>
          <tr>
            <th>Accuracy profile</th>
            <td>{metrics.quality.accuracy}</td>
          </tr>
          <tr>
            <th>Mesh elements</th>
            <td className="numeric">{metrics.quality.mesh_elements?.toLocaleString() ?? "—"}</td>
          </tr>
          <tr>
            <th>Matrix non-zeros</th>
            <td className="numeric">{metrics.quality.matrix_nonzeros.toLocaleString()}</td>
          </tr>
          <tr>
            <th>Linear residual</th>
            <td className="numeric">{formatExp(conservation.residual)}</td>
          </tr>
          <tr>
            <th>Current imbalance</th>
            <td className="numeric">{formatExp(conservation.current_imbalance_fraction)}</td>
          </tr>
          <tr>
            <th>Power balance error</th>
            <td className="numeric">{formatExp(conservation.power_mismatch_fraction)}</td>
          </tr>
          <tr>
            <th>Total dissipation</th>
            <td className="numeric">{formatPower(conservation.dissipated_power_w)}</td>
          </tr>
        </tbody>
      </table>

      {convergence ? (
        <>
          <div className="panel__header">Convergence</div>
          <table className="property-table" aria-label="Mesh convergence">
            <tbody>
              <tr>
                <th>Meshes</th>
                <td className="numeric">
                  {convergence.coarse_elements.toLocaleString()} →{" "}
                  {convergence.fine_elements.toLocaleString()}
                </td>
              </tr>
              {Object.entries(convergence.quantities).map(([name, entry]) => (
                <tr key={name}>
                  <th>{quantityLabel(name)}</th>
                  <td className="numeric">{(entry.relative_change * 100).toFixed(3)} %</td>
                </tr>
              ))}
              <tr>
                <th>Status</th>
                <td>
                  {convergence.converged
                    ? `Converged within ${(convergence.target_fraction * 100).toFixed(1)} % target`
                    : "NOT converged to target"}
                </td>
              </tr>
            </tbody>
          </table>
        </>
      ) : metrics.quality.accuracy === "preview" ? (
        <p className="sim-note">Preview — not convergence verified.</p>
      ) : null}

      {metrics.terminals.length > 0 ? (
        <>
          <div className="panel__header">Terminals</div>
          <table className="data-table" aria-label="Terminal results">
            <thead>
              <tr>
                <th>Terminal</th>
                <th>Current</th>
                <th>Voltage</th>
              </tr>
            </thead>
            <tbody>
              {metrics.terminals.map((terminal) => {
                const memberTerminalIds = terminal.member_terminal_ids?.length
                  ? terminal.member_terminal_ids
                  : [terminal.terminal_id];
                const memberViaIds = terminal.member_via_ids ?? [];
                const memberCount = memberTerminalIds.length + memberViaIds.length;
                return (
                  <tr key={terminal.terminal_id}>
                    <td>
                      <button
                        type="button"
                        className="link-button"
                        onClick={() => {
                          dispatch({
                            type: "terminals-highlighted",
                            terminalIds: memberTerminalIds,
                          });
                          dispatch({ type: "via-group-highlighted", viaIds: memberViaIds });
                        }}
                      >
                        {terminal.terminal_id}
                      </button>
                      {memberCount > 1 ? (
                        <span className="sim-note"> (+{memberCount - 1} more)</span>
                      ) : null}
                    </td>
                    <td className="numeric">
                      {terminal.is_source ? "source" : `${terminal.current_a.toFixed(3)} A`}
                    </td>
                    <td className="numeric">{terminal.voltage_v.toFixed(5)} V</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      ) : null}

      {warnings.length > 0 ? (
        <>
          <div className="panel__header">Assumptions & warnings</div>
          <ul className="result-warnings">
            {warnings.map((diagnostic) => (
              <li key={`${diagnostic.code}-${diagnostic.message.slice(0, 24)}`}>
                <span className={`diag diag--${diagnostic.severity}`}>{diagnostic.severity}</span>{" "}
                {diagnostic.message}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}

function formatResistance(ohm: number): string {
  if (Math.abs(ohm) >= 1) {
    return `${ohm.toFixed(5)} Ω`;
  }
  if (Math.abs(ohm) >= 1e-3) {
    return `${(ohm * 1e3).toFixed(4)} mΩ`;
  }
  return `${(ohm * 1e6).toFixed(2)} µΩ`;
}

function formatPower(watts: number): string {
  if (Math.abs(watts) >= 1) {
    return `${watts.toFixed(4)} W`;
  }
  return `${(watts * 1e3).toFixed(4)} mW`;
}

function formatExp(value: number): string {
  return value === 0 ? "0" : value.toExponential(2);
}

function quantityLabel(name: string): string {
  const labels: Record<string, string> = {
    resistance_ohm: "R change",
    total_loss_w: "Loss change",
    worst_drop_v: "Worst drop change",
    j99_a_per_m2: "J99 change",
  };
  return labels[name] ?? name;
}
