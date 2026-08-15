/**
 * Stackup review: a proportional cross-section next to the layer table.
 *
 * Unknown thicknesses render as "Unknown" with their consequences visible
 * (no Z below the gap), never as a plausible-looking default. Slice heights
 * are proportional to physical thickness with a minimum so thin copper stays
 * visible next to a thick stiffener.
 */

import type { LayerResponse, QuantityResponse } from "../../api/types";
import { formatMm, formatUm } from "../../lib/units";
import { useBoardState } from "../../state/boardState";
import { ProvenanceBadge } from "../ProvenanceBadge";

const LAYER_COLOR_SLOTS = 6;
const SECTION_HEIGHT_PX = 200;
const MIN_SLICE_PX = 3;
/** Below this slice height the name is unreadable; the tooltip carries it. */
const LABEL_MIN_PX = 12;

export function StackupView() {
  const { state, dispatch } = useBoardState();
  const review = state.review;
  if (review === null) {
    return null;
  }
  const layers = review.layers;
  const conductiveIndex = new Map(
    layers.filter((layer) => layer.is_conductive).map((layer, index) => [layer.id, index]),
  );
  const known = layers.map((layer) => layer.thickness?.value ?? 0);
  const total = known.reduce((sum, value) => sum + value, 0) || 1;
  const selectedLayer = state.selection?.kind === "layer" ? state.selection.layerId : null;

  const sliceStyle = (layer: LayerResponse, index: number) => {
    const thickness = layer.thickness?.value ?? 0;
    const height = Math.max(MIN_SLICE_PX, (thickness / total) * SECTION_HEIGHT_PX);
    if (layer.is_conductive) {
      const slot = (conductiveIndex.get(layer.id) ?? index) % LAYER_COLOR_SLOTS;
      return { height, background: `var(--layer-color-${slot})` };
    }
    return { height };
  };

  return (
    <div className="stackup">
      <div>
        <div className="empty-state">TOP</div>
        <ul className="stackup__section net-list" aria-label="Stackup cross-section">
          {layers.map((layer, index) => (
            <li key={layer.id}>
              <button
                type="button"
                className={[
                  "stackup__slice",
                  layer.is_conductive ? "stackup__slice--conductive" : "stackup__slice--dielectric",
                  selectedLayer === layer.id ? "stackup__slice--selected" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                style={{ ...sliceStyle(layer, index), border: "none", width: "100%" }}
                title={`${layer.name} — ${layer.thickness ? formatUm(layer.thickness.value) : "unknown thickness"}`}
                onClick={() =>
                  dispatch({ type: "selected", selection: { kind: "layer", layerId: layer.id } })
                }
              >
                {Math.max(
                  MIN_SLICE_PX,
                  ((layer.thickness?.value ?? 0) / total) * SECTION_HEIGHT_PX,
                ) >= LABEL_MIN_PX
                  ? layer.name
                  : ""}
              </button>
            </li>
          ))}
        </ul>
        <div className="empty-state">BOTTOM</div>
      </div>
      <table className="property-table">
        <thead>
          <tr>
            <th>Layer</th>
            <th>Function</th>
            <th>Material</th>
            <th>Thickness</th>
            <th>Z top</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {layers.map((layer) => (
            <tr
              key={layer.id}
              aria-selected={selectedLayer === layer.id}
              className="row-button"
              onClick={() =>
                dispatch({ type: "selected", selection: { kind: "layer", layerId: layer.id } })
              }
            >
              <td>{layer.name}</td>
              <td>{layer.function.replace(/_/g, " ")}</td>
              <td>{layer.material_name ?? "—"}</td>
              <td className="value numeric">
                {layer.thickness ? formatUm(layer.thickness.value) : "Unknown"}
              </td>
              <td className="value numeric">{layer.z_top ? formatMm(layer.z_top.value) : "—"}</td>
              <td>
                <SourceBadge thickness={layer.thickness} z={layer.z_top} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SourceBadge({
  thickness,
  z,
}: {
  thickness: QuantityResponse | null;
  z: QuantityResponse | null;
}) {
  if (thickness) {
    return <ProvenanceBadge provenance={thickness.provenance} note={thickness.note} />;
  }
  if (z) {
    return <ProvenanceBadge provenance={z.provenance} note={z.note} />;
  }
  return <span title="Not present in the imported source">Unknown</span>;
}
