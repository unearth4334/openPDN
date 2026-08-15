/**
 * Via review: summary counts, span/drill groups, and individual inspection.
 *
 * Clicking a group highlights its vias in the viewport; clicking a via selects
 * and centres it. This table is where via current will land once the solver
 * exists, so the ids shown here are the stable ones results will refer to.
 */

import { useState } from "react";
import type { ViaResponse } from "../../api/types";
import { formatMm } from "../../lib/units";
import { useBoardState } from "../../state/boardState";

export function ViasView() {
  const { state, dispatch } = useBoardState();
  const [groupIndex, setGroupIndex] = useState<number | null>(null);
  const review = state.review;
  if (review === null) {
    return null;
  }
  const layerName = (id: string) => review.layers.find((layer) => layer.id === id)?.name ?? id;
  const counts = {
    through: review.vias.filter((via) => via.span_kind === "through").length,
    blind: review.vias.filter((via) => via.span_kind === "blind").length,
    buried: review.vias.filter((via) => via.span_kind === "buried").length,
    unknown: review.vias.filter((via) => via.span_kind === "unknown").length,
  };

  const selectVia = (via: ViaResponse) => {
    dispatch({ type: "selected", selection: { kind: "via", viaId: via.id } });
    dispatch({
      type: "focus-requested",
      x_m: via.x_m,
      y_m: via.y_m,
      radius_m: Math.max((via.drill_diameter?.value ?? 0.3e-3) * 12, 1.5e-3),
    });
  };

  const toggleGroup = (index: number, viaIds: string[]) => {
    const active = groupIndex === index;
    setGroupIndex(active ? null : index);
    dispatch({ type: "via-group-highlighted", viaIds: active ? [] : viaIds });
  };

  const selectedViaId = state.selection?.kind === "via" ? state.selection.viaId : null;

  return (
    <div className="tab-panel--split">
      <div>
        <table className="property-table" aria-label="Via summary">
          <tbody>
            <tr>
              <th scope="row">Total vias</th>
              <td className="value numeric">{review.vias.length}</td>
            </tr>
            <tr>
              <th scope="row">Through</th>
              <td className="value numeric">{counts.through}</td>
            </tr>
            <tr>
              <th scope="row">Blind</th>
              <td className="value numeric">{counts.blind}</td>
            </tr>
            <tr>
              <th scope="row">Buried</th>
              <td className="value numeric">{counts.buried}</td>
            </tr>
            <tr>
              <th scope="row">Unknown span</th>
              <td className="value numeric">{counts.unknown}</td>
            </tr>
          </tbody>
        </table>

        <table className="property-table" aria-label="Via groups">
          <thead>
            <tr>
              <th>Count</th>
              <th>Span</th>
              <th>Drill</th>
              <th>Padstack</th>
            </tr>
          </thead>
          <tbody>
            {review.via_groups.map((group, index) => (
              <tr
                key={`${group.from_layer_id}-${group.to_layer_id}-${group.drill_diameter_m ?? "x"}`}
                className="row-button"
                aria-selected={groupIndex === index}
                title="Highlight these vias in the viewport"
                onClick={() => toggleGroup(index, group.via_ids)}
              >
                <td className="value numeric">{group.count}</td>
                <td>
                  {layerName(group.from_layer_id)} → {layerName(group.to_layer_id)} (
                  {group.span_kind})
                </td>
                <td className="value numeric">
                  {group.drill_diameter_m !== null ? formatMm(group.drill_diameter_m, 3) : "—"}
                </td>
                <td>{group.padstack_name ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <table className="property-table" aria-label="Vias">
        <thead>
          <tr>
            <th>ID</th>
            <th>Net</th>
            <th>X</th>
            <th>Y</th>
            <th>Span</th>
            <th>Drill</th>
            <th>Plating</th>
          </tr>
        </thead>
        <tbody>
          {review.vias.map((via) => (
            <tr
              key={via.id}
              className="row-button"
              aria-selected={selectedViaId === via.id}
              title="Select and centre this via"
              onClick={() => selectVia(via)}
            >
              <td className="numeric">{via.id}</td>
              <td>
                {via.net_id
                  ? (review.nets.find((net) => net.id === via.net_id)?.name ?? via.net_id)
                  : "(unassigned)"}
              </td>
              <td className="value numeric">{formatMm(via.x_m)}</td>
              <td className="value numeric">{formatMm(via.y_m)}</td>
              <td>{via.span_kind}</td>
              <td className="value numeric">
                {via.drill_diameter ? formatMm(via.drill_diameter.value, 3) : "—"}
              </td>
              <td>
                {via.plating_thickness ? formatMm(via.plating_thickness.value, 3) : "Unknown"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
