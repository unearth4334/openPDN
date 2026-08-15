/**
 * The contextual inspector: whatever is selected, its numbers.
 *
 * Selection comes from the shared board state, so clicking a net in the list,
 * a via in the table or copper in the viewport all land here. Every physical
 * value shows its unit and, where it matters, its provenance -- an assumed
 * plating must not read like an imported one.
 */

import type {
  BoardReviewResponse,
  LayerResponse,
  NetResponse,
  QuantityResponse,
  ViaResponse,
} from "../api/types";
import type { DeploymentState } from "../hooks/useDeploymentInfo";
import { formatMm, formatMm2, formatUm } from "../lib/units";
import { type Selection, useBoardState } from "../state/boardState";
import { ProvenanceBadge } from "./ProvenanceBadge";
import { QuantityValue } from "./QuantityValue";

export interface InspectorPanelProps {
  deployment: DeploymentState;
}

export function InspectorPanel({ deployment }: InspectorPanelProps) {
  const { state } = useBoardState();
  const review = state.review;

  return (
    <aside className="panel panel--right" aria-label="Inspector">
      <div className="panel__header">Inspector</div>
      <div className="panel__body">
        {review === null ? (
          <DeploymentSummary deployment={deployment} />
        ) : (
          <SelectionDetails review={review} selection={state.selection} />
        )}
      </div>
    </aside>
  );
}

function DeploymentSummary({ deployment }: { deployment: DeploymentState }) {
  if (deployment.status !== "ready") {
    return <p className="empty-state">Open a board to inspect its layers, nets and vias.</p>;
  }
  return (
    <>
      <p className="empty-state">
        Open a board to inspect its layers, nets and vias. Capabilities of this deployment:
      </p>
      {deployment.info.capabilities.map((capability) => (
        <div className="capability" key={capability.name} title={capability.detail ?? undefined}>
          <span>{capability.name}</span>
          <span className={`capability__status capability__status--${capability.status}`}>
            {capability.status.replace(/_/g, " ")}
          </span>
        </div>
      ))}
    </>
  );
}

function SelectionDetails({
  review,
  selection,
}: {
  review: BoardReviewResponse;
  selection: Selection | null;
}) {
  if (selection === null) {
    return <BoardDetails review={review} />;
  }
  if (selection.kind === "net") {
    const net = review.nets.find((candidate) => candidate.id === selection.netId);
    return net ? <NetDetails review={review} net={net} /> : <BoardDetails review={review} />;
  }
  if (selection.kind === "via") {
    const via = review.vias.find((candidate) => candidate.id === selection.viaId);
    return via ? <ViaDetails review={review} via={via} /> : <BoardDetails review={review} />;
  }
  if (selection.kind === "layer") {
    const layer = review.layers.find((candidate) => candidate.id === selection.layerId);
    return layer ? <LayerDetails layer={layer} /> : <BoardDetails review={review} />;
  }
  return (
    <RegionDetails review={review} regionId={selection.regionId} layerId={selection.layerId} />
  );
}

function BoardDetails({ review }: { review: BoardReviewResponse }) {
  const bounds = review.bounds;
  return (
    <>
      <div className="panel__header">Board</div>
      <table className="property-table">
        <tbody>
          <Row label="Name" value={review.name} />
          <Row label="Format" value={review.format_revision ?? review.source_format} />
          {bounds ? (
            <>
              <Row label="Width" value={formatMm(bounds.max_x_m - bounds.min_x_m)} />
              <Row label="Height" value={formatMm(bounds.max_y_m - bounds.min_y_m)} />
            </>
          ) : null}
          {review.total_thickness ? (
            <tr>
              <th scope="row">Thickness</th>
              <td className="value">
                <Thickness quantity={review.total_thickness} millimetres />
              </td>
            </tr>
          ) : null}
          <Row
            label="Layers"
            value={`${review.layers.filter((layer) => layer.is_conductive).length} copper / ${review.layers.length}`}
          />
          <Row label="Nets" value={String(review.nets.length)} />
          <Row label="Vias" value={String(review.vias.length)} />
          <Row label="Components" value={String(review.components.length)} />
          <Row label="Terminals" value={String(review.terminals.length)} />
        </tbody>
      </table>
    </>
  );
}

function NetDetails({ review, net }: { review: BoardReviewResponse; net: NetResponse }) {
  const layerNames = net.layer_ids
    .map((id) => review.layers.find((layer) => layer.id === id)?.name ?? id)
    .join(", ");
  return (
    <>
      <div className="panel__header">Net</div>
      <table className="property-table">
        <tbody>
          <Row label="Name" value={net.name} />
          <Row label="Layers" value={layerNames || "—"} />
          <Row label="Copper regions" value={String(net.region_count)} />
          <Row label="Vias" value={String(net.via_count)} />
          <Row label="Copper area" value={formatMm2(net.copper_area_m2)} />
          <Row label="Terminals" value={String(net.terminal_count)} />
        </tbody>
      </table>
    </>
  );
}

