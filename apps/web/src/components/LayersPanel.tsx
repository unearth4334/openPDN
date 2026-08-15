/**
 * Layer visibility/solo/opacity controls and the searchable net list.
 *
 * Layers appear in physical stackup order, never alphabetically; the swatch
 * colour matches the viewport exactly (both read the same CSS custom
 * properties). Selecting a net highlights its copper in the viewport, dims
 * the rest, and drives the inspector -- the shared selection state is what
 * keeps all three in step.
 */

import { useMemo, useState } from "react";
import { isLayerVisible, useBoardState } from "../state/boardState";

const LAYER_COLOR_SLOTS = 6;

export function LayersPanel() {
  const { state } = useBoardState();

  if (state.phase.status !== "ready" || state.review === null) {
    return (
      <aside className="panel" aria-label="Layers and nets">
        <div className="panel__header">Layers</div>
        <p className="empty-state">No board loaded. Open an IPC-2581 file to review its layers.</p>
        <div className="panel__header">Nets</div>
        <p className="empty-state">No nets.</p>
      </aside>
    );
  }
  return (
    <aside className="panel" aria-label="Layers and nets">
      <LayerRows />
      <NetList />
    </aside>
  );
}

function LayerRows() {
  const { state, dispatch } = useBoardState();
  const review = state.review;
  if (review === null) {
    return null;
  }
  const conductive = review.layers.filter((layer) => layer.is_conductive);

  return (
    <>
      <div className="panel__header">Layers</div>
      <div className="panel__actions">
        <button
          type="button"
          className="button button--ghost"
          onClick={() => dispatch({ type: "all-layers-shown" })}
        >
          All layers
        </button>
      </div>
      <ul className="panel__body net-list" aria-label="Conductive layers">
        {conductive.map((layer, index) => {
          const visible = isLayerVisible(state, layer.id);
          const solo = state.soloLayerId === layer.id;
          return (
            <li key={layer.id} className={`layer-row${visible ? "" : " layer-row--inactive"}`}>
              <span
                className="layer-row__swatch"
                style={{ background: `var(--layer-color-${index % LAYER_COLOR_SLOTS})` }}
                aria-hidden="true"
              />
              <input
                type="checkbox"
                checked={visible}
                disabled={state.soloLayerId !== null && !solo}
                aria-label={`Show ${layer.name}`}
                onChange={() => dispatch({ type: "layer-visibility-toggled", layerId: layer.id })}
              />
              <button
                type="button"
                className="layer-row__name"
                style={{
                  background: "none",
                  border: "none",
                  color: "inherit",
                  font: "inherit",
                  padding: 0,
                  textAlign: "left",
                }}
                title={`${layer.name} (${layer.function})`}
                onClick={() =>
                  dispatch({ type: "selected", selection: { kind: "layer", layerId: layer.id } })
                }
              >
                {layer.name}
              </button>
              <button
                type="button"
                className="layer-row__solo"
                aria-pressed={solo}
                aria-label={`Solo ${layer.name}`}
                title="Show only this layer"
                onClick={() => dispatch({ type: "layer-solo-toggled", layerId: layer.id })}
              >
                S
              </button>
              <input
                className="layer-row__opacity"
                type="range"
                min={0.1}
                max={1}
                step={0.05}
                value={state.layerOpacity[layer.id] ?? 1}
                aria-label={`${layer.name} opacity`}
                onChange={(event) =>
                  dispatch({
                    type: "layer-opacity-set",
                    layerId: layer.id,
                    opacity: Number(event.target.value),
                  })
                }
              />
            </li>
          );
        })}
      </ul>
    </>
  );
}

function NetList() {
  const { state, dispatch } = useBoardState();
  const [query, setQuery] = useState("");
  const review = state.review;

  const nets = useMemo(() => {
    if (review === null) {
      return [];
    }
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return review.nets;
    }
    return review.nets.filter((net) => net.name.toLowerCase().includes(needle));
  }, [review, query]);

  if (review === null) {
    return null;
  }
  const selectedNet = state.selection?.kind === "net" ? state.selection.netId : null;

  return (
    <>
      <div className="panel__header">Nets · {review.nets.length}</div>
      <div className="panel__body">
        <input
          className="net-search"
          type="search"
          placeholder="Search nets…"
          aria-label="Search nets"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <ul className="net-list">
          {nets.map((net) => (
            <li key={net.id}>
              <button
                type="button"
                className="net-row"
                aria-pressed={selectedNet === net.id}
                onClick={() =>
                  dispatch({
                    type: "selected",
                    selection: selectedNet === net.id ? null : { kind: "net", netId: net.id },
                  })
                }
              >
                <span className="net-row__name">{net.name}</span>
                <span
                  className="net-row__meta"
                  title={`${net.layer_ids.length} layers · ${net.via_count} vias`}
                >
                  {net.layer_ids.length}L {net.via_count}V
                </span>
              </button>
            </li>
          ))}
        </ul>
        {nets.length === 0 ? <p className="empty-state">No nets match.</p> : null}
      </div>
    </>
  );
}
