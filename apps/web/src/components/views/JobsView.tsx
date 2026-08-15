/**
 * Simulation queue and results: recent jobs, stage progress, cancellation,
 * and opening completed results into the viewport overlay.
 *
 * Polls `/api/jobs` at a slow steady interval while mounted; polling is
 * architecturally simpler than a push channel and comfortably responsive for
 * jobs measured in seconds to minutes (ADR-0011).
 */

import { useCallback, useEffect } from "react";
import { cancelJob, fetchJobs, fetchResultMetrics } from "../../api/client";
import type { JobResponse } from "../../api/types";
import { useBoardState } from "../../state/boardState";

const POLL_INTERVAL_MS = 2000;

const STATE_GLYPHS: Record<string, string> = {
  queued: "○",
  claimed: "◐",
  running: "●",
  completed: "✓",
  completed_with_warnings: "✓",
  failed: "!",
  cancelling: "◌",
  cancelled: "◌",
};

export function JobsView() {
  const { state, dispatch } = useBoardState();

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const jobs = await fetchJobs();
        if (!cancelled) {
          dispatch({ type: "jobs-updated", jobs });
        }
      } catch {
        // The next tick retries; a dead backend shows stale rows, not a crash.
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [dispatch]);

  const openResult = useCallback(
    async (job: JobResponse) => {
      const metrics = await fetchResultMetrics(job.job_id);
      dispatch({ type: "result-opened", jobId: job.job_id, metrics });
    },
    [dispatch],
  );

  if (state.jobs.length === 0) {
    return <p className="empty-state">No simulations yet. Use “New simulation” to queue one.</p>;
  }

  return (
    <table className="data-table jobs-table" aria-label="Simulation jobs">
      <thead>
        <tr>
          <th aria-label="State" />
          <th>Simulation</th>
          <th>Accuracy</th>
          <th>Status</th>
          <th>Result</th>
          <th aria-label="Actions" />
        </tr>
      </thead>
      <tbody>
        {state.jobs.map((job) => (
          <tr
            key={job.job_id}
            className={
              state.activeResult?.jobId === job.job_id ? "jobs-table__row--active" : undefined
            }
          >
            <td className={`jobs-table__state jobs-table__state--${job.state}`}>
              {STATE_GLYPHS[job.state] ?? "?"}
            </td>
            <td>{job.name}</td>
            <td>{job.accuracy}</td>
            <td>
              {job.state === "running" && job.stage
                ? stageLabel(job.stage)
                : job.state.replace(/_/g, " ")}
              {job.state === "failed" && job.message ? (
                <span className="jobs-table__message" title={job.message}>
                  {" "}
                  — {truncate(job.message, 80)}
                </span>
              ) : null}
            </td>
            <td className="numeric">{summaryLabel(job)}</td>
            <td>
              {isOpenable(job) ? (
                <button
                  type="button"
                  className="button button--ghost"
                  onClick={() => openResult(job)}
                >
                  {state.activeResult?.jobId === job.job_id ? "Shown" : "Open"}
                </button>
              ) : null}
              {isCancellable(job) ? (
                <button
                  type="button"
                  className="button button--ghost"
                  onClick={() => void cancelJob(job.job_id)}
                >
                  Cancel
                </button>
              ) : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function isOpenable(job: JobResponse): boolean {
  return job.state === "completed" || job.state === "completed_with_warnings";
}

function isCancellable(job: JobResponse): boolean {
  return job.state === "queued" || job.state === "claimed" || job.state === "running";
}

function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    validating: "Validating study",
    loading_board: "Loading board",
    meshing: "Generating mesh",
    assembling: "Assembling system",
    solving: "Solving sparse system",
    postprocessing: "Calculating fields",
    verifying_convergence: "Verifying convergence",
    serializing: "Writing results",
  };
  return labels[stage] ?? stage;
}

function summaryLabel(job: JobResponse): string {
  const summary = job.result_summary;
  if (!summary) {
    return "";
  }
  const resistance = summary.resistance_ohm;
  if (typeof resistance === "number") {
    return formatResistance(resistance);
  }
  const drop = summary.worst_drop_v;
  if (typeof drop === "number") {
    return `${(drop * 1e3).toFixed(2)} mV worst drop`;
  }
  return "";
}

function formatResistance(ohm: number): string {
  if (Math.abs(ohm) >= 1) {
    return `${ohm.toFixed(4)} Ω`;
  }
  if (Math.abs(ohm) >= 1e-3) {
    return `${(ohm * 1e3).toFixed(3)} mΩ`;
  }
  return `${(ohm * 1e6).toFixed(1)} µΩ`;
}

function truncate(text: string, length: number): string {
  return text.length > length ? `${text.slice(0, length)}…` : text;
}
