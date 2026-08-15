import type { DeploymentState } from "../hooks/useDeploymentInfo";

export interface ConsolePanelProps {
  deployment: DeploymentState;
}

interface ConsoleLine {
  severity: "info" | "warning" | "error";
  code: string;
  message: string;
}

/**
 * Results, warnings and solver output.
 *
 * Diagnostics are shown with their stable code alongside the message, so a
 * warning can be looked up, filtered and referenced in a bug report.
 */
export function ConsolePanel({ deployment }: ConsolePanelProps) {
  const lines = buildLines(deployment);
  return (
    <section className="console" aria-label="Results and warnings">
      <div className="panel__header">Results / Warnings</div>
      {lines.map((line) => (
        <div className="console__line" key={line.code}>
          <span className={`console__severity--${line.severity}`}>[{line.severity}]</span>
          <span className="console__severity--info">{line.code}</span>
          <span>{line.message}</span>
        </div>
      ))}
    </section>
  );
}

function buildLines(deployment: DeploymentState): ConsoleLine[] {
  if (deployment.status === "loading") {
    return [{ severity: "info", code: "ui.connecting", message: "Contacting the openPDN API…" }];
  }
  if (deployment.status === "error") {
    return [{ severity: "error", code: "ui.api_unreachable", message: deployment.message }];
  }

  const lines: ConsoleLine[] = [
    {
      severity: "info",
      code: "ui.connected",
      message: `Connected to ${deployment.info.name} ${deployment.info.version}.`,
    },
  ];

  // A mock-only deployment must say so unprompted: nobody should have to
  // discover from a result that no physics was applied.
  const physical = deployment.info.solvers.filter((solver) => solver.fidelity !== "mock");
  if (physical.length === 0) {
    lines.push({
      severity: "warning",
      code: "ui.no_physical_solver",
      message:
        "No physical solver is installed. Only the mock backend is available; it solves nothing.",
    });
  }
  return lines;
}
