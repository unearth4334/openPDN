/**
 * Header: board identity, readiness, view selection, open/re-import and
 * backend status.
 *
 * The readiness chip is a button: clicking it opens the diagnostics tab,
 * because a warning a user cannot chase down is decoration. Solve controls
 * are still absent on purpose -- there is no solver to run yet.
 */

import { useRef } from "react";
import type { GeometryViewName } from "../api/types";
import { useBoardActions } from "../hooks/useBoardActions";
import type { DeploymentState } from "../hooks/useDeploymentInfo";
import { useBoardState } from "../state/boardState";

export interface ToolbarProps {
  deployment: DeploymentState;
}

const READINESS_LABEL: Record<string, string> = {
  ready: "Ready",
  ready_with_assumptions: "Ready with assumptions",
  not_ready: "Not ready",
};

export function Toolbar({ deployment }: ToolbarProps) {
  const { state, dispatch } = useBoardState();
  const { importFile } = useBoardActions();
  const fileInput = useRef<HTMLInputElement | null>(null);
  const review = state.review;

  return (
    <header className="toolbar">
      <span className="toolbar__title">openPDN</span>
      {review ? (
        <span className="toolbar__board">
          <span>{review.name}</span>
          <span className="toolbar__source numeric">{review.source_name}</span>
          <button
            type="button"
            className={`readiness readiness--${review.readiness}`}
            title="Open import diagnostics"
            onClick={() => dispatch({ type: "bottom-tab-changed", tab: "diagnostics" })}
          >
            <span className="readiness__dot" aria-hidden="true" />
            {READINESS_LABEL[review.readiness] ?? review.readiness}
          </button>
        </span>
      ) : null}
      <span className="toolbar__spacer" />
      {review ? (
        <label className="status">
          Geometry{" "}
          <select
            className="select"
            aria-label="Geometry view"
            value={state.view}
            onChange={(event) =>
              dispatch({ type: "view-changed", view: event.target.value as GeometryViewName })
            }
          >
            <option value="normalized">Normalized</option>
            <option value="imported">Imported features</option>
          </select>
        </label>
      ) : null}
      {review ? (
        <button
          type="button"
          className="button"
          aria-pressed={state.simulationOpen}
          onClick={() => dispatch({ type: "simulation-panel-toggled" })}
        >
          New simulation
        </button>
      ) : null}
      {review ? (
        <button
          type="button"
          className="button button--ghost"
          onClick={() => dispatch({ type: "bottom-tab-changed", tab: "jobs" })}
        >
          Queue
        </button>
      ) : null}
      <button type="button" className="button" onClick={() => fileInput.current?.click()}>
        Open board…
      </button>
      <input
        ref={fileInput}
        type="file"
        accept=".xml,.cvg"
        hidden
        aria-label="PCB source file"
        onChange={(event) => {
          const file = event.target.files?.item(0);
          if (file) {
            void importFile(file);
          }
          event.target.value = "";
        }}
      />
      <BackendStatus deployment={deployment} />
    </header>
  );
}

function BackendStatus({ deployment }: { deployment: DeploymentState }) {
  if (deployment.status === "loading") {
    return <span className="status">connecting…</span>;
  }
  if (deployment.status === "error") {
    return (
      <span className="status status--error" role="status">
        <span className="status__dot" />
        backend unavailable
      </span>
    );
  }
  const { info } = deployment;
  return (
    <span className="status status--ok" role="status">
      <span className="status__dot" />
      <span className="numeric">
        {info.name} {info.version}
      </span>
      <span>· API {info.api_version}</span>
      <span>· {info.environment}</span>
    </span>
  );
}
