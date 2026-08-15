/**
 * Layer visibility/solo/opacity controls and the searchable net list.
 *
 * Layers appear in physical stackup order, never alphabetically; the swatch
 * colour matches the viewport exactly (both read the same CSS custom
 * properties). Selecting a net highlights its copper in the viewport, dims
 * the rest, and drives the inspector -- the shared selection state is what
 * keeps all three in step.
 */

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { LayerResponse, ViaResponse } from "../api/types";
import { isLayerVisible, isViaSpanVisible, useBoardState, viaSpanKey } from "../state/boardState";

const LAYER_COLOR_SLOTS = 6;

/** Two overlapping translucent circles: the conventional opacity/blend glyph. */
function OpacityIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <circle cx="4.5" cy="6" r="3.5" fill="currentColor" opacity="0.55" />
      <circle cx="7.5" cy="6" r="3.5" fill="currentColor" opacity="0.55" />
    </svg>
  );
}

/** One via-stack column: every via connecting the same pair of layers. */
interface ViaSpanClass {
  key: string;
  fromLayerId: string;
  toLayerId: string;
  /** Row indices (into the conductive-layer list) the barrel spans, inclusive. */
  topRow: number;
  bottomRow: number;
  count: number;
}

/** Group vias by the layer pair they connect, for the stack-column display. */
function buildViaSpanClasses(vias: ViaResponse[], conductive: LayerResponse[]): ViaSpanClass[] {
  const rowOf = new Map(conductive.map((layer, index) => [layer.id, index]));
  const byKey = new Map<string, ViaSpanClass>();
  for (const via of vias) {
    const fromRow = rowOf.get(via.from_layer_id);
    const toRow = rowOf.get(via.to_layer_id);
    if (fromRow === undefined || toRow === undefined) {
      continue; // Touches a non-conductive or unknown layer; nothing to draw a stack for.
    }
    const key = viaSpanKey(via.from_layer_id, via.to_layer_id);
    const existing = byKey.get(key);
    if (existing) {
      existing.count += 1;
      continue;
    }
    byKey.set(key, {
      key,
      fromLayerId: via.from_layer_id,
      toLayerId: via.to_layer_id,
      topRow: Math.min(fromRow, toRow),
      bottomRow: Math.max(fromRow, toRow),
      count: 1,
    });
  }
  // Through-vias (widest span) lead, then by where they start -- a stable,
  // meaningful column order rather than source-data order.
  return [...byKey.values()].sort((a, b) => {
    const extentDiff = b.bottomRow - b.topRow - (a.bottomRow - a.topRow);
    return extentDiff !== 0 ? extentDiff : a.topRow - b.topRow;
  });
}

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

/** Percent readout that opens a popover slider to edit the layer's opacity. */
function OpacityCell({
  layer,
  opacity,
  open,
  onToggle,
  onChange,
}: {
  layer: LayerResponse;
  opacity: number;
  open: boolean;
  onToggle: () => void;
  onChange: (opacity: number) => void;
}) {
  const percent = Math.round(opacity * 100);
  const sliderRef = useRef<HTMLInputElement | null>(null);

  // Move focus into the slider when the popover opens, per the disclosure
  // pattern -- the trigger is a click, not page load, so this isn't the
  // autofocus-on-mount anti-pattern the a11y lint otherwise flags.
  useEffect(() => {
    if (open) {
      sliderRef.current?.focus();
    }
  }, [open]);

  return (
    <div className="opacity-cell">
      <button
        type="button"
        className="opacity-cell__value"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={`${layer.name} opacity, ${percent} percent`}
        onClick={onToggle}
      >
        {percent}%
      </button>
      {open ? (
        <div className="opacity-popover">
          <input
            ref={sliderRef}
            type="range"
            min={0.1}
            max={1}
            step={0.05}
            value={opacity}
            aria-label={`${layer.name} opacity`}
            onChange={(event) => onChange(Number(event.target.value))}
          />
          <span className="opacity-popover__value">{percent}%</span>
        </div>
      ) : null}
    </div>
  );
}

