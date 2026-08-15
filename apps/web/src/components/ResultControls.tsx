/**
 * Result overlay controls and legend, floated over the viewport while a
 * completed simulation is open.
 *
 * The legend always states quantity, units, minimum and maximum; changing the
 * display range never changes the underlying data. Auto range clips |J| and
 * power at an area percentile so an isolated singular peak cannot flatten the
 * whole map (fem-solver skill).
 */

import { useMemo } from "react";
import { type ResultFieldKind, useBoardState } from "../state/boardState";
import {
  autoRange,
  colorFor,
  FIELD_LABELS,
  formatFieldValue,
  referenceVoltage,
  triangleScalars,
} from "../viewer/resultOverlay";

const FIELDS: ResultFieldKind[] = ["voltage", "voltage_drop", "j", "power"];

export function ResultControls() {
  const { state, dispatch } = useBoardState();
  const active = state.activeResult;
  const fields = state.resultFields[state.resultLayerIndex];

  const range = useMemo(() => {
    if (!fields) {
      return null;
    }
    if (state.resultScale.mode === "manual") {
      return { min: state.resultScale.min, max: state.resultScale.max };
    }
    const scalars = triangleScalars(fields, state.resultField, referenceVoltage(fields));
    return autoRange(fields, scalars, state.resultField, state.resultScale.clipPercentile);
  }, [fields, state.resultField, state.resultScale]);

  if (!active) {
    return null;
  }
  const layers = active.metrics.layer_files;

  return (
    <section className="result-controls" aria-label="Result overlay controls">
      <div className="result-controls__row">
        <span className="result-controls__title">{jobTitle(active.metrics.board_name, state)}</span>
        <button
          type="button"
          className="button button--ghost"
          onClick={() => dispatch({ type: "result-closed" })}
        >
          Close
        </button>
      </div>
      <label className="result-controls__field">
        Field
        <select
          value={state.resultField}
          onChange={(event) =>
            dispatch({
              type: "result-field-changed",
              field: event.target.value as ResultFieldKind,
            })
          }
        >
          {FIELDS.map((field) => (
            <option key={field} value={field}>
              {FIELD_LABELS[field]}
            </option>
          ))}
        </select>
      </label>
      <label className="result-controls__field">
        Layer
        <select
          value={state.resultLayerIndex}
          onChange={(event) =>
            dispatch({ type: "result-layer-changed", layerIndex: Number(event.target.value) })
          }
        >
          {layers.map((layer, index) => (
            <option key={layer.layer_id} value={index}>
              {layerName(layer.layer_id)}
            </option>
          ))}
        </select>
      </label>
      <label className="result-controls__clip">
        <input
          type="checkbox"
          checked={state.resultScale.clipPercentile}
          onChange={(event) =>
            dispatch({
              type: "result-scale-changed",
              scale: { clipPercentile: event.target.checked },
            })
          }
        />
        Percentile clip
      </label>
      {range ? (
        <div className="result-legend" role="img" aria-label="Colour scale">
          <div
            className="result-legend__ramp"
            style={{
              background: `linear-gradient(to right, ${[0, 0.25, 0.5, 0.75, 1]
                .map((t) => colorFor(t))
                .join(", ")})`,
            }}
          />
          <div className="result-legend__labels numeric">
            <span>{formatFieldValue(state.resultField, range.min)}</span>
            <span>{formatFieldValue(state.resultField, range.max)}</span>
          </div>
        </div>
      ) : (
        <p className="sim-note">Loading fields…</p>
      )}
    </section>
  );
}

function jobTitle(boardName: string, state: { activeResult: { jobId: string } | null }): string {
  void boardName;
  const job = state.activeResult?.jobId ?? "";
  return `Result ${job.slice(0, 12)}`;
}

/** Layer ids read like "layer-mid-layer-1"; show the human part. */
function layerName(layerId: string): string {
  return layerId.replace(/^layer-/, "").replace(/-/g, " ");
}