function ViaDetails({ review, via }: { review: BoardReviewResponse; via: ViaResponse }) {
  const layerName = (id: string) => review.layers.find((layer) => layer.id === id)?.name ?? id;
  const netName = via.net_id
    ? (review.nets.find((net) => net.id === via.net_id)?.name ?? via.net_id)
    : "(unassigned)";
  return (
    <>
      <div className="panel__header">Via</div>
      <table className="property-table">
        <tbody>
          <Row label="ID" value={via.id} />
          <Row label="Net" value={netName} />
          <Row label="X" value={formatMm(via.x_m)} />
          <Row label="Y" value={formatMm(via.y_m)} />
          <Row
            label="Span"
            value={`${layerName(via.from_layer_id)} → ${layerName(via.to_layer_id)} (${via.span_kind})`}
          />
          <QuantityRow label="Drill" quantity={via.drill_diameter} millimetres />
          <QuantityRow label="Finished hole" quantity={via.finished_hole_diameter} millimetres />
          <QuantityRow label="Plating" quantity={via.plating_thickness} />
          <Row label="Padstack" value={via.padstack_name ?? "—"} />
        </tbody>
      </table>
    </>
  );
}

function LayerDetails({ layer }: { layer: LayerResponse }) {
  return (
    <>
      <div className="panel__header">Layer</div>
      <table className="property-table">
        <tbody>
          <Row label="Name" value={layer.name} />
          <Row label="Function" value={layer.function.replace(/_/g, " ")} />
          <Row label="Stackup index" value={String(layer.index)} />
          <QuantityRow label="Thickness" quantity={layer.thickness} />
          <QuantityRow label="Z top" quantity={layer.z_top} millimetres />
          <QuantityRow label="Z bottom" quantity={layer.z_bottom} millimetres />
          <Row label="Material" value={layer.material_name ?? "—"} />
        </tbody>
      </table>
    </>
  );
}

function RegionDetails({
  review,
  regionId,
  layerId,
}: {
  review: BoardReviewResponse;
  regionId: string;
  layerId: string;
}) {
  const { state } = useBoardState();
  const layers = state.geometry[state.view]?.layers ?? [];
  const region = layers
    .flatMap((layer) => layer.regions)
    .find((candidate) => candidate.id === regionId);
  const layerName = review.layers.find((layer) => layer.id === layerId)?.name ?? layerId;
  const netName = region?.net_id
    ? (review.nets.find((net) => net.id === region.net_id)?.name ?? region.net_id)
    : "(unassigned)";
  return (
    <>
      <div className="panel__header">Copper region</div>
      <table className="property-table">
        <tbody>
          <Row label="ID" value={regionId} />
          <Row label="Layer" value={layerName} />
          <Row label="Net" value={netName} />
          <Row label="Geometry" value={state.view === "normalized" ? "normalized" : "imported"} />
          {region ? <Row label="Vertices" value={String(region.exterior.length)} /> : null}
          {region ? <Row label="Holes" value={String(region.holes.length)} /> : null}
        </tbody>
      </table>
      {region && region.source_refs.length > 0 ? (
        <>
          <div className="panel__header">Source features · {region.source_refs.length}</div>
          <div className="panel__body">
            {region.source_refs.slice(0, 12).map((ref) => (
              <div key={ref} className="numeric" style={{ fontSize: "var(--font-size-small)" }}>
                {ref}
              </div>
            ))}
            {region.source_refs.length > 12 ? (
              <p className="empty-state">…and {region.source_refs.length - 12} more.</p>
            ) : null}
          </div>
        </>
      ) : null}
    </>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <tr>
      <th scope="row">{label}</th>
      <td className="value numeric">{value}</td>
    </tr>
  );
}

function QuantityRow({
  label,
  quantity,
  millimetres = false,
}: {
  label: string;
  quantity: QuantityResponse | null;
  millimetres?: boolean;
}) {
  return (
    <tr>
      <th scope="row">{label}</th>
      <td className="value">
        {quantity ? (
          <Thickness quantity={quantity} millimetres={millimetres} />
        ) : (
          <span title="Not present in the imported source">Unknown</span>
        )}
      </td>
    </tr>
  );
}

function Thickness({
  quantity,
  millimetres = false,
}: {
  quantity: QuantityResponse;
  millimetres?: boolean;
}) {
  if (quantity.unit !== "m") {
    return (
      <QuantityValue
        value={quantity.value}
        unit={quantity.unit}
        provenance={quantity.provenance}
        note={quantity.note}
      />
    );
  }
  const text = millimetres ? formatMm(quantity.value) : formatUm(quantity.value);
  return (
    <span>
      <span className="numeric">{text}</span>{" "}
      <ProvenanceBadge provenance={quantity.provenance} note={quantity.note} />
    </span>
  );
}