function LayerRows() {
  const { state, dispatch } = useBoardState();
  const review = state.review;
  const conductive = useMemo(
    () => (review ? review.layers.filter((layer) => layer.is_conductive) : []),
    [review],
  );
  const spanClasses = useMemo(
    () => (review ? buildViaSpanClasses(review.vias, conductive) : []),
    [review, conductive],
  );
  const [openOpacityLayerId, setOpenOpacityLayerId] = useState<string | null>(null);

  // Close the popover on an outside click or Escape; only listens while open.
  useEffect(() => {
    if (openOpacityLayerId === null) {
      return;
    }
    const onPointerDown = (event: PointerEvent) => {
      if (!(event.target instanceof Element) || !event.target.closest(".opacity-cell")) {
        setOpenOpacityLayerId(null);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpenOpacityLayerId(null);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [openOpacityLayerId]);

  if (review === null) {
    return null;
  }
  const nameByLayerId = new Map(conductive.map((layer) => [layer.id, layer.name]));
  const viaColumnCount = spanClasses.length;
  const opacityColumn = viaColumnCount + 2;

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
        {viaColumnCount > 0 ? (
          <button
            type="button"
            className="button button--ghost"
            onClick={() => dispatch({ type: "all-via-spans-shown" })}
          >
            All vias
          </button>
        ) : null}
      </div>
      <div
        className="panel__body layers-grid"
        style={{
          gridTemplateColumns: `repeat(${viaColumnCount}, var(--via-stack-col-width)) 1fr var(--opacity-col-width)`,
        }}
      >
        <div
          className="opacity-icon-cell"
          style={{ gridColumn: opacityColumn, gridRow: 1 }}
          title="Layer opacity"
        >
          <OpacityIcon />
        </div>
        {spanClasses.map((span, columnIndex) => {
          const fromName = nameByLayerId.get(span.fromLayerId) ?? span.fromLayerId;
          const toName = nameByLayerId.get(span.toLayerId) ?? span.toLayerId;
          const visible = isViaSpanVisible(state, span.key);
          return (
            <div
              key={span.key}
              className="via-stack__toggle-cell"
              style={{ gridColumn: columnIndex + 1, gridRow: 1 }}
            >
              <input
                type="checkbox"
                checked={visible}
                aria-label={`Show vias ${fromName} to ${toName}`}
                title={`Vias ${fromName} – ${toName} (${span.count})`}
                onChange={() =>
                  dispatch({ type: "via-span-visibility-toggled", spanKey: span.key })
                }
              />
            </div>
          );
        })}

        {conductive.map((layer, rowIndex) => {
          const visible = isLayerVisible(state, layer.id);
          const solo = state.soloLayerId === layer.id;
          return (
            <Fragment key={layer.id}>
              {spanClasses.map((span, columnIndex) => {
                const filled = rowIndex >= span.topRow && rowIndex <= span.bottomRow;
                const classes = ["via-stack__cell"];
                if (filled) {
                  classes.push("via-stack__cell--filled");
                  if (rowIndex === span.topRow) classes.push("via-stack__cell--top");
                  if (rowIndex === span.bottomRow) classes.push("via-stack__cell--bottom");
                }
                return (
                  <div
                    key={span.key}
                    className={classes.join(" ")}
                    style={{ gridColumn: columnIndex + 1, gridRow: rowIndex + 2 }}
                    aria-hidden="true"
                  />
                );
              })}
              <div
                className={`layer-row${visible ? "" : " layer-row--inactive"}`}
                style={{ gridColumn: viaColumnCount + 1, gridRow: rowIndex + 2 }}
              >
                <span
                  className="layer-row__swatch"
                  style={{ background: `var(--layer-color-${rowIndex % LAYER_COLOR_SLOTS})` }}
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
              </div>
              <div style={{ gridColumn: opacityColumn, gridRow: rowIndex + 2 }}>
                <OpacityCell
                  layer={layer}
                  opacity={state.layerOpacity[layer.id] ?? 1}
                  open={openOpacityLayerId === layer.id}
                  onToggle={() =>
                    setOpenOpacityLayerId(openOpacityLayerId === layer.id ? null : layer.id)
                  }
                  onChange={(opacity) =>
                    dispatch({ type: "layer-opacity-set", layerId: layer.id, opacity })
                  }
                />
              </div>
            </Fragment>
          );
        })}
      </div>
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
